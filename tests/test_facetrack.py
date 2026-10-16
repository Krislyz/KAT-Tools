# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Krislyz
"""Run: blender -b --factory-startup --python tests/test_facetrack.py"""
import importlib.util
import os
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("kat_facetrack", os.path.join(HERE, "..", "kat", "core", "facetrack.py"))
ft = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ft)


class DataTest(unittest.TestCase):
    def test_unified_list_matches_the_vrcft_page(self):
        self.assertEqual(len(ft.UNIFIED), 102)
        self.assertEqual(len(set(ft.UNIFIED)), 102)
        for name in ("EyeClosedLeft", "EyeWideRight", "JawOpen", "MouthClosed", "MouthCornerPullLeft", "TongueOut",
                     "LipFunnelUpperRight", "BrowInnerUpLeft", "NeckFlexRight", "SoftPalateClose"):
            self.assertIn(name, ft.UNIFIED)

    def test_every_recipe_targets_a_real_unified_shape(self):
        for aliases, targets, group in ft.RECIPES:
            for name, weight in targets:
                self.assertIn(name, ft.UNIFIED, f"{group}: {name}")
                self.assertGreater(weight, 0)

    def test_a_source_that_feeds_several_shapes_adds_up_to_one(self):
        for aliases, targets, group in ft.RECIPES:
            if group in ("blink", "kk_eyeclose1"):  # both eyes close fully for a blink: each eye gets the whole shape
                continue
            self.assertAlmostEqual(sum(w for _, w in targets), 1.0, msg=group)

    def test_blink_closes_each_eye_fully(self):
        mixes, _ = ft.plan(["まばたき"])
        self.assertEqual(sorted(mixes), ["EyeClosedLeft", "EyeClosedRight"])
        self.assertEqual([w for sources in mixes.values() for _, w in sources], [1.0, 1.0])


