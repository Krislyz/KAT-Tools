# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Krislyz
"""Run: blender -b --factory-startup --python tests/test_texsearch.py"""
import importlib.util
import os
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("kat_texsearch", os.path.join(HERE, "..", "kat", "core", "texsearch.py"))
ts = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ts)


def touch(root, *parts):
    path = os.path.join(root, *parts)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "wb").close()
    return path


class RoleTest(unittest.TestCase):
    def test_roles(self):
        self.assertEqual(ts.role("body_d.png"), ("colour", "body"))
        self.assertEqual(ts.role("Body_Albedo_2k.png"), ("colour", "body"))
        self.assertEqual(ts.role("body_n.png"), ("other", "body"))
        self.assertEqual(ts.role("body_roughness.png"), ("other", "body"))
        self.assertEqual(ts.role("face.png"), ("plain", "face"))


class SearchTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name

    def test_scan_is_nearest_first_and_skips_junk(self):
        near = touch(self.root, "model", "a.png")
        far = touch(self.root, "model", "deep", "deeper", "b.png")
        touch(self.root, "model", ".git", "c.png")
        touch(self.root, "model", "notes.txt")
        found = ts.scan([os.path.join(self.root, "model")])
        self.assertEqual(found, [near, far])

    def test_scan_stops_at_its_time_limit(self):
        for i in range(300):
            touch(self.root, f"d{i}", "x.png")
        start = time.monotonic()
        ts.scan([self.root], seconds=0.0)
        self.assertLess(time.monotonic() - start, 1.0)

    def test_reference_from_another_computer_matches_by_name(self):
        tex = touch(self.root, "textures", "Body_D.PNG")
        paths = ts.scan([self.root])
        self.assertEqual(ts.match_reference("C:\\Users\\artist\\Desktop\\tex\\body_d.png", paths), tex)

    def test_reference_with_another_extension(self):
        tex = touch(self.root, "tex", "face.png")
        self.assertEqual(ts.match_reference("D:/proj/face.tga", ts.scan([self.root])), tex)

    def test_reference_with_different_separators_and_size_suffix(self):
        tex = touch(self.root, "tex", "Hair-Color_2k.png")
        self.assertEqual(ts.match_reference("hair_color.png", ts.scan([self.root])), tex)

    def test_unrelated_names_do_not_match(self):
        touch(self.root, "tex", "sword.png")
        self.assertIsNone(ts.match_reference("hair_color.png", ts.scan([self.root])))

    def test_material_name_finds_the_colour_map_not_the_normal_map(self):
        touch(self.root, "tex", "skin_n.png")
        colour = touch(self.root, "tex", "skin_d.png")
        touch(self.root, "tex", "skin_roughness.png")
        self.assertEqual(ts.match_material("Skin", ts.scan([self.root])), colour)

    def test_material_with_only_non_colour_maps_gets_nothing(self):
        touch(self.root, "tex", "skin_n.png")
        self.assertIsNone(ts.match_material("Skin", ts.scan([self.root])))

    def test_material_copy_suffix_is_ignored(self):
        tex = touch(self.root, "tex", "cloth.png")
        self.assertEqual(ts.match_material("Cloth.001", ts.scan([self.root])), tex)

    def test_a_big_tree_is_scanned_well_inside_the_limit(self):
        for i in range(60):
            for j in range(40):
                open(touch(self.root, f"folder{i}", f"img{j}.png"), "wb").close()
        start = time.monotonic()
        found = ts.scan([self.root])
        self.assertEqual(len(found), 2400)
        self.assertLess(time.monotonic() - start, 3.0)


if __name__ == "__main__":
    unittest.main(argv=["kat"], exit=False)
