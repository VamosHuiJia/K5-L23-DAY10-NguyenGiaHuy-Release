"""File and detection rule checks for the Day 10 delivery format.

Only reads files referenced from the supplied delivery directory. The public
validator entry point imports :func:`check_file_detection` and combines these
findings with metadata and panoptic findings.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path


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
