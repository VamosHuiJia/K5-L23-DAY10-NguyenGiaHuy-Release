"""Student Phase 2 tests using only handcrafted deliveries, never private truth."""
import copy
import csv
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from validator import cross_check as cc
from tools import review_phase2 as review


class Phase2Test(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.delivery = self.root / "delivery"
        for folder in ["images", "labels", "masks"]:
            (self.delivery / folder).mkdir(parents=True, exist_ok=True)
        self.image = "img_0001"
        self.manifest = {"items": {self.image: {"image": "images/img_0001.png", "label": "labels/img_0001.txt", "mask": "masks/img_0001.png"}}}
        self.panoptic = {"images": [{"id": 723, "file_name": "img_0001.png", "width": 64, "height": 64}],
                         "categories": [{"id": 90, "name": "truck", "isthing": 1}, {"id": 91, "name": "car", "isthing": 1}, {"id": 92, "name": "wall", "isthing": 0}],
                         "annotations": [{"image_id": 723, "file_name": "img_0001.png", "segments_info": [{"id": 523, "category_id": 90, "iscrowd": 0, "area": 400, "bbox": [10, 10, 20, 20]}]}]}
        (self.delivery / "classes.txt").write_text("0 truck\n1 car\n", encoding="utf-8")
        self.label("0 0.3125 0.3125 0.3125 0.3125\n")
        pixels = bytearray(64 * 64 * 3)
        for y in range(10, 30):
            for x in range(10, 30):
                offset = (y*64+x)*3
                pixels[offset:offset+3] = bytes(cc.pngutil.segment_rgb(523))
        cc.pngutil.write_rgb(self.delivery / "masks/img_0001.png", 64, 64, pixels)
        cc.pngutil.write_rgb(self.delivery / "images/img_0001.png", 64, 64, pixels)
        for key, hash_key in [("image", "image_sha256"), ("label", "label_sha256"), ("mask", "mask_sha256")]:
            item = self.manifest["items"][self.image]
            item[hash_key] = hashlib.sha256((self.delivery/item[key]).read_bytes()).hexdigest()
        self.write("manifest.json", self.manifest)
        self.write("release.json", {"release_id": "fixture-release"})
        self.write("metadata.json", {"items": [{"image": self.image, "scene_id": "s", "split": "train", "job_id": "j", "guideline_version": "v1"}]})
        (self.delivery/"jobs.csv").write_text("job_id,annotator,reviewer,guideline\nj,a,r,v1\n", encoding="utf-8")
        self.sync()

    def write(self, name, value):
        (self.delivery / name).write_text(json.dumps(value), encoding="utf-8")

    def sync(self):
        self.write("panoptic.json", self.panoptic)

    def label(self, value):
        (self.delivery / "labels/img_0001.txt").write_text(value, encoding="utf-8")

    def generate(self):
        self.sync()
        return cc.generate_candidates(self.delivery)

    def progress(self, document):
        decisions = {}
        for c in document["candidates"]:
            decisions[c["id"]] = {"reviewed": True, "rows": [
                {"type": t, "verdict": "rejected", "reviewer": "Nguyễn Hải Đăng",
                 "note": f'Fixture only, {t}: ghi chú có dấu, dấu phẩy và "ngoặc kép"\nxuống dòng.'}
                for t in c["possible_types"]]}
        return {"schema_version": 1, "release_id": document["release_id"], "fingerprint": cc.fingerprint(document),
                "scope": list(decisions), "decisions": decisions}

    def progress_file(self, progress, name="progress.json"):
        path = self.root / name
        path.write_text(json.dumps(progress, ensure_ascii=False), encoding="utf-8")
        return path

    def test_correct_match_and_distinct_numeric_ids(self):
        self.assertEqual(self.generate()["candidates"], [])
        reader = cc.DeliveryReader(self.delivery)
        comparison = reader.compare(self.image)
        self.assertEqual(comparison.boxes[0]["bbox_xyxy"], [10, 10, 30, 30])
        self.assertEqual(comparison.pairs[523][0]["mask_coverage"], 1)

    def test_both_class_disagreement_directions_need_review(self):
        self.label("1 .3125 .3125 .3125 .3125\n")
        candidate = self.generate()["candidates"][0]
        self.assertEqual(candidate["possible_types"], ["class_swap", "category_swap"])
        self.assertEqual(candidate["evidence"][0]["detection"]["class_name"], "car")
        self.label("0 .3125 .3125 .3125 .3125\n")
        self.panoptic["annotations"][0]["segments_info"][0]["category_id"] = 91
        self.assertEqual(self.generate()["candidates"][0]["type"], "class_swap")

    def test_missing_box_and_no_boxes(self):
        self.label("")
        candidate = self.generate()["candidates"][0]
        self.assertEqual(candidate["type"], "missing_box")
        self.assertIsNone(candidate["evidence"][0]["detection"])

    def test_crowd_stuff_small_and_unknown_segments(self):
        self.label("")
        s = self.panoptic["annotations"][0]["segments_info"][0]
        for update in [{"iscrowd": 1}, {"category_id": 92}, {"category_id": 999}, {"bbox": [10, 10, 7, 20]}]:
            original = copy.deepcopy(s)
            s.update(update)
            self.assertEqual(self.generate()["candidates"], [])
            s.clear(); s.update(original)
        self.assertEqual(cc.propose(cc.DeliveryReader(self.delivery).compare(self.image), cc.Thresholds(min_segment_pixels=500)), [])

    def test_mask_match_when_iou_is_weaker(self):
        self.label("0 .3125 .3125 .625 .625\n")
        self.assertEqual(self.generate()["candidates"], [])

    def test_weak_overlap_remains_candidate_with_nearby_evidence(self):
        self.label("0 .546875 .3125 .3125 .3125\n")
        candidate = self.generate()["candidates"][0]
        self.assertEqual(candidate["type"], "missing_box")
        self.assertTrue(candidate["evidence"][0]["nearest_boxes"])

    def test_same_class_match_prevents_false_conflict_and_keeps_all_pairs(self):
        self.label("1 .3125 .3125 .3125 .3125\n0 .3125 .3125 .3125 .3125\n")
        self.assertEqual(self.generate()["candidates"], [])
        self.assertEqual(len(cc.DeliveryReader(self.delivery).compare(self.image).pairs[523]), 2)

    def test_deterministic_read_only_and_missing_image(self):
        before = {p: p.read_bytes() for p in self.delivery.rglob("*") if p.is_file()}
        a = self.generate()
        self.assertEqual(a, self.generate())
        self.assertTrue(all(p.read_bytes() == data for p, data in before.items()))
        self.label("")
        (self.delivery / "images/img_0001.png").unlink()
        document = self.generate()
        self.assertFalse(document["candidates"][0]["image_available"])
        progress = self.progress_file(self.progress(document))
        with self.assertRaisesRegex(ValueError, "unavailable"):
            review.merge_progress(self.delivery, document, [progress])

    def test_malformed_label_and_mask_have_diagnostics(self):
        self.label("nan 0 0 1 1\n999 0 0 1 1\n")
        self.assertEqual(len(self.generate()["diagnostics"]), 2)
        (self.delivery / "masks/img_0001.png").write_bytes(b"not png")
        document = self.generate()
        self.assertFalse(document["candidates"])
        self.assertTrue(any("skipped" in d["reason"] for d in document["diagnostics"]))

    def test_paths_cannot_escape_and_outputs_cannot_enter_delivery(self):
        with self.assertRaises(ValueError):
            cc.delivery_path(self.delivery, "../outside")
        with self.assertRaises(ValueError):
            cc.output_path(self.delivery, self.delivery / "images/../out.json")

    def test_cli_rules_unchanged_with_candidate_option(self):
        output = self.root / "rules.json"
        baseline = subprocess.run([sys.executable, "-B", "-X", "utf8", str(ROOT/"validator/validate.py"), str(self.delivery), "--out", str(output)], capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(baseline.returncode, 0, baseline.stderr)
        original = output.read_bytes()
        candidates = self.root / "candidates.json"
        result = subprocess.run([sys.executable, "-B", "-X", "utf8", str(ROOT/"validator/validate.py"), str(self.delivery), "--out", str(output), "--candidates", str(candidates)], capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(output.read_bytes(), original)
        self.assertEqual(json.loads(candidates.read_text())["candidates"], [])

    def test_viewer_embeds_original_and_mask_and_handles_empty_queue(self):
        self.label("")
        document = self.generate()
        payload = review.build_payload(self.delivery, document)
        self.assertTrue(payload["assets"][self.image]["image_data"].startswith("data:image/png;base64,"))
        self.assertTrue(payload["assets"][self.image]["segments"][0]["path"].startswith("M10 10h20"))
        self.assertFalse(payload.get("decisions"))
        empty = copy.deepcopy(document); empty["candidates"] = []
        self.assertEqual(review.build_payload(self.delivery, empty)["assets"], {})

    def test_progress_round_trip_csv_and_confirmed_only_merge(self):
        self.label("1 .3125 .3125 .3125 .3125\n")
        document = self.generate()
        progress = self.progress(document)
        decision = next(iter(progress["decisions"].values()))
        decision["rows"][1]["verdict"] = "confirmed"
        path = self.progress_file(progress)
        rows = review.merge_progress(self.delivery, document, [path])
        csv_path = self.root / "review.csv"
        review.write_csv(csv_path, rows)
        with csv_path.open(encoding="utf-8", newline="") as stream:
            restored = list(csv.DictReader(stream))
        self.assertEqual(restored, rows)
        self.assertEqual(restored[0]["image"], "img_0001")
        rules = self.root / "rules.json"; rules.write_text('{"errors": []}', encoding="utf-8")
        output = self.root / "errors.json"
        result = subprocess.run([sys.executable, "-X", "utf8", str(ROOT/"scripts/merge_review.py"), "--rules", str(rules), "--review", str(csv_path), "--out", str(output)], capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(output.read_text())["errors"], [{"type": "category_swap", "target": "img_0001"}])

    def test_progress_rejects_pending_wrong_fingerprint_short_notes_and_conflicts(self):
        self.label("")
        document = self.generate()
        good = self.progress(document)
        key = document["candidates"][0]["id"]
        for mutation in [lambda p: p.update(fingerprint="old"), lambda p: p.update(decisions={}),
                         lambda p: p["decisions"][key].update(reviewed=False),
                         lambda p: p["decisions"][key]["rows"][0].update(note="short"),
                         lambda p: p["decisions"][key]["rows"][0].update(reviewer="")]:
            bad = copy.deepcopy(good); mutation(bad)
            with self.assertRaises(ValueError):
                review.merge_progress(self.delivery, document, [self.progress_file(bad)])
        conflicting = copy.deepcopy(good)
        conflicting["decisions"][key]["rows"][0]["verdict"] = "confirmed"
        with self.assertRaisesRegex(ValueError, "Conflicting"):
            review.merge_progress(self.delivery, document, [self.progress_file(good), self.progress_file(conflicting, "other.json")])

    def test_actual_mask_counts_and_grouped_segments(self):
        self.label("")
        original = self.panoptic["annotations"][0]["segments_info"][0]
        original["area"] = 999999
        other = dict(original, id=524, bbox=[35, 35, 20, 20])
        self.panoptic["annotations"][0]["segments_info"].append(other)
        mask_path = self.delivery / "masks/img_0001.png"
        w, h, pixels = cc.pngutil.read_rgb(mask_path)
        for y in range(35, 55):
            for x in range(35, 55):
                offset = (y*w+x)*3
                pixels[offset:offset+3] = bytes(cc.pngutil.segment_rgb(524))
        cc.pngutil.write_rgb(mask_path, w, h, pixels)
        candidate = self.generate()["candidates"]
        self.assertEqual(len(candidate), 1)
        self.assertEqual(len(candidate[0]["evidence"]), 2)
        self.assertEqual([e["segment"]["mask_pixels"] for e in candidate[0]["evidence"]], [400, 400])

    def test_assignment_and_escaped_html_data(self):
        self.label("")
        document = self.generate()
        with self.assertRaisesRegex(ValueError, "no candidates"):
            review.build_payload(self.delivery, document, {"unknown"})
        payload = review.build_payload(self.delivery, document, {self.image})
        self.assertEqual(len(payload["candidates"]), 1)
        document["candidates"][0]["reason"] = "</script><img onerror=alert(1)>"
        candidates_path = self.root / "candidates.json"
        candidates_path.write_text(json.dumps(document), encoding="utf-8")
        output = self.root / "review.html"
        result = subprocess.run([sys.executable, "-X", "utf8", str(ROOT/"tools/review_phase2.py"), str(self.delivery), "--candidates", str(candidates_path), "--out", str(output)], capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stderr)
        page = output.read_text(encoding="utf-8")
        self.assertNotIn("</script><img onerror", page)
        self.assertIn("\\u003c/script>", page)
        self.assertNotIn("/*__REVIEW_DATA__*/", page)

    def test_cli_rejects_output_in_delivery_before_writing(self):
        output = self.delivery / "unsafe.json"
        result = subprocess.run([sys.executable, "-X", "utf8", str(ROOT/"validator/validate.py"), str(self.delivery), "--out", str(output)], capture_output=True, text=True, encoding="utf-8")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(output.exists())

    def test_conflicting_types_across_candidates_cannot_be_merged(self):
        self.label("1 .3125 .3125 .3125 .3125\n")
        document = self.generate()
        extra = copy.deepcopy(document["candidates"][0])
        extra.update(id=self.image+":missing_box", type="missing_box", possible_types=["missing_box"])
        document["candidates"].append(extra)
        progress = self.progress(document)
        decision = progress["decisions"][extra["id"]]
        decision["rows"].append({"type": "class_swap", "verdict": "confirmed", "reviewer": "Fixture reviewer",
                                 "note": "Synthetic conflicting observation, not a lab verdict"})
        with self.assertRaisesRegex(ValueError, "Conflicting judgments"):
            review.merge_progress(self.delivery, document, [self.progress_file(progress)])

    def test_bbox_rejection_does_not_require_optional_mask_verdict(self):
        self.label("1 .3125 .3125 .3125 .3125\n")
        candidate = self.generate()["candidates"][0]
        decision = self.progress({"candidates": [candidate], "release_id": "fixture-release"})["decisions"][candidate["id"]]
        decision["rows"][1].update(verdict="", note="")
        rows = review.validate_decision(candidate, decision)
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["type"], rows[0]["verdict"]), ("class_swap", "rejected"))
        decision["rows"] = decision["rows"][:1]
        self.assertEqual(review.validate_decision(candidate, decision), rows)

    def test_explicit_import_preserves_undocumented_rejection_without_inventing_observation(self):
        self.label("")
        candidate = self.generate()["candidates"][0]
        decision = {"reviewed": True, "rows": [{"type": "missing_box", "verdict": "rejected", "note": "", "reviewer": "Fixture reviewer"}]}
        with self.assertRaises(ValueError):
            review.validate_decision(candidate, decision)
        rows = review.validate_decision(candidate, decision, allow_undocumented_rejections=True)
        self.assertIn("Import metadata", rows[0]["note"])
        self.assertIn("không có ghi chú quan sát", rows[0]["note"])
        self.assertEqual(rows[0]["verdict"], "rejected")
        decision["rows"][0]["verdict"] = "confirmed"
        with self.assertRaises(ValueError):
            review.validate_decision(candidate, decision, allow_undocumented_rejections=True)

    def test_explicit_import_retains_decided_mask_rows_and_defers_unclassified_comments(self):
        self.label("1 .3125 .3125 .3125 .3125\n")
        candidate = self.generate()["candidates"][0]
        decision = self.progress({"candidates": [candidate], "release_id": "fixture-release"})["decisions"][candidate["id"]]
        decision["rows"][1].update(verdict="", note="Optional synthetic mask observation without a selected verdict")
        original = copy.deepcopy(decision)
        with self.assertRaises(ValueError):
            review.validate_decision(candidate, decision)
        rows = review.validate_decision(candidate, decision, allow_undocumented_rejections=True)
        self.assertEqual([r["type"] for r in rows], ["class_swap"])
        self.assertEqual(decision, original)
        decision["rows"][1]["verdict"] = "confirmed"
        rows = review.validate_decision(candidate, decision, allow_undocumented_rejections=True)
        self.assertEqual([r["type"] for r in rows], ["class_swap", "category_swap"])


if __name__ == "__main__":
    unittest.main()
