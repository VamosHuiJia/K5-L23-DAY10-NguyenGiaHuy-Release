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
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
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
    pixels: bytes | bytearray | None,
    image_width: int,
    image_height: int,
    segment_id: int,
    box_xyxy: tuple[float, float, float, float],
    *,
    coverage_threshold: float,
    min_segment_pixels: int,
    box_index: int | None = None,
    iscrowd: bool = False,
    mask_measurements: tuple[int, int] | None = None,
) -> dict | None:
    """Suggest bbox_drift for one already matched, same-class box/segment pair.

    Matching and class-name mapping belong to the caller. Supply an RGB mask
    from pngutil.read_rgb() and a box in pixel XYXY coordinates. Thresholds
    must be chosen and checked on the clean lot; no seed-specific defaults
    are assumed here. A returned candidate still requires human image review.
    mask_measurements may supply (total_pixels, inside_pixels) measured from
    mask spans by DeliveryReader, avoiding repeated full-mask decoding.
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

    if mask_measurements is None:
        if pixels is None:
            raise ValueError("Provide RGB mask pixels or measured mask counts")
        pixels_inside = count_segment_pixels_in_box(
            pixels, image_width, image_height, segment_id, box_xyxy,
        )
        total_pixels = count_segment_pixels(pixels).get(segment_id, 0)
    else:
        total_pixels, pixels_inside = mask_measurements
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
    pixels: bytes | bytearray | None,
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
    mask_measurements: tuple[int, int, int, int] | None = None,
) -> dict | None:
    """Suggest instance_merge for one segment and two same-class detections.

    The caller must check that both boxes and the thing segment have the same
    class name. Each box must contain its own substantial, non-overlapping
    share of the segment. Intersection pixels are counted once in the union,
    so repeated or nested boxes do not create artificial merge evidence.
    Thresholds need clean-lot calibration; candidates require human review.
    mask_measurements may supply (total, first, second, shared) pixel counts
    measured from mask spans, instead of decoding the same mask repeatedly.
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

    if mask_measurements is None:
        if pixels is None:
            raise ValueError("Provide RGB mask pixels or measured mask counts")
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
    else:
        total_pixels, first_pixels, second_pixels, shared_pixels = mask_measurements
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


# Detection/panoptic matching API integrated from Dang's commit e8de233.
@dataclass(frozen=True)
class Thresholds:
    match_iou: float = 0.50
    mask_coverage: float = 0.85
    min_area_ratio: float = 0.25
    max_area_ratio: float = 4.0
    min_segment_pixels: int = 128
    min_segment_area_ratio: float = 0.0005
    min_bbox_extent: int = 8


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def delivery_path(delivery: Path, relative: str) -> Path:
    root = delivery.resolve()
    result = (root / relative).resolve()
    if not result.is_relative_to(root):
        raise ValueError(f"Path escapes delivery: {relative!r}")
    return result


def output_path(delivery: Path, output: Path) -> Path:
    """Refuse to write generated files anywhere in the original delivery."""
    output = output.resolve()
    if output.is_relative_to(delivery.resolve()):
        raise ValueError("Output must be outside the read-only delivery")
    return output


def load_classes(delivery: Path) -> dict[int, str]:
    classes = {}
    for line in (delivery / "classes.txt").read_text(encoding="utf-8").splitlines():
        if line.strip():
            number, name = line.strip().split(maxsplit=1)
            classes[int(number)] = name.strip()
    return classes


def load_boxes(path: Path, width: int, height: int, classes: dict,
               diagnostics: list, image: str) -> list[dict]:
    boxes = []
    for index, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            fields = line.split()
            if len(fields) != 5:
                raise ValueError("expected five YOLO fields")
            raw_class, x, y, w, h = map(float, fields)
            if (not all(map(math.isfinite, (raw_class, x, y, w, h)))
                    or not raw_class.is_integer() or int(raw_class) not in classes
                    or w <= 0 or h <= 0):
                raise ValueError("invalid class or box geometry")
        except ValueError as exc:
            diagnostics.append({"image": image, "reason": f"label row {index}: {exc}"})
            continue
        boxes.append({"row": index, "class_id": int(raw_class),
                      "class_name": classes[int(raw_class)],
                      "bbox_xyxy": [(x-w/2)*width, (y-h/2)*height,
                                    (x+w/2)*width, (y+h/2)*height]})
    return boxes


