# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Krislyz
"""Run: blender -b --factory-startup --python tests/test_geom.py"""
import importlib.util
import os
import unittest

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("kat_geom", os.path.join(HERE, "..", "kat", "core", "geom.py"))
geom = importlib.util.module_from_spec(spec)
spec.loader.exec_module(geom)


class SpreadTest(unittest.TestCase):
    def line(self, n):
        a = np.arange(n - 1)
        return a, a + 1, np.full(n - 1, 0.01)  # a row of vertices 1 cm apart

    def test_never_below_need_and_fades_with_distance(self):
        a, b, length = self.line(40)
        need = np.zeros(40)
        need[20] = 0.01
        out = geom.spread(need, a, b, length, slope=0.35)
        self.assertGreaterEqual(out[20], 0.01 - 1e-12)
        self.assertGreater(out[21], 0.0)
        self.assertLess(out[25], out[21])
        self.assertAlmostEqual(out[39], 0.0, places=9)

    def test_whole_patch_keeps_its_value(self):
        a, b, length = self.line(10)
        need = np.full(10, 0.004)
        np.testing.assert_allclose(geom.spread(need, a, b, length), need)

    def test_lone_vertices_keep_their_value(self):
        out = geom.spread(np.array([0.0, 0.003]), np.array([], dtype=int), np.array([], dtype=int), np.array([]))
        np.testing.assert_allclose(out, [0.0, 0.003])


if __name__ == "__main__":
    unittest.main(argv=["kat"], exit=False)
