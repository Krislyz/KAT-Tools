# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Krislyz
"""Run: blender -b --factory-startup --python tests/test_synth.py"""
import importlib.util
import os
import unittest

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))


def load(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, "..", "kat", "core", name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


synth = load("synth")
facetrack = load("facetrack")


def head():
    """A box of points 0.15 wide with a mouth near z=0.5 and eyes near z=0.8, facing -Y."""
    rng = np.random.default_rng(1)
    pts = rng.uniform([-0.075, -0.07, 0.3], [0.075, 0.07, 0.95], size=(4000, 3)).astype(np.float32)
    shapes = {}

    def blob(centre, radius, move):
        d = np.zeros_like(pts)
        near = np.linalg.norm(pts - np.array(centre), axis=1) < radius
        d[near] = move
        return d

    shapes["mouth open big1"] = blob((0, -0.05, 0.5), 0.05, (0, 0.004, -0.012))
    shapes["mouth upperlipup"] = blob((0, -0.06, 0.55), 0.02, (0, 0, 0.004))
    shapes["mouth lowerlipdown"] = blob((0, -0.06, 0.45), 0.02, (0, 0, -0.004))
    shapes["mouth wide"] = blob((0, -0.06, 0.5), 0.04, (0.003, 0, 0))
    shapes["tongue out"] = blob((0, -0.04, 0.48), 0.015, (0, -0.02, 0))
    shapes["eye look left"] = blob((0, -0.05, 0.8), 0.07, (0.007, 0, 0))
    shapes["eye look right"] = blob((0, -0.05, 0.8), 0.07, (-0.007, 0, 0))
    return pts, shapes


class SynthTest(unittest.TestCase):
    def setUp(self):
        self.pts, self.shapes = head()
        self.get = lambda *names: next((self.shapes[facetrack.norm(n)] for n in names if facetrack.norm(n) in self.shapes), None)
        self.shapes = {facetrack.norm(k): v for k, v in self.shapes.items()}
        self.wanted = {n for n in facetrack.UNIFIED if n not in facetrack.OPTIONAL}

    def test_makes_shapes_from_what_exists(self):
        out = synth.build(self.pts, self.get, self.wanted)
        for name in ("JawLeft", "JawRight", "JawForward", "TongueTwistLeft", "TongueRight", "LipSuckUpperLeft",
                     "EyeLookInLeft", "EyeLookOutRight", "MouthDimpleLeft", "NasalDilationRight", "MouthRaiserUpper"):
            self.assertIn(name, out, name)
        for name, (delta, how) in out.items():
            self.assertEqual(delta.shape, self.pts.shape, name)
            self.assertTrue(np.isfinite(delta).all(), name)
            self.assertLess(np.abs(delta).max(), 0.05, name)  # nothing flies off the face
            self.assertTrue(how)

    def test_sides_point_the_right_way(self):
        out = synth.build(self.pts, self.get, self.wanted)
        left, right = out["EyeLookOutLeft"][0], out["EyeLookOutRight"][0]
        self.assertGreater(left[:, 0].sum(), 0)    # the character's left is +X
        self.assertLess(right[:, 0].sum(), 0)
        self.assertEqual(np.abs(left[self.pts[:, 0] < -0.02]).max(), 0)  # the left eye shape does not touch the right side
        self.assertGreater(out["JawLeft"][0][:, 0].sum(), 0)
        self.assertLess(out["JawRight"][0][:, 0].sum(), 0)
        self.assertLess(out["JawForward"][0][:, 1].sum(), 0)  # forward is -Y
        self.assertGreater(out["LipSuckUpperLeft"][0][:, 1].sum(), 0)  # pulled in is back, +Y

    def test_only_what_is_wanted_and_nothing_without_sources(self):
        out = synth.build(self.pts, self.get, {"JawLeft"})
        self.assertEqual(list(out), ["JawLeft"])
        none = synth.build(self.pts, lambda *n: None, self.wanted)
        self.assertNotIn("JawLeft", none, "no open-mouth source: no jaw region")
        self.assertNotIn("EyeLookInLeft", none)

    def test_symmetric_detection(self):
        both = self.shapes[facetrack.norm("eye look left")]
        x0 = 0.0
        self.assertTrue(synth.is_symmetric(both, self.pts[:, 0], x0))
        one = both * (self.pts[:, 0] > 0)[:, None]
        self.assertFalse(synth.is_symmetric(one, self.pts[:, 0], x0))


if __name__ == "__main__":
    unittest.main(argv=["kat"], exit=False)