def mask_spans(width: int, height: int, pixels: bytes,
               wanted: set[int]) -> dict[int, list[list[int]]]:
    """Runs [y, left, right-exclusive], usable for coverage and SVG overlays."""
    if len(pixels) != width * height * 3:
        raise ValueError("mask pixel buffer size does not match dimensions")
    runs = defaultdict(list)
    for y in range(height):
        previous, start = 0, 0
        for x in range(width):
            sid = pngutil.segment_id(pixels, (y * width + x) * 3)
            if sid != previous:
                if previous in wanted:
                    runs[previous].append([y, start, x])
                previous, start = sid, x
        if previous in wanted:
            runs[previous].append([y, start, width])
    return dict(runs)


def bbox_area(box: list) -> float:
    return max(0, box[2]-box[0]) * max(0, box[3]-box[1])


def bbox_iou(a: list, b: list) -> float:
    intersection = max(0, min(a[2], b[2])-max(a[0], b[0])) * max(0, min(a[3], b[3])-max(a[1], b[1]))
    union = bbox_area(a) + bbox_area(b) - intersection
    return intersection / union if union else 0.0


def coverage(spans: list, box: list, area: int) -> float:
    # Count pixel centers in a half-open rectangle; do not round box coordinates
    # differently between mask measurements and the displayed detection box.
    left, top, right, bottom = (math.ceil(v - 0.5) for v in box)
    inside = sum(max(0, min(x1, right)-max(x0, left))
                 for y, x0, x1 in spans if top <= y < bottom)
    return inside / area if area else 0.0


@dataclass
class Comparison:
    image: str
    width: int
    height: int
    boxes: list[dict]
    segments: list[dict]
    spans: dict[int, list]
    pairs: dict[int, list[dict]]
    image_available: bool


