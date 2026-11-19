#!/usr/bin/env python3
"""Validator for file, detection, metadata and panoptic rules."""

from __future__ import annotations

import argparse
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


# Mask pixel measurements and annotation review candidates.
def count_segment_pixels(pixels: bytes | bytearray) -> dict[int, int]:
    """Count RGB mask pixels per segment, excluding background ID 0."""
    if len(pixels) % 3:
        raise ValueError("RGB mask buffer length must be divisible by 3")

    counts: dict[int, int] = defaultdict(int)
    for index in range(0, len(pixels), 3):
        segment_id = pngutil.segment_id(pixels, index)
        if segment_id != 0:
            counts[segment_id] += 1
    return dict(counts)


def count_segment_pixels_in_box(
    pixels: bytes | bytearray,
    image_width: int,
    image_height: int,
    segment_id: int,
    box_xyxy: tuple[float, float, float, float],
) -> int:
    """Count a segment's pixel centers inside a pixel-coordinate XYXY box.

    The box is (left, top, right, bottom), not a normalized YOLO box.
    Left/top are inclusive and right/bottom are exclusive. Coordinates outside
    the image are clipped for counting only; the supplied box is not modified.
    Empty boxes and background ID 0 contribute no segment pixels.
    """
    if image_width <= 0 or image_height <= 0:
        raise ValueError("Mask width and height must be positive")
    if len(pixels) != image_width * image_height * 3:
        raise ValueError("RGB mask buffer does not match image dimensions")
    if not 0 <= segment_id <= 0xFFFFFF:
        raise ValueError("Segment ID must fit in a 24-bit RGB value")
    if len(box_xyxy) != 4:
        raise ValueError("Box must contain left, top, right and bottom")

    left, top, right, bottom = (float(value) for value in box_xyxy)
    if not all(math.isfinite(value) for value in (left, top, right, bottom)):
        raise ValueError("Box coordinates must be finite")
    if segment_id == 0 or right <= left or bottom <= top:
        return 0

    x_start = max(0, min(image_width, math.ceil(left - 0.5)))
    x_stop = max(0, min(image_width, math.ceil(right - 0.5)))
    y_start = max(0, min(image_height, math.ceil(top - 0.5)))
    y_stop = max(0, min(image_height, math.ceil(bottom - 0.5)))

    count = 0
    for y in range(y_start, y_stop):
        row_offset = y * image_width * 3
        for x in range(x_start, x_stop):
            if pngutil.segment_id(pixels, row_offset + x * 3) == segment_id:
                count += 1
    return count


def calculate_segment_coverage(segment_pixels: int, pixels_inside_box: int) -> float:
    """Return the fraction of a segment covered by a detection box.

    Use the actual mask pixel count as the denominator, not segments_info.area.
    An absent segment returns 0.0 and must be skipped by candidate detection.
    This measurement alone does not confirm an annotation error.
    """
    if segment_pixels < 0 or pixels_inside_box < 0:
        raise ValueError("Pixel counts cannot be negative")
    if pixels_inside_box > segment_pixels:
        raise ValueError("Inside pixel count cannot exceed segment pixel count")
    if segment_pixels == 0:
        return 0.0
    return pixels_inside_box / segment_pixels


def find_bbox_drift_candidate(
    image: str,
    pixels: bytes | bytearray,
    image_width: int,
    image_height: int,
    segment_id: int,
    box_xyxy: tuple[float, float, float, float],
    *,
    coverage_threshold: float,
    min_segment_pixels: int,
    box_index: int | None = None,
    iscrowd: bool = False,
) -> dict | None:
    """Suggest bbox_drift for one already matched, same-class box/segment pair.

    Matching and class-name mapping belong to the caller. Supply an RGB mask
    from pngutil.read_rgb() and a box in pixel XYXY coordinates. Thresholds
    must be chosen and checked on the clean lot; no seed-specific defaults
    are assumed here. A returned candidate still requires human image review.
    """
    if not math.isfinite(coverage_threshold) or not 0 < coverage_threshold <= 1:
        raise ValueError("Coverage threshold must be greater than 0 and at most 1")
    if min_segment_pixels < 1:
        raise ValueError("Minimum segment size must be at least one pixel")
    if len(box_xyxy) != 4:
        raise ValueError("Box must contain left, top, right and bottom")

    left, top, right, bottom = (float(value) for value in box_xyxy)
    if not all(math.isfinite(value) for value in (left, top, right, bottom)):
        raise ValueError("Box coordinates must be finite")
    if iscrowd or segment_id == 0 or right <= left or bottom <= top:
        return None

    pixels_inside = count_segment_pixels_in_box(
        pixels, image_width, image_height, segment_id, box_xyxy,
    )
    total_pixels = count_segment_pixels(pixels).get(segment_id, 0)
    if total_pixels < min_segment_pixels:
        return None

    coverage = calculate_segment_coverage(total_pixels, pixels_inside)
    if coverage >= coverage_threshold:
        return None

    candidate = {
        "image": Path(image).stem,
        "type": "bbox_drift",
        "segment_id": segment_id,
        "box_xyxy": [left, top, right, bottom],
        "segment_pixels": total_pixels,
        "pixels_inside_box": pixels_inside,
        "coverage": coverage,
        "coverage_threshold": coverage_threshold,
    }
    if box_index is not None:
        candidate["box_index"] = box_index
    return candidate


