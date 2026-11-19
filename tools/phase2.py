#!/usr/bin/env python3
"""Automate P2 preparation, triage, local autosave, and human-reviewed finalization.

Run: python tools/phase2.py serve --run runs/r6007
The loopback review app saves human work locally and finalizes only complete
human review. Geometry proposals never impersonate named reviewers.
"""
from __future__ import annotations

import argparse
import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from pathlib import Path
import secrets
import subprocess
import sys
import threading
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from validator import cross_check as cc
from tools import review_phase2 as review


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def tree_digest(delivery: Path) -> str:
    files = {p.relative_to(delivery).as_posix(): digest(p)
             for p in sorted(delivery.rglob("*")) if p.is_file()}
    return cc.fingerprint(files)


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def run_script(script: str, *args: str) -> None:
    subprocess.run([sys.executable, "-B", "-X", "utf8", str(ROOT / script), *map(str, args)],
                   cwd=ROOT, check=True)


def ensure_candidates(run: Path) -> dict:
    """Cache only when delivery, code, and generated file hashes still match."""
    run = run.resolve()
    delivery = run / "delivery"
    if not delivery.is_dir():
        raise ValueError(f"Delivery missing: {delivery}")
    fingerprint = tree_digest(delivery)
    state_path = run / "phase2_cache.json"
    old = cc.read_json(state_path) if state_path.exists() else {}
    if old and old.get("delivery") != fingerprint:
        raise ValueError("Delivery bytes changed since preparation; preserve v1 and investigate before continuing")
    code = cc.fingerprint({name: digest(ROOT / name) for name in
                          ["validator/validate.py", "validator/cross_check.py", "scripts/pngutil.py"]})
    rules, candidates = run / "rules.json", run / "candidates.json"
    if (old.get("code") == code and rules.is_file() and candidates.is_file()
            and old.get("rules") == digest(rules) and old.get("candidates") == digest(candidates)):
        return cc.read_json(candidates)
    run_script("validator/validate.py", delivery, "--out", rules, "--candidates", candidates)
    if tree_digest(delivery) != fingerprint:
        raise ValueError("Delivery changed during candidate generation")
    write_json(state_path, {"delivery": fingerprint, "code": code,
                           "rules": digest(rules), "candidates": digest(candidates)})
    return cc.read_json(candidates)


def triage(document: dict, rules: dict, sizes: dict, comparisons: dict | None = None) -> dict:
    """Route conservative geometry evidence; no machine human-review records."""
    rule_images = {e["target"] for e in rules["errors"]}
    records = []
    for candidate in document["candidates"]:
        reasons, suggestion = [], None
        lane, owner = "hard", "Nguyễn Hải Đăng"
        if candidate.get("image_available") is False:
            lane = "blocked"
            reasons.append("image_unavailable")
        elif comparisons is not None and comparisons.get(candidate["image"]) is None:
            lane = "blocked"
            reasons.append("comparison_inputs_unreadable")
        elif candidate["image"] in rule_images:
            reasons.append("existing_rule_fault_may_affect_comparison")
        elif candidate["type"] == "class_swap":
            reasons.append("image_needed_to_choose_detection_or_panoptic")
        elif candidate["type"] == "missing_box":
            width, height = sizes[candidate["image"]]
            strong_absence = True
            for observation in candidate["evidence"]:
                segment = observation["segment"]
                left, top, right, bottom = segment["bbox_xyxy"]
                # Use every delivered box for triage. The reviewer-facing top
                # three nearest boxes alone cannot establish absence.
                nearby = (comparisons[candidate["image"]].pairs[segment["id"]]
                          if comparisons is not None else observation["nearest_boxes"])
                same = [p for p in nearby if p["detection"]["class_name"] == segment["class_name"]
                        and p["mask_coverage"] >= 0.20]
                if len(same) >= 2 and sum(p["mask_coverage"] for p in same) >= 0.80:
                    reasons.append("possible_instance_merge")
                    owner = "Đặng Quốc Thái"
                elif same:
                    reasons.append("possible_bbox_drift_or_occlusion")
                    owner = "Đặng Quốc Thái"
                if min(left, top, width-right, height-bottom) <= 2:
                    reasons.append("object_at_image_edge")
                max_iou = max((p["bbox_iou"] for p in nearby), default=0)
                max_coverage = max((p["mask_coverage"] for p in nearby), default=0)
                if (segment["mask_pixels"] < 1024 or min(right-left, bottom-top) < 16
                        or max_iou >= 0.20 or max_coverage >= 0.10 or reasons):
                    strong_absence = False
            if strong_absence and candidate["evidence"]:
                lane = "quick"
                reasons.append("large_interior_segment_without_overlapping_detection")
                suggestion = {"type": "missing_box", "assessment": "likely_confirmed",
                              "source": "automated_geometry", "human_reviewed": False}
            elif not reasons:
                reasons.append("weak_or_ambiguous_missing_box_evidence")
        else:
            reasons.append("specialist_detector_requires_review")
            owner = "Đặng Quốc Thái"
        records.append({"id": candidate["id"], "image": candidate["image"], "lane": lane,
                        "recommended_owner": owner, "reasons": sorted(set(reasons)),
                        "machine_proposal": suggestion})
    return {"schema_version": 1, "release_id": document["release_id"],
            "fingerprint": cc.fingerprint(document), "assessments": records,
            "summary": {lane: sum(r["lane"] == lane for r in records) for lane in ["quick", "hard", "blocked"]},
            "diagnostics": document.get("diagnostics", [])}