class DeliveryReader:
    def __init__(self, delivery: Path):
        self.delivery = delivery.resolve()
        self.manifest = read_json(delivery / "manifest.json")["items"]
        self.panoptic = read_json(delivery / "panoptic.json")
        self.classes = load_classes(delivery)
        self.categories = {int(c["id"]): c for c in self.panoptic["categories"]}
        self.images = {Path(i["file_name"]).stem: i for i in self.panoptic["images"]}
        self.annotations = {a["image_id"]: a for a in self.panoptic["annotations"]}
        self.diagnostics: list[dict] = []

    def compare(self, image: str, *, include_all_segments: bool = False) -> Comparison | None:
        """Pixel evidence; default keeps the existing candidate behavior.

        include_all_segments retains crowd and stuff metadata for integration.
        The caller must filter these before ordinary instance matching.
        """
        item = self.manifest[image]
        try:
            info = self.images[image]
            width, height = int(info["width"]), int(info["height"])
            if width <= 0 or height <= 0:
                raise ValueError("non-positive image dimensions")
            annotation = self.annotations[info["id"]]
            boxes = load_boxes(delivery_path(self.delivery, item["label"]), width, height,
                               self.classes, self.diagnostics, image)
            mask_width, mask_height, pixels = pngutil.read_rgb(delivery_path(self.delivery, item["mask"]))
            if (width, height) != (mask_width, mask_height):
                raise ValueError("image and mask dimensions disagree")
            segments = []
            for raw in annotation["segments_info"]:
                category = self.categories.get(raw.get("category_id"))
                if category is None:
                    self.diagnostics.append({"image": image, "reason": f"unknown category for segment {raw.get('id')}"})
                    continue
                if not include_all_segments and (not category.get("isthing") or raw.get("iscrowd", 0)):
                    continue
                try:
                    x, y, w, h = map(float, raw["bbox"])
                    sid = int(raw["id"])
                    if sid <= 0 or w <= 0 or h <= 0 or not all(map(math.isfinite, (x,y,w,h))):
                        raise ValueError("invalid segment geometry")
                except (ValueError, KeyError, TypeError):
                    self.diagnostics.append({"image": image, "reason": "invalid segment geometry"})
                    continue
                segments.append({"id": sid, "category_id": category["id"],
                                 "class_name": category["name"],
                                 "bbox_xyxy": [x, y, x+w, y+h]})
                if include_all_segments:
                    segments[-1].update(iscrowd=int(raw.get("iscrowd", 0)),
                                        isthing=int(category.get("isthing", 0)))
            spans = mask_spans(width, height, pixels, {s["id"] for s in segments})
            pairs = {}
            for segment in segments:
                runs = spans.get(segment["id"], [])
                area = sum(x1-x0 for _, x0, x1 in runs)
                segment["mask_pixels"] = area
                if not area:
                    self.diagnostics.append({"image": image, "reason": f"segment {segment['id']} has no mask pixels"})
                measurements = []
                for box in boxes:
                    ratio = bbox_area(box["bbox_xyxy"]) / bbox_area(segment["bbox_xyxy"])
                    measurements.append({"detection": box, "bbox_iou": bbox_iou(box["bbox_xyxy"], segment["bbox_xyxy"]),
                                         "mask_coverage": coverage(runs, box["bbox_xyxy"], area),
                                         "area_ratio": ratio})
                pairs[segment["id"]] = sorted(measurements, key=lambda p: (-p["bbox_iou"], -p["mask_coverage"], p["detection"]["row"]))
            available = delivery_path(self.delivery, item["image"]).is_file()
            if not available:
                self.diagnostics.append({"image": image, "reason": "image unavailable: human review blocked"})
            return Comparison(image, width, height, boxes, segments, spans, pairs, available)
        except (OSError, ValueError, KeyError, TypeError, zlib.error, IndexError) as exc:
            self.diagnostics.append({"image": image, "reason": f"comparison skipped: {exc}"})
            return None

    def match(self, image: str, config: Thresholds | None = None) -> dict | None:
        """Return a JSON-ready basic matching contract for drift/merge callers.

        ``pairs`` retains every box/segment measurement, including zero overlap.
        ``matches`` selects spatial matches to non-crowd things, independently
        of class, without imposing a one-to-one assignment. No fault verdicts.
        On unreadable inputs return None; inspect reader.diagnostics.
        """
        config = config or Thresholds()
        comparison = self.compare(image, include_all_segments=True)
        if comparison is None:
            return None
        segments = sorted(comparison.segments, key=lambda segment: segment["id"])
        pairs = []
        for segment in segments:
            eligible = segment["isthing"] == 1 and segment["iscrowd"] == 0 and segment["mask_pixels"] > 0
            for measurement in comparison.pairs[segment["id"]]:
                pairs.append({"box_row": measurement["detection"]["row"],
                              "segment_id": segment["id"],
                              "bbox_iou": measurement["bbox_iou"],
                              "mask_coverage": measurement["mask_coverage"],
                              "area_ratio": measurement["area_ratio"],
                              "same_class": measurement["detection"]["class_name"] == segment["class_name"],
                              "eligible_for_matching": eligible,
                              "matched": eligible and strong_match(measurement, config)})
        pairs.sort(key=lambda pair: (pair["box_row"], pair["segment_id"]))
        matches = [pair.copy() for pair in pairs if pair["matched"]]
        matched_boxes = {pair["box_row"] for pair in matches}
        matched_segments = {pair["segment_id"] for pair in matches}
        return {"schema_version": 1, "image": image,
                "file_name": Path(self.manifest[image]["image"]).name,
                "width": comparison.width, "height": comparison.height,
                "image_available": comparison.image_available,
                "matching_config": asdict(config),
                "boxes": comparison.boxes, "segments": segments,
                "pairs": pairs, "matches": matches,
                "unmatched_box_rows": [box["row"] for box in comparison.boxes if box["row"] not in matched_boxes],
                "unmatched_segment_ids": [segment["id"] for segment in segments
                                          if segment["isthing"] == 1 and segment["iscrowd"] == 0
                                          and segment["id"] not in matched_segments]}


