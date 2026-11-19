"""Automation tests use handcrafted files and a loopback server, no lab verdicts."""
import copy
import json
import threading
from types import SimpleNamespace
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import patch

import test_phase2 as fixtures
from tools import phase2 as auto
from validator import cross_check as cc


class AutomationTest(unittest.TestCase):
    setUp = fixtures.Phase2Test.setUp
    label = fixtures.Phase2Test.label
    write = fixtures.Phase2Test.write
    sync = fixtures.Phase2Test.sync
    generate = fixtures.Phase2Test.generate
    progress = fixtures.Phase2Test.progress

    def prepared(self):
        self.label("1 .3125 .3125 .3125 .3125\n")
        self.run = self.root
        auto.prepare(self.run)
        self.document = cc.read_json(self.run / "candidates.json")
        return self.document

    def test_prepare_cache_and_delivery_mutation_guard(self):
        self.prepared()
        candidate_bytes = (self.run / "candidates.json").read_bytes()
        with patch.object(auto, "run_script", side_effect=AssertionError("cache should avoid rerun")):
            self.assertEqual(auto.ensure_candidates(self.run), self.document)
        self.assertEqual((self.run / "candidates.json").read_bytes(), candidate_bytes)
        self.label("0 .3125 .3125 .3125 .3125\n")
        with self.assertRaisesRegex(ValueError, "Delivery bytes changed"):
            auto.ensure_candidates(self.run)

    def test_triage_quick_class_conflict_edge_and_specialist(self):
        candidate = {"id": "x:missing_box", "image": "x", "type": "missing_box", "image_available": True,
                     "evidence": [{"segment": {"mask_pixels": 2400, "bbox_xyxy": [20,20,80,80], "class_name": "truck"}, "nearest_boxes": []}]}
        document = {"release_id": "synthetic", "candidates": [candidate]}
        def assess():
            return auto.triage(document, {"errors": []}, {"x": (100,100)})["assessments"][0]
        quick = assess()
        self.assertEqual(quick["lane"], "quick")
        self.assertFalse(quick["machine_proposal"]["human_reviewed"])
        self.assertNotIn("verdict", quick["machine_proposal"])
        candidate["type"] = "class_swap"
        self.assertEqual(assess()["lane"], "hard")
        candidate["type"] = "missing_box"
        candidate["evidence"][0]["segment"]["bbox_xyxy"][0] = 1
        self.assertIn("object_at_image_edge", assess()["reasons"])
        candidate["evidence"][0]["segment"]["bbox_xyxy"][0] = 20
        candidate["evidence"][0]["nearest_boxes"] = [{"detection": {"class_name": "truck"}, "mask_coverage": .5, "bbox_iou": .3}] * 2
        self.assertIn("possible_instance_merge", assess()["reasons"])
        self.assertEqual(assess()["recommended_owner"], "Đặng Quốc Thái")

    def test_no_automatic_human_rows_or_final_output(self):
        self.prepared()
        self.assertFalse((self.run / "review_log.csv").exists())
        self.assertFalse((self.run / "errors.json").exists())
        for name in ["phase2.html", "review_quick.html", "review_hard.html", "review_blocked.html"]:
            self.assertTrue((self.run/name).exists())
        with self.assertRaisesRegex(ValueError, "has not been reviewed"):
            auto.finalize(self.run, score=False)

    def test_triage_checks_all_pairs_beyond_reviewer_nearest_boxes(self):
        candidate = {"id": "x:missing_box", "image": "x", "type": "missing_box", "image_available": True,
                     "evidence": [{"segment": {"id": 1, "mask_pixels": 2400, "bbox_xyxy": [20,20,80,80], "class_name": "truck"}, "nearest_boxes": []}]}
        document = {"release_id": "synthetic", "candidates": [candidate]}
        comparisons = {"x": SimpleNamespace(pairs={1: [{"detection": {"class_name": "truck"}, "bbox_iou": .05, "mask_coverage": .9}]})}
        result = auto.triage(document, {"errors": []}, {"x": (100,100)}, comparisons)
        self.assertEqual(result["assessments"][0]["lane"], "hard")

    def test_finalization_requires_submission_and_is_idempotent(self):
        document = self.prepared()
        progress = self.progress(document)
        auto.write_json(self.run/"reviews/hard.json", progress)
        with self.assertRaisesRegex(ValueError, "still a draft"):
            auto.finalize(self.run, score=False)
        progress["submitted"] = True
        auto.write_json(self.run/"reviews/hard.json", progress)
        result = auto.finalize(self.run, score=False)
        self.assertEqual(result["state"], "complete")
        self.assertEqual(cc.read_json(self.run/"errors.json")["errors"], [])
        with patch.object(auto, "run_script", side_effect=AssertionError("unchanged finalization must not rerun")):
            self.assertEqual(auto.finalize(self.run, score=False), result)

    def test_partial_scope_and_fingerprint_are_checked(self):
        document = self.prepared()
        assessment = cc.read_json(self.run/"auto_assessments.json")
        progress = self.progress(document)
        auto.validate_partial(document, assessment, "hard", progress)
        bad = copy.deepcopy(progress); bad["fingerprint"] = "old"
        with self.assertRaises(ValueError):
            auto.validate_partial(document, assessment, "hard", bad)
        with self.assertRaises(ValueError):
            auto.validate_partial(document, assessment, "quick", progress)

    def test_explicit_completed_file_bypasses_ui_flags_but_preserves_human_evidence(self):
        document = self.prepared()
        progress = self.progress(document)
        progress["submitted"] = False
        decision = next(iter(progress["decisions"].values()))
        decision["rows"][0]["note"] = ""
        decision["rows"][1].update(verdict="", note="")
        path = self.run / "review_progress_personal.json"
        auto.write_json(path, progress)
        original = path.read_bytes()
        result = auto.finalize(self.run, score=False, reviewed_progress=[path])
        self.assertEqual(result["state"], "complete")
        self.assertEqual(result["rejected"], 1)
        self.assertEqual(result["confirmed"], 0)
        self.assertEqual(len(result["documentation_gaps"]), 1)
        self.assertFalse(result["phase2_team_complete"])
        self.assertEqual(path.read_bytes(), original)

    def test_loopback_autosave_restore_conflict_and_finalization(self):
        document = self.prepared()
        server = auto.ReviewServer(self.run, 0, score=False)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        self.addCleanup(server.server_close)
        self.addCleanup(worker.join, 2)
        self.addCleanup(server.shutdown)
        origin = f"http://127.0.0.1:{server.server_port}"
        def request(route, body=None, token=None, origin_header=None):
            headers = {}
            if body is not None:
                headers = {"Content-Type": "application/json", "Origin": origin if origin_header is None else origin_header,
                           "X-Review-Token": server.token if token is None else token}
            req = Request(origin+route, data=json.dumps(body).encode() if body is not None else None, headers=headers)
            with urlopen(req, timeout=5) as response:
                return response.read().decode("utf-8")
        self.assertIn('"persistence"', request("/review_hard.html"))
        self.assertEqual(json.loads(request("/api/progress/hard")), {"progress": None})
        draft = self.progress(document)
        draft["submitted"] = False
        result = json.loads(request("/api/progress/hard", draft))
        self.assertEqual(result["revision"], 1)
        self.assertFalse((self.run/"review_log.csv").exists())
        restored = json.loads(request("/api/progress/hard"))["progress"]
        self.assertEqual(restored["decisions"], draft["decisions"])
        with self.assertRaises(HTTPError) as conflict:
            request("/api/progress/hard", draft)
        self.assertEqual(conflict.exception.code, 409)
        with self.assertRaises(HTTPError) as forbidden:
            request("/api/progress/hard", restored, token="wrong")
        self.assertEqual(forbidden.exception.code, 403)
        with self.assertRaises(HTTPError) as forbidden:
            request("/api/progress/hard", restored, origin_header="https://unrelated.example")
        self.assertEqual(forbidden.exception.code, 403)
        with self.assertRaises(HTTPError) as missing:
            request("/../delivery/manifest.json")
        self.assertEqual(missing.exception.code, 404)
        restored["submitted"] = True
        result = json.loads(request("/api/progress/hard", restored))
        self.assertEqual(result["status"]["state"], "complete")
        self.assertTrue((self.run/"review_log.csv").exists())


if __name__ == "__main__":
    unittest.main()
