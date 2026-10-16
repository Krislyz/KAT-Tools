# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Krislyz
"""Armature module against a synthetic Mixamo-style rig.
Run: blender -b --factory-startup --python tests/blender_armature.py"""
import math
import os
import sys

import bpy
from mathutils import Vector

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import strict  # noqa: E402,F401

from kat.core import humanoid  # noqa: E402
from kat.modules import armature  # noqa: E402

BONES = [  # name, parent, head, tail
    ("mixamorig:Hips", None, (0, 0, 1.0), (0, 0, 1.1)),
    ("mixamorig:Spine", "mixamorig:Hips", (0, 0, 1.1), (0, 0, 1.2)),
    ("mixamorig:Spine1", "mixamorig:Spine", (0, 0, 1.2), (0, 0, 1.3)),
    ("mixamorig:Neck", "mixamorig:Spine1", (0, 0, 1.3), (0, 0, 1.4)),
    ("mixamorig:Head", "mixamorig:Neck", (0, 0, 1.4), (0, 0, 1.6)),
    ("mixamorig:LeftShoulder", "mixamorig:Spine1", (0, 0, 1.3), (0.3, 0, 1.3)),
    ("mixamorig:LeftArm", "mixamorig:LeftShoulder", (0.3, 0, 1.3), (0.6, 0, 1.3)),
    ("mixamorig:LeftForeArm", "mixamorig:LeftArm", (0.6, 0, 1.3), (0.9, 0, 1.3)),
    ("mixamorig:LeftHand", "mixamorig:LeftForeArm", (0.9, 0, 1.3), (1.0, 0, 1.3)),
    ("mixamorig:RightShoulder", "mixamorig:Spine1", (0, 0, 1.3), (-0.3, 0, 1.3)),
    ("mixamorig:RightArm", "mixamorig:RightShoulder", (-0.3, 0, 1.3), (-0.6, 0, 1.3)),
    ("mixamorig:RightForeArm", "mixamorig:RightArm", (-0.6, 0, 1.3), (-0.9, 0, 1.3)),
    ("mixamorig:RightHand", "mixamorig:RightForeArm", (-0.9, 0, 1.3), (-1.0, 0, 1.3)),
    ("mixamorig:LeftUpLeg", "mixamorig:Hips", (0.1, 0, 1.0), (0.1, 0, 0.55)),
    ("mixamorig:LeftLeg", "mixamorig:LeftUpLeg", (0.1, 0, 0.55), (0.1, 0, 0.1)),
    ("mixamorig:LeftFoot", "mixamorig:LeftLeg", (0.1, 0, 0.1), (0.1, 0.15, 0.0)),
    ("mixamorig:RightUpLeg", "mixamorig:Hips", (-0.1, 0, 1.0), (-0.1, 0, 0.55)),
    ("mixamorig:RightLeg", "mixamorig:RightUpLeg", (-0.1, 0, 0.55), (-0.1, 0, 0.1)),
    ("mixamorig:RightFoot", "mixamorig:RightLeg", (-0.1, 0, 0.1), (-0.1, 0.15, 0.0)),
    ("hair_01", "mixamorig:Head", (0, 0, 1.6), (0, 0, 1.7)),
    ("hair_02", "hair_01", (0, 0, 1.7), (0, 0, 1.8)),
    ("unused_bone", "mixamorig:Hips", (0.2, 0, 1.0), (0.3, 0, 1.0)),
]
CLEAN = [n for n, *_ in BONES]