def write_dashboard(run: Path, assessment: dict, clean: dict | None) -> None:
    from html import escape
    counts = assessment["summary"]
    rows = "".join(f"<tr><td>{escape(r['image'])}</td><td>{r['lane']}</td>"
                   f"<td>{escape(r['recommended_owner'])}</td><td>{escape(', '.join(r['reasons']))}</td></tr>"
                   for r in assessment["assessments"])
    calibration = (f"Lô sạch: {clean['summary']['candidates']} ứng viên." if clean else "Chưa có lô sạch tại đường dẫn chỉ định.")
    html = f"""<!doctype html><html lang="vi"><meta charset="utf-8"><title>Phase 2 · Hàng đợi</title>
<style>body{{font:16px/1.6 system-ui;max-width:1100px;margin:40px auto;padding:20px;background:#f1f5f8;color:#17293c}}a{{color:#006d77}}table{{border-collapse:collapse;width:100%;background:white}}td,th{{padding:12px;border:1px solid #ccd6dd;text-align:left}}.cards{{display:flex;gap:24px;flex-wrap:wrap;background:white;padding:20px;border-radius:10px}}</style>
<h1>Phase 2 · Hàng đợi cần xử lý</h1><p>Đã tự động sinh ứng viên, đối chiếu luật và phân luồng. Máy không ghi verdict dưới tên người xem.</p>
<div class="cards"><a href="review_hard.html">Ca khó: {counts['hard']}</a><a href="review_quick.html">Kiểm tra nhanh: {counts['quick']}</a><a href="review_blocked.html">Bị chặn: {counts['blocked']}</a></div>
<p>{calibration} Khi chạy bằng server local, kết quả được tự lưu vào repo và tự merge/chấm sau khi cả hàng đợi hoàn tất.</p>
<p>Nhập tên người xem, xem ảnh rồi điền verdict và note. Các đề xuất tự động chỉ là hỗ trợ; lab vẫn yêu cầu người thật xác nhận. Ca blocked phải xử lý nguyên nhân trước. Không sửa delivery v1.</p>
<p><a href="/api/status">Trạng thái tự lưu / hoàn tất hiện tại</a></p>
<table><tr><th>Ảnh</th><th>Luồng</th><th>Người phù hợp</th><th>Căn cứ máy</th></tr>{rows}</table></html>"""
    (run / "phase2.html").write_text(html, encoding="utf-8")


