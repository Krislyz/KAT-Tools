# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Krislyz
"""All Blender-version differences live here so a 5.x API break is fixed in one place."""
import bpy

BLENDER = bpy.app.version  # (major, minor, patch)


def at_least(major, minor=0):
    return BLENDER >= (major, minor, 0)