def build():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    arm_data = bpy.data.armatures.new("Rig")
    rig = bpy.data.objects.new("Rig", arm_data)
    bpy.context.scene.collection.objects.link(rig)
    bpy.context.view_layer.objects.active = rig
    bpy.ops.object.mode_set(mode="EDIT")
    for name, parent, head, tail in BONES:
        eb = arm_data.edit_bones.new(name)
        eb.head, eb.tail = head, tail
        if parent:
            eb.parent = arm_data.edit_bones[parent]
    bpy.ops.object.mode_set(mode="OBJECT")

    mesh = bpy.data.meshes.new("Body")
    mesh.from_pydata([(x / 4 - 0.5 + 0.5, 0, 0.8 + z / 4) for z in range(8) for x in range(8)], [], [])
    ob = bpy.data.objects.new("Body", mesh)
    bpy.context.scene.collection.objects.link(ob)
    # spread verts across the arm and body so the pose visibly moves some of them
    for i, v in enumerate(mesh.vertices):
        v.co = Vector(((i % 8) * 0.16 - 0.1, 0.0, 0.9 + (i // 8) * 0.12))
    groups = {n: ob.vertex_groups.new(name=n) for n in ("mixamorig:Hips", "mixamorig:LeftArm", "hair_01", "stray_empty")}
    for i, v in enumerate(mesh.vertices):
        if v.co.x > 0.3:
            groups["mixamorig:LeftArm"].add([i], 1.0, "REPLACE")
        else:
            groups["mixamorig:Hips"].add([i], 1.0, "REPLACE")
    groups["hair_01"].add([0], 0.5, "REPLACE")
    ob.shape_key_add(name="Basis")
    key = ob.shape_key_add(name="smile")
    for v in key.data:
        v.co = v.co + Vector((0.0, 0.2, 0.0))
    mod = ob.modifiers.new("Armature", "ARMATURE")
    mod.object = rig
    return rig, ob


def solo_coords(ob, index):
    ob.show_only_shape_key = True
    ob.active_shape_key_index = index
    bpy.context.view_layer.update()
    return armature._evaluated_coords(ob, bpy.context.evaluated_depsgraph_get())


rig, ob = build()
bpy.context.view_layer.objects.active = rig

# 1. rename: map, collisions, vertex groups follow
best, rest = armature.map_bones(rig)
assert armature.missing_required(best) == [], armature.missing_required(best)
assert best["LeftUpperArm"] == "mixamorig:LeftArm" and best["LeftLowerLeg"] == "mixamorig:LeftLeg"
assert set(rest) == {"hair_01", "hair_02", "unused_bone"}, rest
count, plan = armature.rename_bones(rig, "unity")
names = {b.name for b in rig.data.bones}
assert {"Hips", "Spine", "Chest", "LeftUpperArm", "LeftLowerLeg", "RightHand", "hair_01"} <= names, names
vg = {g.name for g in ob.vertex_groups}
assert "LeftUpperArm" in vg and "Hips" in vg and "mixamorig:LeftArm" not in vg, vg
assert count == 19, count

# 2. rename again is a no-op, and a squatter on a target name gets moved aside
assert armature.rename_bones(rig, "unity")[0] == 0
rig.data.bones["hair_02"].name = "Neck_squat"
armature.rename_bones(rig, "blender")
names = {b.name for b in rig.data.bones}
assert "UpperArm.L" in names and "Hips" in names, names
armature.rename_bones(rig, "unity")

# 3. prune removes only the unweighted leaf bones
doomed = armature.prunable_bones(rig, armature.meshes_of(rig, bpy.context.scene))
assert set(doomed) == {"unused_bone", "Neck_squat"}, doomed
armature.delete_bones(bpy.context, rig, doomed)
assert "unused_bone" not in rig.data.bones and "hair_01" in rig.data.bones

# 4. apply pose as rest keeps basis and shape key identical in the world
rig.pose.bones["LeftUpperArm"].rotation_mode = "XYZ"
rig.pose.bones["LeftUpperArm"].rotation_euler = (0, 0, math.radians(40))
bpy.context.view_layer.update()
before = [solo_coords(ob, i) for i in (0, 1)]
ob.show_only_shape_key = False
assert armature.apply_pose_as_rest(bpy.context, rig) == 1
assert tuple(rig.pose.bones["LeftUpperArm"].rotation_euler) == (0, 0, 0), "pose should be reset"
after = [solo_coords(ob, i) for i in (0, 1)]
for b, a in zip(before, after):
    assert max(abs(x - y) for x, y in zip(b, a)) < 1e-4, "mesh moved when pose was applied"
basis_now = [v.co[i] for v in ob.data.vertices for i in range(3)]
assert max(abs(x - y) for x, y in zip(before[0], basis_now)) < 1e-4, "basis should hold the posed shape now"

print("ARMATURE OK")