def prepare(run: Path, clean_run: Path | None = None) -> dict:
    document = ensure_candidates(run)
    clean = ensure_candidates(clean_run) if clean_run and (clean_run / "delivery").is_dir() else None
    reader = cc.DeliveryReader(run / "delivery")
    sizes = {name: (int(i["width"]), int(i["height"])) for name, i in reader.images.items()}
    comparisons = {image: reader.compare(image) for image in {c["image"] for c in document["candidates"]}}
    assessment = triage(document, cc.read_json(run / "rules.json"), sizes, comparisons)
    write_json(run / "auto_assessments.json", assessment)
    payload = review.build_payload(run / "delivery", document)
    payload["triage"] = {r["id"]: r for r in assessment["assessments"]}
    review.write_html(payload, run / "review.html")
    for lane in ["quick", "hard", "blocked"]:
        ids = {r["id"] for r in assessment["assessments"] if r["lane"] == lane}
        selected = [c for c in payload["candidates"] if c["id"] in ids]
        subset = dict(payload, candidates=selected,
                      assets={image: asset for image, asset in payload["assets"].items() if any(c["image"] == image for c in selected)})
        review.write_html(subset, run / f"review_{lane}.html")
    write_dashboard(run, assessment, clean)
    status = {"state": "needs_human_review" if document["candidates"] else "ready_to_finalize",
              "triage": assessment["summary"], "clean_candidates": clean["summary"]["candidates"] if clean else None}
    write_json(run / "phase2_status.json", status)
    return status


def finalize(run: Path, export: bool = False, score: bool = True, reviewed_progress: list[Path] | None = None) -> dict:
    document = cc.read_json(run / "candidates.json")
    cached = cc.read_json(run / "phase2_cache.json")
    if (tree_digest(run / "delivery") != cached["delivery"] or
            digest(run / "rules.json") != cached["rules"] or digest(run / "candidates.json") != cached["candidates"]):
        raise ValueError("Delivery or generated inputs changed; rerun preparation and investigate")
    assessment = cc.read_json(run / "auto_assessments.json")
    progress = list(reviewed_progress or [])
    for lane, count in (assessment["summary"].items() if not reviewed_progress else []):
        if not count:
            continue
        path = run / "reviews" / f"{lane}.json"
        if not path.exists():
            raise ValueError(f"Assignment {lane} has not been reviewed")
        part = cc.read_json(path)
        validate_partial(document, assessment, lane, part)
        if part.get("submitted") is not True:
            raise ValueError(f"Assignment {lane} is still a draft; click Hoàn tất phần này")
        progress.append(path)
    # Explicit CLI import is the user's confirmation of completion. Retain
    # reviewed/verdict evidence, but do not depend on old UI submitted/lanes.
    rows = review.merge_progress(run / "delivery", document, progress,
                                 allow_undocumented_rejections=bool(reviewed_progress))
    gaps, deferred = [], []
    for path in reviewed_progress or []:
        source = cc.read_json(path)
        for candidate_id, decision in source["decisions"].items():
            for row in decision["rows"]:
                if row.get("verdict") == "rejected" and not row.get("note", "").strip():
                    gaps.append({"candidate": candidate_id, "type": row["type"], "issue": "rejected_without_observation_note"})
                if not row.get("verdict") and row.get("note", "").strip():
                    deferred.append({"candidate": candidate_id, "type": row["type"],
                                     "note": row["note"], "reviewer": row.get("reviewer", ""),
                                     "issue": "optional_comment_without_verdict"})
    if reviewed_progress:
        write_json(run / "review_import.json", {
            "basis": "user_confirmed_review_complete", "ui_submitted_flag_ignored": True,
            "sources": [{"file": p.name, "sha256": digest(p)} for p in reviewed_progress],
            "documentation_gaps": gaps, "human_verdicts_changed": False,
            "optional_comments_without_verdict": deferred,
            "blank_optional_mask_rows_omitted": True})
    review_hash = cc.fingerprint({"rows": rows, "candidates": cc.fingerprint(document)})
    state_path = run / "phase2_status.json"
    old = cc.read_json(state_path) if state_path.exists() else {}
    final_files = [run / "review_log.csv", run / "errors.json"]
    if (old.get("review_hash") != review_hash or
            old.get("final_hashes") != {p.name: digest(p) for p in final_files if p.exists()}):
        review.write_csv(run / "review_log.csv", rows)
        run_script("scripts/merge_review.py", "--rules", run / "rules.json", "--review", run / "review_log.csv", "--out", run / "errors.json")
        old = {}
    if score and old.get("scored_hash") != review_hash:
        run_script("scripts/score_findings.py", "--run", run)
    state = {"state": "complete", "review_hash": review_hash, "scored_hash": review_hash if score else old.get("scored_hash"),
             "human_review_rows": len(rows), "confirmed": sum(r["verdict"] == "confirmed" for r in rows),
             "rejected": sum(r["verdict"] == "rejected" for r in rows),
             "scope": "Nguyễn Hải Đăng", "phase2_team_complete": False,
             "excluded_work": ["Đặng Quốc Thái: bbox_drift, instance_merge và tích hợp phần Thái"],
             "documentation_gaps": gaps,
             "optional_comments_without_verdict": deferred,
             "final_hashes": {p.name: digest(p) for p in final_files}}
    write_json(state_path, state)
    if export:
        run_script("scripts/export_submission.py", "--run", run)
    return state


