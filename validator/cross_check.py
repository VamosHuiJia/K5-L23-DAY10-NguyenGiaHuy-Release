"""Detection/panoptic reading and matching API, from commit e8de233."""
from __future__ import annotations

import json
import math
import sys
import zlib
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import pngutil


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