def strong_match(pair: dict, config: Thresholds) -> bool:
    return (pair["bbox_iou"] >= config.match_iou or
            (pair["mask_coverage"] >= config.mask_coverage and
             config.min_area_ratio <= pair["area_ratio"] <= config.max_area_ratio))


def propose(comparison: Comparison, config: Thresholds) -> list[dict]:
    grouped: dict[str, list] = defaultdict(list)
    for segment in comparison.segments:
        if not segment["mask_pixels"]:
            continue
        pairs = comparison.pairs[segment["id"]]
        strong = [p for p in pairs if strong_match(p, config)]
        same = [p for p in strong if p["detection"]["class_name"] == segment["class_name"]]
        if same:
            continue
        if strong:
            kind, selected = "class_swap", strong[0]
        else:
            x0, y0, x1, y1 = segment["bbox_xyxy"]
            minimum = max(config.min_segment_pixels, config.min_segment_area_ratio * comparison.width * comparison.height)
            if segment["mask_pixels"] < minimum or min(x1-x0, y1-y0) < config.min_bbox_extent:
                continue
            kind, selected = "missing_box", pairs[0] if pairs else None
        grouped[kind].append({"segment": segment, "detection": selected["detection"] if selected else None,
                              "bbox_iou": selected["bbox_iou"] if selected else 0.0,
                              "mask_coverage": selected["mask_coverage"] if selected else 0.0,
                              "nearest_boxes": pairs[:3]})
    return [{"id": f"{comparison.image}:{kind}", "image": comparison.image, "type": kind,
             "possible_types": ["class_swap", "category_swap"] if kind == "class_swap" else [kind],
             "reason": "Strong spatial match but class names disagree; inspect both sources."
             if kind == "class_swap" else "Significant thing segment has no strong detection match; inspect nearby boxes.",
             "image_available": comparison.image_available,
             "evidence": sorted(evidence, key=lambda e: e["segment"]["id"])}
            for kind, evidence in sorted(grouped.items())]


@dataclass(frozen=True)
class MaskThresholds:
    """Starting thresholds; measure clean-lot noise before choosing final values."""
    drift_coverage: float = 0.90
    merge_min_fraction: float = 0.15
    merge_coverage: float = 0.85
    merge_max_iou: float = 0.30
    association_distance: float = 1.0

    def __post_init__(self):
        if not math.isfinite(self.drift_coverage) or not 0 < self.drift_coverage <= 1:
            raise ValueError("Drift coverage must be in (0, 1]")
        if not math.isfinite(self.merge_min_fraction) or not 0 < self.merge_min_fraction <= 0.5:
            raise ValueError("Merge minimum fraction must be in (0, 0.5]")
        if not math.isfinite(self.merge_coverage) or not 0 < self.merge_coverage <= 1:
            raise ValueError("Merge coverage must be in (0, 1]")
        if not math.isfinite(self.merge_max_iou) or not 0 <= self.merge_max_iou < 1:
            raise ValueError("Merge maximum IoU must be in [0, 1)")
        if not math.isfinite(self.association_distance) or self.association_distance <= 0:
            raise ValueError("Association distance must be positive")


