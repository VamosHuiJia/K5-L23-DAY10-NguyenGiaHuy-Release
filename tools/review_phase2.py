#!/usr/bin/env python3
"""Build an offline review page, or validate/merge human progress exports.

No automatic verdicts. Generated outputs must stay outside the input delivery.
"""
from __future__ import annotations

import argparse
import base64
import csv
import io
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from validator import cross_check as cc

SEMANTIC_TYPES = {"class_swap", "category_swap", "missing_box", "bbox_drift", "instance_merge"}


def svg_runs(spans: list) -> str:
    return "".join(f"M{x0} {y}h{x1-x0}v1h{x0-x1}z" for y, x0, x1 in spans)


def build_payload(delivery: Path, document: dict, images: set[str] | None = None) -> dict:
    reader = cc.DeliveryReader(delivery)
    release_id = cc.read_json(delivery / "release.json")["release_id"]
    if document.get("release_id") != release_id:
        raise ValueError("Candidate release does not match delivery")
    all_candidates = document["candidates"]
    if len({c["id"] for c in all_candidates}) != len(all_candidates):
        raise ValueError("Duplicate candidate IDs")
    unknown = (images or set()) - {c["image"] for c in all_candidates}
    if unknown:
        raise ValueError(f"Assigned images have no candidates: {sorted(unknown)}")
    candidates = [c for c in all_candidates if images is None or c["image"] in images]
    assets = {}
    for image in sorted({c["image"] for c in candidates}):
        item = reader.manifest[image]
        comparison = reader.compare(image)
        asset = {"image_data": None, "width": 1, "height": 1, "boxes": [], "segments": [], "error": None}
        try:
            if comparison is None:
                raise ValueError("Không đọc được nhãn hoặc mask; cần xử lý trước khi review")
            if not comparison.image_available:
                raise ValueError("Thiếu ảnh gốc; không thể kết luận bằng mắt")
            path = cc.delivery_path(delivery, item["image"])
            mime = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
            asset.update(image_data=f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode("ascii"),
                         width=comparison.width, height=comparison.height, boxes=comparison.boxes)
            wanted = {e["segment"]["id"] for c in candidates if c["image"] == image for e in c["evidence"]}
            asset["segments"] = [dict(s, path=svg_runs(comparison.spans.get(s["id"], [])))
                                 for s in comparison.segments if s["id"] in wanted]
        except (OSError, ValueError) as exc:
            asset["error"] = str(exc)
        assets[image] = asset
    return {"release_id": release_id, "fingerprint": cc.fingerprint(document),
            "candidates": candidates, "assets": assets, "semantic_types": sorted(SEMANTIC_TYPES)}


def active_rows(candidate: dict, rows: list[dict]) -> list[dict]:
    """Legacy blank alternatives are UI placeholders, not human decisions."""
    return [row for row in rows if row.get("type") == candidate["type"]
            or row.get("explicit") is True or row.get("verdict")
            or (isinstance(row.get("note"), str) and row["note"].strip())]


def validate_decision(candidate: dict, decision: dict, allow_undocumented_rejections: bool = False) -> list[dict]:
    if not isinstance(decision, dict) or decision.get("reviewed") is not True:
        raise ValueError(f"Pending visual review: {candidate['id']}")
    rows = decision.get("rows", [])
    if not isinstance(rows, list) or not rows or any(not isinstance(r, dict) for r in rows):
        raise ValueError(f"Invalid review rows: {candidate['id']}")
    types = [r.get("type") for r in rows]
    if len(types) != len(set(types)) or candidate["type"] not in types:
        raise ValueError(f"Missing proposed type or duplicate types: {candidate['id']}")
    output = []
    for row in active_rows(candidate, rows):
        kind, verdict = row.get("type"), row.get("verdict")
        if allow_undocumented_rejections and kind != candidate["type"] and not verdict:
            # An optional comment is retained in the import audit, not promoted
            # into a verdict. The user's primary bbox decision remains usable.
            continue
        reviewer, note = row.get("reviewer", ""), row.get("note", "")
        if not isinstance(reviewer, str) or not isinstance(note, str):
            raise ValueError(f"Reviewer and note must be text: {candidate['id']}")
        reviewer, note = reviewer.strip(), note.strip()
        if allow_undocumented_rejections and verdict == "rejected" and not note:
            note = "[Import metadata: người review chọn rejected trong file gốc; không có ghi chú quan sát.]"
        if kind not in SEMANTIC_TYPES or verdict not in {"confirmed", "rejected"} or not reviewer or len(note) < 20:
            raise ValueError(f"Invalid type/verdict, missing reviewer, or note <20 characters: {candidate['id']}")
        output.append({"image": candidate["image"], "type": kind, "verdict": verdict,
                       "note": f"Reviewer: {reviewer}; {note}"})
    return output


