# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Krislyz
"""VRChat's 15 viseme shape keys as mixes of three source mouth shapes. Pure data, no bpy.

The weights are our own phonetic estimates, a starting point to tweak by eye. Sources:
  A  = open mouth ("ah")      O = rounded mouth ("oh")      CH = wide, teeth showing ("ee")
"""
import re

PREFIX = "vrc.v_"

# viseme -> (weight of A, weight of O, weight of CH)
RECIPES = {
    "sil": (0.0, 0.0, 0.0),
    "pp": (0.0, 0.1, 0.0),
    "ff": (0.1, 0.0, 0.25),
    "th": (0.3, 0.0, 0.2),
    "dd": (0.35, 0.0, 0.15),
    "kk": (0.3, 0.1, 0.0),
    "ch": (0.0, 0.5, 0.25),
    "ss": (0.1, 0.0, 0.5),
    "nn": (0.25, 0.0, 0.1),
    "rr": (0.0, 0.3, 0.2),
    "aa": (1.0, 0.0, 0.0),
    "e": (0.2, 0.0, 0.55),
    "ih": (0.0, 0.0, 0.9),
    "oh": (0.2, 0.7, 0.0),
    "ou": (0.0, 1.0, 0.0),
}

NAMES = [PREFIX + v for v in RECIPES]

# Names exporters and MMD models commonly use for the three source mouth shapes, lower case.
GUESSES = {
    "a": ("あ", "aa", "a", "ah", "mouth_a", "mouth a", "vrc.v_aa"),
    "o": ("お", "oh", "o", "mouth_o", "mouth o", "vrc.v_oh"),
    "ch": ("い", "ih", "ch", "i", "ee", "e", "mouth_i", "mouth i", "vrc.v_ih"),
}

BLINK = ("vrc.blink_left", "vrc.blink_right", "vrc.lowerlid_left", "vrc.lowerlid_right")

GUESSES.update({
    "blink_left": ("eyeblinkleft", "eye close1l", "eye close2l", "blink l", "blink_l", "blink left", "wink l", "eyeclosel"),
    "blink_right": ("eyeblinkright", "eye close1r", "eye close2r", "blink r", "blink_r", "blink right", "wink r", "eyecloser"),
})


def _loose(text):
    return re.sub(r"[\W_]+", "", text.lower())


def guess(key_names, role):
    """Pick the shape key most likely to be the `role` ('a', 'o', 'ch', 'blink_left', 'blink_right'), or ''.
    Case, spaces and underscores do not matter."""
    by_loose = {_loose(n): n for n in key_names}
    for candidate in GUESSES[role]:
        if _loose(candidate) in by_loose:
            return by_loose[_loose(candidate)]
    return ""