def validate_partial(document: dict, assessment: dict, lane: str, progress: dict) -> None:
    expected = {r["id"] for r in assessment["assessments"] if r["lane"] == lane}
    if (not isinstance(progress, dict) or progress.get("schema_version") != 1 or
            progress.get("release_id") != document["release_id"] or progress.get("fingerprint") != cc.fingerprint(document)
            or not isinstance(progress.get("scope"), list) or sorted(progress["scope"]) != sorted(expected)):
        raise ValueError("Progress does not match this release, fingerprint, or assignment")
    decisions = progress.get("decisions")
    if not isinstance(decisions, dict) or not set(decisions) <= expected:
        raise ValueError("Invalid decisions or assignment")
    candidates = {c["id"]: c for c in document["candidates"]}
    for key, decision in decisions.items():
        if not isinstance(decision, dict) or not isinstance(decision.get("reviewed"), bool):
            raise ValueError("Invalid visual-review state")
        rows = decision.get("rows")
        if not isinstance(rows, list) or not rows or any(not isinstance(row, dict) for row in rows):
            raise ValueError("Invalid rows")
        kinds = [r.get("type") for r in rows]
        if len(kinds) != len(set(kinds)) or candidates[key]["type"] not in kinds:
            raise ValueError("Missing proposed type or duplicate types")
        for row in rows:
            if (row.get("type") not in review.SEMANTIC_TYPES or row.get("verdict") not in ["", "confirmed", "rejected"]
                    or not isinstance(row.get("note"), str) or not isinstance(row.get("reviewer"), str)):
                raise ValueError("Invalid review row")


class ReviewServer(HTTPServer):
    def __init__(self, run: Path, port: int = 8765, score: bool = True):
        super().__init__(("127.0.0.1", port), ReviewHandler)
        self.run_dir, self.score = run, score
        self.token = secrets.token_urlsafe(32)
        self.document = cc.read_json(run / "candidates.json")
        self.assessment = cc.read_json(run / "auto_assessments.json")
        self.lock = threading.Lock()


