"""Basic integration contract: handcrafted delivery only; no private answers."""
import json
import unittest

import test_phase2 as fixtures
from validator import cross_check as cc


class MatchingTest(unittest.TestCase):
    setUp = fixtures.Phase2Test.setUp
    label = fixtures.Phase2Test.label
    write = fixtures.Phase2Test.write
    sync = fixtures.Phase2Test.sync

    def match(self):
        self.sync()
        return cc.DeliveryReader(self.delivery).match(self.image)

    def test_names_sizes_pixel_boxes_and_ids_are_explicit(self):
        result = self.match()
        self.assertEqual((result["image"], result["file_name"], result["width"], result["height"]),
                         ("img_0001", "img_0001.png", 64, 64))
        self.assertEqual(result["boxes"], [{"row": 1, "class_id": 0, "class_name": "truck", "bbox_xyxy": [10, 10, 30, 30]}])
        self.assertEqual((result["segments"][0]["id"], result["segments"][0]["class_name"], result["segments"][0]["iscrowd"]),
                         (523, "truck", 0))
        self.assertEqual([(p["box_row"], p["segment_id"]) for p in result["matches"]], [(1, 523)])
        self.assertEqual(result["unmatched_box_rows"], [])
        self.assertEqual(json.loads(json.dumps(result)), result)

    def test_crowd_and_stuff_are_returned_but_not_selected(self):
        segment = self.panoptic["annotations"][0]["segments_info"][0]
        segment["iscrowd"] = 1
        result = self.match()
        self.assertEqual(result["segments"][0]["iscrowd"], 1)
        self.assertEqual(len(result["pairs"]), 1)
        self.assertFalse(result["pairs"][0]["eligible_for_matching"])
        self.assertEqual(result["matches"], [])
        segment.update(iscrowd=0, category_id=92)
        result = self.match()
        self.assertEqual(result["segments"][0]["isthing"], 0)
        self.assertEqual(result["segments"][0]["class_name"], "wall")
        self.assertEqual(result["matches"], [])

    def test_many_boxes_can_match_one_segment(self):
        # Explicit handcrafted two-box/one-segment integration example.
        self.label("0 .3125 .3125 .3125 .3125\n0 .625 .3125 .3125 .3125\n")
        self.panoptic["annotations"][0]["segments_info"][0].update(bbox=[10, 10, 40, 20], area=800)
        mask = self.delivery / "masks/img_0001.png"
        width, height, pixels = cc.pngutil.read_rgb(mask)
        for y in range(10, 30):
            for x in range(30, 50):
                offset = (y*width+x)*3
                pixels[offset:offset+3] = bytes(cc.pngutil.segment_rgb(523))
        cc.pngutil.write_rgb(mask, width, height, pixels)
        result = self.match()
        self.assertEqual([(p["box_row"], p["segment_id"]) for p in result["matches"]], [(1, 523), (2, 523)])

    def test_class_disagreement_does_not_discard_spatial_match(self):
        self.label("1 .3125 .3125 .3125 .3125\n")
        result = self.match()
        self.assertEqual(len(result["matches"]), 1)
        self.assertFalse(result["matches"][0]["same_class"])

    def test_zero_overlap_evidence_remains_available_for_drift(self):
        self.label("0 .8 .8 .2 .2\n")
        result = self.match()
        self.assertEqual(len(result["pairs"]), 1)
        self.assertEqual(result["pairs"][0]["bbox_iou"], 0)
        self.assertEqual(result["pairs"][0]["mask_coverage"], 0)
        self.assertEqual(result["matches"], [])
        self.assertEqual(result["unmatched_box_rows"], [1])
        self.assertEqual(result["unmatched_segment_ids"], [523])

    def test_box_row_is_source_line_number_and_coordinates_keep_fractions(self):
        self.label("\n0 .32 .3125 .3125 .3125\n")
        result = self.match()
        self.assertEqual(result["boxes"][0]["row"], 2)
        self.assertAlmostEqual(result["boxes"][0]["bbox_xyxy"][0], 10.48)

    def test_default_comparison_and_candidate_format_stay_unchanged(self):
        reader = cc.DeliveryReader(self.delivery)
        default = reader.compare(self.image)
        self.assertNotIn("iscrowd", default.segments[0])
        self.assertNotIn("isthing", default.segments[0])
        before = cc.propose(default, cc.Thresholds())
        reader.match(self.image)
        self.assertEqual(cc.propose(reader.compare(self.image), cc.Thresholds()), before)

    def test_unreadable_mask_is_reported_in_diagnostics(self):
        (self.delivery / "masks/img_0001.png").write_bytes(b"invalid")
        reader = cc.DeliveryReader(self.delivery)
        self.assertIsNone(reader.match(self.image))
        self.assertTrue(reader.diagnostics)


if __name__ == "__main__":
    unittest.main()