class PlanTest(unittest.TestCase):
    def test_arkit_names_map_across(self):
        mixes, _ = ft.plan(["eyeBlinkLeft", "jawOpen", "mouthSmileLeft", "browInnerUp", "tongueOut"])
        self.assertEqual(mixes["EyeClosedLeft"], [("eyeBlinkLeft", 1.0)])
        self.assertEqual(mixes["JawOpen"], [("jawOpen", 1.0)])
        self.assertEqual(sorted(mixes["MouthCornerPullLeft"]), [("mouthSmileLeft", 0.5)])
        self.assertEqual(mixes["BrowInnerUpRight"], [("browInnerUp", 0.5)])
        self.assertIn("TongueOut", mixes)
        self.assertNotIn("EyeClosedRight", mixes)

    def test_funnel_spreads_over_four_lip_shapes(self):
        mixes, _ = ft.plan(["mouthFunnel"])
        self.assertEqual(len(mixes), 4)
        self.assertAlmostEqual(sum(w for s in mixes.values() for _, w in s), 1.0)

    def test_names_match_whatever_the_case_and_separators(self):
        mixes, _ = ft.plan(["Jaw_Open", "EYE-BLINK-LEFT"])
        self.assertIn("JawOpen", mixes)
        self.assertIn("EyeClosedLeft", mixes)

    def test_mmd_japanese_morphs(self):
        mixes, groups = ft.plan(["あ", "お", "い", "まばたき", "にこり"])
        self.assertEqual(mixes["JawOpen"], [("あ", 1.0)])
        self.assertIn("MouthStretchLeft", mixes)
        self.assertIn("EyeClosedRight", mixes)
        self.assertIn("MouthCornerSlantLeft", mixes)

    def test_arkit_wins_over_visemes_so_nothing_is_mixed_twice(self):
        mixes, _ = ft.plan(["jawOpen", "vrc.v_aa", "あ"])
        self.assertEqual(mixes["JawOpen"], [("jawOpen", 1.0)])

    def test_an_arkit_smile_is_not_mixed_again_with_a_plain_smile_morph(self):
        mixes, _ = ft.plan(["mouthSmileLeft", "mouthSmileRight", "smile"])
        self.assertEqual(mixes["MouthCornerPullLeft"], [("mouthSmileLeft", 0.5)])
        self.assertEqual(mixes["MouthCornerSlantRight"], [("mouthSmileRight", 0.5)])
        self.assertTrue(all(src == [("mouthSmileLeft", 0.5)] or src == [("mouthSmileRight", 0.5)]
                            for name, src in mixes.items() if name.startswith("MouthCorner")))

    def test_koikatsu_style_names(self):
        names = ["Eye Close1L", "Eye Close1R", "Eye Close1", "Eyebrows Angry1L", "Eyebrows Sad1R", "Mouth WideL",
                 "plump cheekL", "plump cheeks", "Tongue Up", "Tongue Roll", "Mouth Sad Close1", "Eye Look Up"]
        mixes, _ = ft.plan(names)
        self.assertEqual(mixes["EyeClosedLeft"], [("Eye Close1L", 1.0)])
        self.assertEqual(mixes["EyeClosedRight"], [("Eye Close1R", 1.0)])
        self.assertEqual(mixes["BrowLowererLeft"], [("Eyebrows Angry1L", 0.5)])
        self.assertEqual(mixes["BrowPinchLeft"], [("Eyebrows Angry1L", 0.5)])
        self.assertEqual(mixes["BrowInnerUpRight"], [("Eyebrows Sad1R", 1.0)])
        self.assertEqual(mixes["MouthStretchLeft"], [("Mouth WideL", 1.0)])
        self.assertEqual(mixes["CheekPuffLeft"], [("plump cheekL", 1.0)])
        self.assertEqual(mixes["CheekPuffRight"], [("plump cheeks", 1.0)], "fills what is left, at full strength")
        self.assertEqual(mixes["TongueUp"], [("Tongue Up", 1.0)])
        self.assertEqual(mixes["MouthFrownLeft"], [("Mouth Sad Close1", 0.5)])
        self.assertEqual(mixes["EyeLookUpLeft"], [("Eye Look Up", 0.5)])

    def test_the_combined_eye_close_only_fills_in_what_left_and_right_did_not(self):
        mixes, _ = ft.plan(["Eye Close1"])
        self.assertEqual(sorted(mixes), ["EyeClosedLeft", "EyeClosedRight"])

    def test_visemes_are_used_when_there_is_no_arkit_set(self):
        mixes, _ = ft.plan(["vrc.v_aa", "vrc.v_ou"])
        self.assertEqual(mixes["JawOpen"], [("vrc.v_aa", 1.0)])
        self.assertIn("LipPuckerUpperLeft", mixes)

    def test_needed_lists_what_is_missing_by_region(self):
        have, can, missing, groups = ft.needed(["Basis", "jawOpen", "JawLeft", "eyeBlinkLeft"])
        self.assertEqual(have, ["JawLeft"])
        self.assertIn("JawOpen", can)
        self.assertIn("EyeClosedLeft", can)
        self.assertNotIn("EyeClosedLeft", missing.get("Eyes", []))
        self.assertIn("EyeClosedRight", missing["Eyes"])
        self.assertIn("TongueOut", missing["Tongue"])
        self.assertNotIn("Neck", missing, "the optional four are never listed")
        every = have + can + [n for names in missing.values() for n in names]
        self.assertEqual(sorted(every), sorted(n for n in ft.UNIFIED if n not in ft.OPTIONAL))

    def test_every_shape_has_a_region(self):
        self.assertTrue(all(ft.region_of(n) != "Other" for n in ft.UNIFIED))

    def test_nothing_matches_nothing(self):
        self.assertEqual(ft.plan(["Basis", "weird_key", "sculpt1"])[0], {})


if __name__ == "__main__":
    unittest.main(argv=["kat"], exit=False)