class ReviewHandler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    @property
    def origin(self):
        return f"http://127.0.0.1:{self.server.server_port}"

    def respond(self, status, value, mime="application/json; charset=utf-8"):
        data = json.dumps(value, ensure_ascii=False).encode("utf-8") if not isinstance(value, bytes) else value
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def valid_host(self):
        return self.headers.get("Host") == self.origin.removeprefix("http://")

    def do_GET(self):
        if not self.valid_host():
            return self.respond(403, {"error": "Use the exact loopback URL printed by the server"})
        route = urlsplit(self.path).path
        run = self.server.run_dir
        if route == "/api/status":
            return self.respond(200, cc.read_json(run / "phase2_status.json"))
        if route.startswith("/api/progress/"):
            lane = route.removeprefix("/api/progress/")
            if lane not in ["quick", "hard", "blocked"]:
                return self.respond(404, {"error": "Unknown assignment"})
            path = run / "reviews" / f"{lane}.json"
            if not path.exists():
                return self.respond(200, {"progress": None})
            progress = cc.read_json(path)
            try:
                validate_partial(self.server.document, self.server.assessment, lane, progress)
            except (ValueError, TypeError):
                return self.respond(409, {"error": "Saved progress is stale; retain it and resolve the assignment change"})
            return self.respond(200, {"progress": progress})
        name = "phase2.html" if route == "/" else route.removeprefix("/")
        if name not in ["phase2.html", "review_quick.html", "review_hard.html", "review_blocked.html"]:
            return self.respond(404, {"error": "Not found"})
        page = (run / name).read_text(encoding="utf-8")
        if name.startswith("review_"):
            lane = name.removeprefix("review_").removesuffix(".html")
            import re
            pattern = r'(<script id="reviewData" type="application/json">)(.*?)(</script>)'
            match = re.search(pattern, page, re.S)
            payload = json.loads(match[2])
            payload["persistence"] = {"endpoint": f"/api/progress/{lane}", "token": self.server.token}
            encoded = json.dumps(payload, ensure_ascii=False).replace("<", "\\u003c")
            page = page[:match.start(2)] + encoded + page[match.end(2):]
        return self.respond(200, page.encode("utf-8"), "text/html; charset=utf-8")

    def do_POST(self):
        if (not self.valid_host() or self.headers.get("Origin") != self.origin or
                self.headers.get("X-Review-Token") != self.server.token):
            return self.respond(403, {"error": "Same-origin review session required"})
        lane = urlsplit(self.path).path.removeprefix("/api/progress/")
        if lane not in ["quick", "hard", "blocked"]:
            return self.respond(404, {"error": "Unknown assignment"})
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 10_000_000:
                raise ValueError("Invalid progress size")
            progress = json.loads(self.rfile.read(length))
            validate_partial(self.server.document, self.server.assessment, lane, progress)
            run = self.server.run_dir
            with self.server.lock:
                destination = run / "reviews" / f"{lane}.json"
                # A second browser with an older revision cannot silently
                # overwrite more recent human work.
                prior = cc.read_json(destination) if destination.exists() else {}
                revision = prior.get("revision", 0)
                if progress.get("revision", 0) != revision:
                    return self.respond(409, {"error": "Newer saved work exists; export this tab and reload before reconciling"})
                progress["revision"] = revision + 1
                write_json(destination, progress)
                if progress.get("submitted") is True:
                    try:
                        state = finalize(run, score=self.server.score)
                    except ValueError as exc:
                        state = {"state": "needs_human_review", "detail": str(exc), "triage": self.server.assessment["summary"]}
                    except subprocess.CalledProcessError:
                        state = {"state": "finalization_failed", "detail": "A supplied script failed; inspect the server terminal"}
                else:
                    state = {"state": "needs_human_review", "detail": "Draft saved; explicit submission required", "triage": self.server.assessment["summary"]}
                write_json(run / "phase2_status.json", state)
            return self.respond(200, {"saved": True, "revision": revision + 1, "status": state})
        except (OSError, ValueError, TypeError, KeyError) as exc:
            return self.respond(400, {"error": str(exc)})


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["prepare", "serve", "finalize"])
    parser.add_argument("--run", type=Path, default=ROOT / "runs/r6007")
    parser.add_argument("--clean-run", type=Path, default=ROOT / "runs/clean")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--export", action="store_true", help="Export practice artifacts after complete human review")
    parser.add_argument("--review-progress", nargs="+", type=Path,
                        help="Import reviews the user confirms complete, independent of legacy UI submission state")
    args = parser.parse_args()
    run = args.run.resolve()
    try:
        if not run.is_relative_to(ROOT):
            raise ValueError("Run must be inside this repository")
        if args.action == "finalize":
            print(json.dumps(finalize(run, args.export, reviewed_progress=args.review_progress), ensure_ascii=False, indent=2))
        else:
            print(json.dumps(prepare(run, args.clean_run.resolve()), ensure_ascii=False, indent=2), flush=True)
            if args.action == "serve":
                with ReviewServer(run, args.port) as server:
                    print(f"Open http://127.0.0.1:{server.server_port}/ — Ctrl+C to stop", flush=True)
                    server.serve_forever()
    except KeyboardInterrupt:
        pass
    except (OSError, ValueError, KeyError, TypeError, subprocess.CalledProcessError) as exc:
        parser.exit(1, f"Phase 2: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
