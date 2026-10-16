# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Krislyz
"""All Blender-version differences live here so a 5.x API break is fixed in one place."""
import bpy

BLENDER = bpy.app.version  # (major, minor, patch)


def at_least(major, minor=0):
    return BLENDER >= (major, minor, 0)


def selected_bones(rig):
    """Names of the selected bones: the edit bones in Edit mode, else the pose bones (Bone.select is gone in Blender 5)."""
    bones = rig.data.edit_bones if rig.mode == "EDIT" else rig.pose.bones
    return [b.name for b in bones if b.select]


def select_bones(rig, names):
    """Select exactly `names` (pose bones, so Object and Pose mode)."""
    for pb in rig.pose.bones:
        pb.select = pb.name in names