def merge_progress(delivery: Path, document: dict, paths: list[Path], allow_undocumented_rejections: bool = False) -> list[dict]:
    release_id = cc.read_json(delivery / "release.json")["release_id"]
    if release_id != document.get("release_id"):
        raise ValueError("Candidate release does not match delivery")
    candidates = {c["id"]: c for c in document["candidates"]}
    if len(candidates) != len(document["candidates"]):
        raise ValueError("Duplicate candidate IDs")
    decisions = {}
    for path in paths:
        progress = cc.read_json(path)
        if (progress.get("schema_version") != 1 or progress.get("release_id") != release_id
                or progress.get("fingerprint") != cc.fingerprint(document)):
            raise ValueError(f"Incompatible progress export: {path.name}")
        scope = progress.get("scope", [])
        if not isinstance(scope, list) or len(scope) != len(set(scope)) or not set(scope) <= set(candidates):
            raise ValueError(f"Unknown/duplicate assignment: {path.name}")
        incoming = progress.get("decisions", {})
        if not isinstance(incoming, dict) or not set(incoming) <= set(scope):
            raise ValueError(f"Decisions outside assignment: {path.name}")
        for key, value in incoming.items():
            if key in decisions and decisions[key] != value:
                raise ValueError(f"Conflicting progress for {key}; resolve with reviewers")
            decisions[key] = value
    if set(decisions) != set(candidates):
        raise ValueError(f"Review incomplete: {len(set(candidates)-set(decisions))} candidates missing")
    manifest = cc.read_json(delivery / "manifest.json")["items"]
    combined = {}
    for key in sorted(candidates):
        candidate = candidates[key]
        if (candidate.get("image_available") is False or
                not cc.delivery_path(delivery, manifest[candidate["image"]]["image"]).is_file()):
            raise ValueError(f"Image unavailable for visual review: {candidate['image']}")
        for row in validate_decision(candidate, decisions[key], allow_undocumented_rejections):
            pair = row["image"], row["type"]
            if pair in combined:
                old = combined[pair]
                if old["verdict"] != row["verdict"]:
                    raise ValueError(f"Conflicting judgments for {pair}; resolve with reviewers")
                if row["note"] != old["note"]:
                    old["note"] += " | " + row["note"]
            else:
                combined[pair] = row
    result = [combined[key] for key in sorted(combined)]
    notes = [row["note"] for row in result]
    if notes and len(set(notes)) < 0.7 * len(notes):
        raise ValueError("Too many identical review notes; write image-specific observations")
    return result


def write_csv(path: Path, rows: list[dict]) -> None:
    # Validate everything before opening the destination, preserving an existing
    # review log if progress is incomplete or contradictory.
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=["image", "type", "verdict", "note"], lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        handle.write(stream.getvalue())


def write_html(payload: dict, output: Path) -> None:
    data = json.dumps(payload, ensure_ascii=False).replace("<", "\\u003c").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    template = Path(__file__).with_name("review_phase2.html").read_text(encoding="utf-8")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(template.replace("/*__REVIEW_DATA__*/", data), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("delivery", type=Path)
    parser.add_argument("--candidates", type=Path, required=True)
    parser.add_argument("--out", type=Path, help="Generated self-contained HTML")
    parser.add_argument("--images", nargs="+", help="Only these exact image stems (assignment)")
    parser.add_argument("--merge-progress", nargs="+", type=Path, help="Human progress JSON exports to validate and merge")
    parser.add_argument("--review-out", type=Path, help="Merged final review_log.csv")
    args = parser.parse_args()
    try:
        document = cc.read_json(args.candidates)
        if args.merge_progress:
            if not args.review_out or args.out or args.images:
                parser.error("Use --merge-progress with --review-out, without --out/--images")
            output = cc.output_path(args.delivery, args.review_out)
            if output in {args.candidates.resolve(), *(p.resolve() for p in args.merge_progress)}:
                raise ValueError("Review output must not overwrite candidates or progress exports")
            rows = merge_progress(args.delivery, document, args.merge_progress)
            write_csv(output, rows)
            print(f"Đã kiểm tra và ghi {len(rows)} dòng review: {output}")
        else:
            if not args.out or args.review_out:
                parser.error("Use --out for HTML, or --merge-progress with --review-out")
            output = cc.output_path(args.delivery, args.out)
            if output == args.candidates.resolve():
                raise ValueError("HTML output must not overwrite candidates")
            payload = build_payload(args.delivery, document, set(args.images) if args.images else None)
            # JSON is data, never HTML. Escape < to prevent a filename/note from
            # terminating the script element. Dynamic labels use textContent.
            write_html(payload, output)
            print(f"Đã tạo {len(payload['candidates'])} ứng viên, chưa có verdict: {output}")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.exit(1, f"Review error: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