def propose_mask_candidates(
    comparison: Comparison, config: Thresholds, mask_config: MaskThresholds,
) -> list[dict]:
    """Use all API pairs, including unmatched pairs, to suggest drift/merge.

    YOLO row numbers stay one-based. Mask spans retain the exact half-open
    pixel-center convention of the original matching API. No label is edited.
    """
    minimum = max(config.min_segment_pixels,
                  config.min_segment_area_ratio * comparison.width * comparison.height)
    segments = [segment for segment in comparison.segments
                if segment.get("isthing") == 1 and not segment.get("iscrowd")
                and segment["mask_pixels"] >= minimum
                and min(segment["bbox_xyxy"][2] - segment["bbox_xyxy"][0],
                        segment["bbox_xyxy"][3] - segment["bbox_xyxy"][1]) >= config.min_bbox_extent]
    grouped = defaultdict(list)
    box_pairs = defaultdict(list)
    merge_members = set()

    def center_distance(box, segment):
        a, b = box["bbox_xyxy"], segment["bbox_xyxy"]
        distance = math.hypot((a[0] + a[2] - b[0] - b[2]) / 2,
                              (a[1] + a[3] - b[1] - b[3]) / 2)
        scale = max(math.hypot(a[2] - a[0], a[3] - a[1]),
                    math.hypot(b[2] - b[0], b[3] - b[1]), 1.0)
        return distance / scale

    for segment in segments:
        sid, area = segment["id"], segment["mask_pixels"]
        pairs = comparison.pairs[sid]
        same_class = []
        for pair in pairs:
            box_pairs[pair["detection"]["row"]].append((segment, pair))
            if (pair["detection"]["class_name"] == segment["class_name"]
                    and pair["mask_coverage"] >= mask_config.merge_min_fraction):
                same_class.append(pair)

        for index, first in enumerate(same_class):
            for second in same_class[index + 1:]:
                first_box, second_box = first["detection"], second["detection"]
                a, b = first_box["bbox_xyxy"], second_box["bbox_xyxy"]
                intersection = (max(a[0], b[0]), max(a[1], b[1]),
                                min(a[2], b[2]), min(a[3], b[3]))
                shared = round(coverage(comparison.spans.get(sid, []), intersection, area) * area)
                candidate = find_instance_merge_candidate(
                    comparison.image, None, comparison.width, comparison.height,
                    sid, a, b,
                    min_segment_fraction=mask_config.merge_min_fraction,
                    min_combined_coverage=mask_config.merge_coverage,
                    max_box_iou=mask_config.merge_max_iou,
                    min_segment_pixels=math.ceil(minimum),
                    box_indices=(first_box["row"], second_box["row"]),
                    mask_measurements=(area, round(first["mask_coverage"] * area),
                                       round(second["mask_coverage"] * area), shared),
                )
                if candidate is None:
                    continue
                candidate["box_rows"] = candidate.pop("box_indices")
                grouped["instance_merge"].append({
                    "segment": segment, "detections": [first_box, second_box],
                    "measurements": candidate,
                })
                merge_members.update(((first_box["row"], sid), (second_box["row"], sid)))

    for row, measurements in sorted(box_pairs.items()):
        def rank(entry):
            segment, pair = entry
            return (strong_match(pair, config), pair["bbox_iou"], pair["mask_coverage"],
                    -center_distance(pair["detection"], segment))

        same_class = [(segment, pair) for segment, pair in measurements
                      if pair["detection"]["class_name"] == segment["class_name"]
                      and config.min_area_ratio <= pair["area_ratio"] <= config.max_area_ratio
                      and center_distance(pair["detection"], segment) <= mask_config.association_distance]
        if not same_class:
            continue
        segment, pair = max(same_class, key=rank)
        strongest_segment, strongest_pair = max(measurements, key=rank)
        if (strongest_pair["detection"]["class_name"] != strongest_segment["class_name"]
                and strong_match(strongest_pair, config) and not strong_match(pair, config)):
            continue  # Leave a strong class disagreement for the class-review queue.
        if (row, segment["id"]) in merge_members:
            continue
        if not strong_match(pair, config):
            # Do not borrow another object's segment just because its class
            # matches. Keep unmatched pairs available when no stronger
            # same-class box already accounts for that segment.
            anchored_elsewhere = any(
                other["detection"]["row"] != row
                and other["detection"]["class_name"] == segment["class_name"]
                and strong_match(other, config)
                for other in comparison.pairs[segment["id"]]
            )
            if anchored_elsewhere:
                continue

        area = segment["mask_pixels"]
        candidate = find_bbox_drift_candidate(
            comparison.image, None, comparison.width, comparison.height,
            segment["id"], pair["detection"]["bbox_xyxy"],
            coverage_threshold=mask_config.drift_coverage,
            min_segment_pixels=math.ceil(minimum), box_index=row,
            mask_measurements=(area, round(pair["mask_coverage"] * area)),
        )
        if candidate is not None:
            candidate["box_row"] = candidate.pop("box_index")
            grouped["bbox_drift"].append({
                "segment": segment, "detection": pair["detection"],
                "bbox_iou": pair["bbox_iou"], "mask_coverage": pair["mask_coverage"],
                "measurements": candidate,
            })

    reasons = {
        "bbox_drift": "Box cùng lớp không bao phủ đủ pixel của segment; cần mở ảnh kiểm tra.",
        "instance_merge": "Một segment có pixel riêng đáng kể trong hai box cùng lớp; cần xem số vật thực tế.",
    }
    return [{"id": f"{comparison.image}:{kind}", "image": comparison.image,
             "type": kind, "possible_types": [kind], "reason": reasons[kind],
             "image_available": comparison.image_available, "evidence": evidence}
            for kind, evidence in sorted(grouped.items())]


