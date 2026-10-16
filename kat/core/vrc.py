# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Krislyz
"""VRChat PC avatar performance ranks. Pure data, no bpy.

Limits from https://creators.vrchat.com/avatars/avatar-performance-ranking-system/ . VRChat changes
these now and then, so treat the numbers as a snapshot and check the SDK's own stats before uploading.
The Quest (Android) numbers are the same kind of snapshot: from the same page, as I read it. VRChat's own SDK is the
last word on both.
"""

RANKS = ["Excellent", "Good", "Medium", "Poor", "Very Poor"]

# metric -> upper limits for Excellent, Good, Medium, Poor
LIMITS = {
    "triangles": (32000, 70000, 70000, 70000),
    "texture_mb": (40, 75, 110, 150),
    "skinned_meshes": (1, 2, 8, 16),
    "material_slots": (4, 8, 16, 32),
    "bones": (75, 150, 256, 400),
}

# Quest / Android avatars, same layout
MOBILE_LIMITS = {
    "triangles": (7500, 10000, 15000, 20000),
    "texture_mb": (10, 18, 25, 40),
    "skinned_meshes": (1, 1, 2, 2),
    "material_slots": (1, 1, 2, 2),
    "bones": (75, 90, 150, 150),
}
PLATFORMS = {"PC": LIMITS, "QUEST": MOBILE_LIMITS}

LABELS = {
    "triangles": "Triangles",
    "texture_mb": "Texture memory (MB, estimate)",
    "skinned_meshes": "Skinned meshes",
    "material_slots": "Material slots",
    "bones": "Bones",
}


def rank_of(metric, value, platform="PC"):
    """Index into RANKS for one metric."""
    for i, limit in enumerate(PLATFORMS[platform][metric]):
        if value <= limit:
            return i
    return len(RANKS) - 1


def evaluate(stats, platform="PC"):
    """({metric: rank index}, worst rank index). The worst metric sets the avatar's rank."""
    per = {m: rank_of(m, stats[m], platform) for m in LIMITS}
    return per, max(per.values())


def texture_mb(sizes):
    """Rough VRAM for a list of (width, height) textures: BC7/DXT5 is one byte per pixel, mipmaps add a third."""
    return sum(w * h for w, h in sizes) * (4 / 3) / (1 << 20)
