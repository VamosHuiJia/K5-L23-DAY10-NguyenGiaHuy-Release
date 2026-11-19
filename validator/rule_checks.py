"""The nine Phase 1 rule checks; only reads the supplied delivery."""
from __future__ import annotations

import csv
import hashlib
import json
import math
import sys
import zlib
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import pngutil


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _delivery_path(delivery: Path, relative_path: str) -> Path:
    """Resolve a delivery-relative path and refuse paths escaping delivery."""
    root = delivery.resolve()
    path = (root / relative_path).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"manifest path escapes delivery directory: {relative_path!r}") from exc
    return path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _image_sizes(delivery: Path) -> dict[str, tuple[int, int]]:
    """Read public image dimensions from panoptic.json for pixel tolerances."""
    document = _read_json(delivery / "panoptic.json")
    sizes: dict[str, tuple[int, int]] = {}
    for image in document.get("images", []):
        try:
            stem = Path(str(image["file_name"])).stem
            sizes[stem] = (int(image["width"]), int(image["height"]))
        except (KeyError, TypeError, ValueError):
            continue
    return sizes


def _parse_box(line: str) -> tuple[int, float, float, float, float] | None:
    fields = line.split()
    if len(fields) < 5:
        return None
    try:
        raw_class = float(fields[0])
        values = tuple(float(value) for value in fields[1:5])
    except ValueError:
        return None
    if not math.isfinite(raw_class) or not raw_class.is_integer():
        return None
    if not all(math.isfinite(value) for value in values):
        return None
    return int(raw_class), values[0], values[1], values[2], values[3]


def _has_duplicate_box(
    boxes: list[tuple[int, float, float, float, float]],
    image_size: tuple[int, int],
) -> bool:
    image_width, image_height = image_size
    pixel_boxes = [
        (class_id, x * image_width, y * image_height,
         width * image_width, height * image_height)
        for class_id, x, y, width, height in boxes
    ]

    for index, first in enumerate(pixel_boxes):
        class_a, x_a, y_a, width_a, height_a = first
        for class_b, x_b, y_b, width_b, height_b in pixel_boxes[index + 1 :]:
            if class_a != class_b:
                continue
            # The lab defines duplicates as same-class, same-size boxes whose
            # centers differ by only 1–2 image pixels. Keep this tighter than
            # an IoU threshold to avoid flagging ordinary overlapping objects.
            same_size = abs(width_a - width_b) <= 0.25 and abs(height_a - height_b) <= 0.25
            near_center = abs(x_a - x_b) <= 2.01 and abs(y_a - y_b) <= 2.01
            if same_size and near_center:
                return True
    return False


def check_file_detection(delivery: Path) -> list[dict[str, str]]:
    """Return findings for missing images, image hashes and YOLO labels."""
    delivery = delivery.resolve()
    manifest = _read_json(delivery / "manifest.json")
    items = manifest.get("items", {})
    image_sizes = _image_sizes(delivery)
    findings: list[dict[str, str]] = []

    for raw_target, item in items.items():
        target = str(raw_target)
        image_relative = str(item.get("image", ""))
        image_path = _delivery_path(delivery, image_relative) if image_relative else None

        if image_path is None or not image_path.is_file():
            findings.append({"type": "missing_image", "target": target})
        else:
            expected_hash = str(item.get("image_sha256", "")).strip().lower()
            if not expected_hash or _sha256(image_path) != expected_hash:
                findings.append({"type": "wrong_hash", "target": target})

        label_relative = str(item.get("label", ""))
        if not label_relative:
            continue
        label_path = _delivery_path(delivery, label_relative)
        if not label_path.is_file():
            continue

        boxes: list[tuple[int, float, float, float, float]] = []
        invalid_class = False
        bbox_out_of_bounds = False
        with label_path.open("r", encoding="utf-8") as stream:
            for line in stream:
                if not line.strip():
                    continue
                box = _parse_box(line)
                if box is None:
                    continue
                class_id, x_center, y_center, width, height = box
                boxes.append(box)

                if not 0 <= class_id < 80:
                    invalid_class = True

                left = x_center - width / 2
                top = y_center - height / 2
                right = x_center + width / 2
                bottom = y_center + height / 2
                # Allow up to 1% normalized overshoot, matching the lab's
                # handover checker tolerance for floating-point edge cases.
                tolerance = 0.01
                if (
                    left < -tolerance
                    or top < -tolerance
                    or right > 1 + tolerance
                    or bottom > 1 + tolerance
                ):
                    bbox_out_of_bounds = True

        if invalid_class:
            findings.append({"type": "invalid_class", "target": target})
        if bbox_out_of_bounds:
            findings.append({"type": "bbox_out_of_bounds", "target": target})
        if target in image_sizes and _has_duplicate_box(boxes, image_sizes[target]):
            findings.append({"type": "duplicate_box", "target": target})

    unique = {(finding["type"], finding["target"]): finding for finding in findings}
    return [unique[key] for key in sorted(unique)]


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
