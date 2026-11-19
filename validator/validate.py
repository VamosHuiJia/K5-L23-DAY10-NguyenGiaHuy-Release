#!/usr/bin/env python3
"""Validator for metadata and panoptic rules."""

from __future__ import annotations

import argparse
import csv
import json
import sys
import zlib
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import pngutil


def image_stem(file_name: str) -> str:
    return Path(file_name).stem


def check_scene_leaks(metadata_path: Path) -> list[dict]:
    if not metadata_path.is_file():
        return []

    document = json.loads(metadata_path.read_text(encoding="utf-8"))
    scene_splits: dict[str, set[str]] = defaultdict(set)
    for item in document.get("items", []):
        scene_id = item.get("scene_id")
        split = item.get("split")
        if scene_id and split:
            scene_splits[str(scene_id)].add(str(split))

    return [
        {"type": "scene_leak", "target": scene_id}
        for scene_id, splits in sorted(scene_splits.items())
        if len(splits) > 1
    ]


def check_guideline_mismatches(delivery: Path) -> list[dict]:
    metadata_path = delivery / "metadata.json"
    jobs_path = delivery / "jobs.csv"
    if not metadata_path.is_file() or not jobs_path.is_file():
        return []

    with jobs_path.open(encoding="utf-8", newline="") as handle:
        job_guidelines = {
            row["job_id"].strip(): row["guideline"].strip()
            for row in csv.DictReader(handle)
        }

    document = json.loads(metadata_path.read_text(encoding="utf-8"))
    findings = []
    seen_targets = set()
    for item in document.get("items", []):
        image = str(item.get("image", "")).strip()
        job_id = str(item.get("job_id", "")).strip()
        if not image or job_id not in job_guidelines:
            continue
        image_guideline = str(item.get("guideline_version", "")).strip()
        if image_guideline != job_guidelines[job_id]:
            target = image_stem(image)
            if target not in seen_targets:
                findings.append({"type": "guideline_mismatch", "target": target})
                seen_targets.add(target)
    return sorted(findings, key=lambda item: item["target"])


def load_panoptic(delivery: Path) -> dict | None:
    panoptic_path = delivery / "panoptic.json"
    if not panoptic_path.is_file():
        return None
    return json.loads(panoptic_path.read_text(encoding="utf-8"))


def check_invalid_category_ids(delivery: Path, panoptic: dict) -> list[dict]:
    valid_ids = {int(category["id"]) for category in panoptic.get("categories", [])}
    findings = []
    seen_targets = set()

    for annotation in panoptic.get("annotations", []):
        target = image_stem(str(annotation.get("file_name", "")))
        if not target:
            continue
        for segment in annotation.get("segments_info", []):
            try:
                category_id = int(segment["category_id"])
            except (KeyError, TypeError, ValueError):
                if target not in seen_targets:
                    findings.append({"type": "invalid_category_id", "target": target})
                    seen_targets.add(target)
                continue
            if category_id not in valid_ids and target not in seen_targets:
                findings.append({"type": "invalid_category_id", "target": target})
                seen_targets.add(target)

    return sorted(findings, key=lambda item: item["target"])


def get_mask_path(delivery: Path, manifest: dict | None, target: str, annotation: dict) -> Path | None:
    if manifest:
        item = manifest.get("items", {}).get(target)
        if item and item.get("mask"):
            mask_path = delivery / str(item["mask"])
            if mask_path.is_file():
                return mask_path

    mask_name = str(annotation.get("file_name", "")).strip()
    if mask_name:
        mask_path = delivery / "masks" / mask_name
        if mask_path.is_file():
            return mask_path
    return None


def check_thing_overlaps(delivery: Path, panoptic: dict) -> list[dict]:
    manifest_path = delivery / "manifest.json"
    manifest = None
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    thing_category_ids = {
        int(category["id"])
        for category in panoptic.get("categories", [])
        if int(category.get("isthing", 0)) == 1
    }
    findings = []

    for annotation in panoptic.get("annotations", []):
        target = image_stem(str(annotation.get("file_name", "")))
        if not target:
            continue
        mask_path = get_mask_path(delivery, manifest, target, annotation)
        if mask_path is None:
            continue

        try:
            width, height, pixels = pngutil.read_rgb(mask_path)
        except (OSError, ValueError, zlib.error):
            continue

        if width * height * 3 != len(pixels):
            continue

        pixel_counts: dict[int, int] = defaultdict(int)
        for index in range(0, len(pixels), 3):
            segment_id = pngutil.segment_id(pixels, index)
            if segment_id != 0:
                pixel_counts[segment_id] += 1

        for segment in annotation.get("segments_info", []):
            try:
                category_id = int(segment["category_id"])
                segment_id = int(segment["id"])
                declared_area = int(segment["area"])
            except (KeyError, TypeError, ValueError):
                continue
            if category_id not in thing_category_ids:
                continue
            if segment.get("iscrowd", 0):
                continue
            if declared_area > pixel_counts.get(segment_id, 0):
                findings.append({"type": "thing_overlap", "target": target})
                break

    targets = sorted({finding["target"] for finding in findings})
    return [{"type": "thing_overlap", "target": target} for target in targets]


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate delivery metadata and panoptic rules")
    parser.add_argument("delivery", type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--candidates", type=Path)
    args = parser.parse_args()

    delivery = args.delivery
    if not delivery.is_dir():
        parser.error(f"delivery không tồn tại: {delivery}")

    findings = []
    findings.extend(check_scene_leaks(delivery / "metadata.json"))
    findings.extend(check_guideline_mismatches(delivery))

    panoptic = load_panoptic(delivery)
    if panoptic is not None:
        findings.extend(check_invalid_category_ids(delivery, panoptic))
        findings.extend(check_thing_overlaps(delivery, panoptic))

    unique = sorted(
        {(finding["type"], finding["target"]) for finding in findings},
        key=lambda pair: (pair[0], pair[1]),
    )
    output = {"errors": [{"type": error_type, "target": target} for error_type, target in unique]}

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(output, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    if args.candidates:
        args.candidates.parent.mkdir(parents=True, exist_ok=True)
        args.candidates.write_text(json.dumps({"candidates": []}, indent=2) + "\n", encoding="utf-8")

    print(f"Đã ghi {len(output['errors'])} lỗi metadata/panoptic vào {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