def generate_candidates(
    delivery: Path, config: Thresholds | None = None,
    mask_config: MaskThresholds | None = None,
) -> dict:
    """Combine class/missing-box evidence with drift/merge in one image pass."""
    config = config or Thresholds()
    mask_config = mask_config or MaskThresholds()
    reader = DeliveryReader(delivery)
    candidates = []
    for image in sorted(reader.manifest):
        comparison = reader.compare(image, include_all_segments=True)
        if comparison is None or not comparison.image_available:
            continue  # Diagnostics retain skipped inputs; these are not clean verdicts.
        class_comparison = Comparison(
            comparison.image, comparison.width, comparison.height, comparison.boxes,
            [segment for segment in comparison.segments
             if segment.get("isthing") == 1 and not segment.get("iscrowd")],
            comparison.spans, comparison.pairs, comparison.image_available,
        )
        candidates.extend(propose(class_comparison, config))
        candidates.extend(propose_mask_candidates(comparison, config, mask_config))

    release_id = read_json(delivery / "release.json")["release_id"]
    return {
        "schema_version": 1, "release_id": release_id,
        "config": asdict(config), "mask_config": asdict(mask_config),
        "candidates": candidates, "diagnostics": reader.diagnostics,
        "summary": {
            "candidates": len(candidates),
            "images": len({candidate["image"] for candidate in candidates}),
            "by_type": dict(sorted(Counter(candidate["type"] for candidate in candidates).items())),
            "diagnostics": len(reader.diagnostics),
        },
    }


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
    parser.add_argument("--drift-coverage", type=float, default=0.90)
    parser.add_argument("--merge-min-fraction", type=float, default=0.15)
    parser.add_argument("--merge-coverage", type=float, default=0.85)
    parser.add_argument("--merge-max-iou", type=float, default=0.30)
    parser.add_argument("--association-distance", type=float, default=1.0)
    parser.add_argument("--min-segment-pixels", type=int, default=128)
    args = parser.parse_args()

    delivery = args.delivery
    if not delivery.is_dir():
        parser.error(f"delivery không tồn tại: {delivery}")
    try:
        output_path(delivery, args.out)
        if args.candidates:
            output_path(delivery, args.candidates)
            if args.out.resolve() == args.candidates.resolve():
                raise ValueError("--out and --candidates must be different files")
        if args.min_segment_pixels < 1:
            raise ValueError("Minimum segment size must be at least one pixel")
        matching_config = Thresholds(min_segment_pixels=args.min_segment_pixels)
        mask_config = MaskThresholds(
            drift_coverage=args.drift_coverage,
            merge_min_fraction=args.merge_min_fraction,
            merge_coverage=args.merge_coverage,
            merge_max_iou=args.merge_max_iou,
            association_distance=args.association_distance,
        )
    except ValueError as exc:
        parser.error(str(exc))

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
        candidate_document = generate_candidates(delivery, matching_config, mask_config)
        args.candidates.parent.mkdir(parents=True, exist_ok=True)
        args.candidates.write_text(json.dumps(candidate_document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"Ứng viên cần người xem: {json.dumps(candidate_document['summary'], ensure_ascii=False)}")

    print(f"Đã ghi {len(output['errors'])} lỗi luật vào {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