def find_instance_merge_candidate(
    image: str,
    pixels: bytes | bytearray,
    image_width: int,
    image_height: int,
    segment_id: int,
    first_box_xyxy: tuple[float, float, float, float],
    second_box_xyxy: tuple[float, float, float, float],
    *,
    min_segment_fraction: float,
    min_combined_coverage: float,
    max_box_iou: float,
    min_segment_pixels: int,
    box_indices: tuple[int, int] | None = None,
    iscrowd: bool = False,
) -> dict | None:
    """Suggest instance_merge for one segment and two same-class detections.

    The caller must check that both boxes and the thing segment have the same
    class name. Each box must contain its own substantial, non-overlapping
    share of the segment. Intersection pixels are counted once in the union,
    so repeated or nested boxes do not create artificial merge evidence.
    Thresholds need clean-lot calibration; candidates require human review.
    """
    if not math.isfinite(min_segment_fraction) or not 0 < min_segment_fraction <= 0.5:
        raise ValueError("Minimum segment fraction must be greater than 0 and at most 0.5")
    if not math.isfinite(min_combined_coverage) or not 0 < min_combined_coverage <= 1:
        raise ValueError("Combined coverage must be greater than 0 and at most 1")
    if not math.isfinite(max_box_iou) or not 0 <= max_box_iou < 1:
        raise ValueError("Maximum box IoU must be at least 0 and less than 1")
    if min_segment_pixels < 1:
        raise ValueError("Minimum segment size must be at least one pixel")
    if len(first_box_xyxy) != 4 or len(second_box_xyxy) != 4:
        raise ValueError("Each box must contain left, top, right and bottom")
    if box_indices is not None:
        if len(box_indices) != 2:
            raise ValueError("Two detection box indices are required")
        if box_indices[0] == box_indices[1]:
            return None

    first = tuple(float(value) for value in first_box_xyxy)
    second = tuple(float(value) for value in second_box_xyxy)
    if not all(math.isfinite(value) for value in first + second):
        raise ValueError("Box coordinates must be finite")
    if iscrowd or segment_id == 0:
        return None
    if first[2] <= first[0] or first[3] <= first[1]:
        return None
    if second[2] <= second[0] or second[3] <= second[1]:
        return None

    intersection = (
        max(first[0], second[0]), max(first[1], second[1]),
        min(first[2], second[2]), min(first[3], second[3]),
    )
    intersection_area = max(0.0, intersection[2] - intersection[0]) * max(
        0.0, intersection[3] - intersection[1],
    )
    first_area = (first[2] - first[0]) * (first[3] - first[1])
    second_area = (second[2] - second[0]) * (second[3] - second[1])
    box_iou = intersection_area / (first_area + second_area - intersection_area)
    if box_iou > max_box_iou:
        return None

    first_pixels = count_segment_pixels_in_box(
        pixels, image_width, image_height, segment_id, first,
    )
    second_pixels = count_segment_pixels_in_box(
        pixels, image_width, image_height, segment_id, second,
    )
    shared_pixels = count_segment_pixels_in_box(
        pixels, image_width, image_height, segment_id, intersection,
    )
    total_pixels = count_segment_pixels(pixels).get(segment_id, 0)
    if total_pixels < min_segment_pixels:
        return None

    first_fraction = calculate_segment_coverage(total_pixels, first_pixels - shared_pixels)
    second_fraction = calculate_segment_coverage(total_pixels, second_pixels - shared_pixels)
    combined_coverage = calculate_segment_coverage(
        total_pixels, first_pixels + second_pixels - shared_pixels,
    )
    if min(first_fraction, second_fraction) < min_segment_fraction:
        return None
    if combined_coverage < min_combined_coverage:
        return None

    candidate = {
        "image": Path(image).stem,
        "type": "instance_merge",
        "segment_id": segment_id,
        "boxes_xyxy": [list(first), list(second)],
        "segment_pixels": total_pixels,
        "pixels_inside_boxes": [first_pixels, second_pixels],
        "shared_pixels": shared_pixels,
        "exclusive_segment_fractions": [first_fraction, second_fraction],
        "combined_coverage": combined_coverage,
        "box_iou": box_iou,
        "min_segment_fraction": min_segment_fraction,
        "min_combined_coverage": min_combined_coverage,
        "max_box_iou": max_box_iou,
    }
    if box_indices is not None:
        candidate["box_indices"] = list(box_indices)
    return candidate


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


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate delivery rule checks")
    parser.add_argument("delivery", type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--candidates", type=Path)
    args = parser.parse_args()

    delivery = args.delivery
    if not delivery.is_dir():
        parser.error(f"delivery không tồn tại: {delivery}")

    findings = []
    findings.extend(check_file_detection(delivery))
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

    print(f"Đã ghi {len(output['errors'])} lỗi luật vào {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
