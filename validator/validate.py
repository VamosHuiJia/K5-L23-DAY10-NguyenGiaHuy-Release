#!/usr/bin/env python3
"""CLI entry point for rule checks and annotation review candidates."""
from __future__ import annotations

import argparse
import json
from collections import Counter
from dataclasses import asdict
from pathlib import Path

if __package__:
    from .cross_check import (
        Comparison,
        DeliveryReader,
        Thresholds,
        output_path,
        propose,
        read_json,
    )
    from .mask_checks import (
        MaskThresholds,
        count_segment_pixels,
        count_segment_pixels_in_box,
        calculate_segment_coverage,
        find_bbox_drift_candidate,
        find_instance_merge_candidate,
        propose_mask_candidates,
    )
    from .rule_checks import (
        check_file_detection,
        check_scene_leaks,
        check_guideline_mismatches,
        load_panoptic,
        check_invalid_category_ids,
        check_thing_overlaps,
    )
else:
    from cross_check import (
        Comparison,
        DeliveryReader,
        Thresholds,
        output_path,
        propose,
        read_json,
    )
    from mask_checks import (
        MaskThresholds,
        count_segment_pixels,
        count_segment_pixels_in_box,
        calculate_segment_coverage,
        find_bbox_drift_candidate,
        find_instance_merge_candidate,
        propose_mask_candidates,
    )
    from rule_checks import (
        check_file_detection,
        check_scene_leaks,
        check_guideline_mismatches,
        load_panoptic,
        check_invalid_category_ids,
        check_thing_overlaps,
    )


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
