"""Mask pixel evidence for bbox drift and instance merge candidates."""
from __future__ import annotations

import math
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import pngutil

if __package__:
    from .cross_check import Comparison, Thresholds, coverage, strong_match
else:
    from cross_check import Comparison, Thresholds, coverage, strong_match


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
