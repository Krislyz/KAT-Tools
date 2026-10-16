# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Krislyz
"""Run: blender -b --factory-startup --python tests/test_jp_vrc.py"""
import importlib.util
import os
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))


def load(name):
    spec = importlib.util.spec_from_file_location(f"kat_{name}", os.path.join(HERE, "..", "kat", "core", f"{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


jp, vrc, visemes = load("jp"), load("vrc"), load("visemes")


class JpTest(unittest.TestCase):
    def test_known_terms(self):
        cases = {"髪1": "hair1", "左髪1": "hair1.L", "右前髪２": "front_hair2.R" if False else "bangs2.R",
                 "まばたき": "blink", "ウィンク２右": "wink2.R", "スカート前": "skirt_front", "白目": "eye_white",
                 "足ＩＫ": "leg_ik"}
        for src, want in cases.items():
            self.assertEqual(jp.translate(src), want, src)

    def test_single_vowel_morphs_only_match_whole_names(self):
        self.assertEqual(jp.translate("あ"), "a")
        self.assertEqual(jp.translate("お"), "o")
        self.assertEqual(jp.translate("おっぱい"), "おっぱい")  # not o_っ_ぱ_i
        self.assertEqual(jp.translate("髪あ"), "hair_あ")

    def test_unknown_text_survives(self):
        self.assertEqual(jp.translate("謎"), "謎")
        self.assertEqual(jp.translate("髪謎"), "hair_謎")

    def test_ascii_untouched(self):
        self.assertEqual(jp.translate("Spine.001"), "Spine.001")
        self.assertFalse(jp.has_japanese("Spine.001"))
        self.assertTrue(jp.has_japanese("髪"))


class VrcTest(unittest.TestCase):
    def test_ranks_per_metric(self):
        self.assertEqual(vrc.rank_of("triangles", 32000), 0)
        self.assertEqual(vrc.rank_of("triangles", 32001), 1)
        self.assertEqual(vrc.rank_of("triangles", 70000), 1)
        self.assertEqual(vrc.rank_of("triangles", 70001), 4)
        self.assertEqual(vrc.rank_of("bones", 256), 2)
        self.assertEqual(vrc.rank_of("skinned_meshes", 17), 4)

    def test_worst_metric_sets_overall_rank(self):
        stats = {"triangles": 20000, "texture_mb": 30, "skinned_meshes": 1, "material_slots": 4, "bones": 80}
        per, worst = vrc.evaluate(stats)
        self.assertEqual(vrc.RANKS[worst], "Good")
        self.assertEqual(vrc.RANKS[per["triangles"]], "Excellent")

    def test_texture_estimate(self):
        self.assertAlmostEqual(vrc.texture_mb([(2048, 2048)]), 2048 * 2048 * 4 / 3 / (1 << 20))
        self.assertEqual(vrc.texture_mb([]), 0)


class VisemeTest(unittest.TestCase):
    def test_fifteen_visemes_with_vrchat_names(self):
        self.assertEqual(len(visemes.NAMES), 15)
        self.assertIn("vrc.v_sil", visemes.NAMES)
        self.assertIn("vrc.v_ou", visemes.NAMES)

    def test_guesses_ignore_case_spaces_and_underscores(self):
        names = ["Basis", "Mouth_A", "MOUTH o", "ih", "Eye Close1L", "eye_close1r"]
        self.assertEqual(visemes.guess(["Basis", "Mouth A"], "a"), "Mouth A")
        self.assertEqual(visemes.guess(["mouth_a"], "a"), "mouth_a")
        self.assertEqual(visemes.guess(names, "blink_left"), "Eye Close1L")
        self.assertEqual(visemes.guess(names, "blink_right"), "eye_close1r")
        self.assertEqual(visemes.guess(["Basis", "sculpt"], "blink_left"), "")

    def test_weights_stay_in_range(self):
        for name, ws in visemes.RECIPES.items():
            self.assertEqual(len(ws), 3, name)
            self.assertTrue(all(0 <= w <= 1 for w in ws), name)


class QuestTest(unittest.TestCase):
    def test_quest_limits_are_tighter_and_in_the_same_shape(self):
        self.assertEqual(set(vrc.MOBILE_LIMITS), set(vrc.LIMITS))
        for metric, limits in vrc.MOBILE_LIMITS.items():
            self.assertEqual(len(limits), 4)
            self.assertEqual(list(limits), sorted(limits), metric)
            self.assertLessEqual(limits[-1], vrc.LIMITS[metric][-1], metric)

    def test_same_avatar_ranks_lower_on_quest(self):
        stats = dict(triangles=20000, texture_mb=30, skinned_meshes=2, material_slots=6, bones=100)
        pc = vrc.evaluate(stats, "PC")[1]
        quest = vrc.evaluate(stats, "QUEST")[1]
        self.assertGreater(quest, pc)
        self.assertEqual(vrc.rank_of("triangles", 10000, "QUEST"), 1)
        self.assertEqual(vrc.rank_of("triangles", 10001, "QUEST"), 2)
        self.assertEqual(vrc.rank_of("triangles", 5000), 0, "PC is the default")


if __name__ == "__main__":
    unittest.main(argv=["kat"], exit=False)
