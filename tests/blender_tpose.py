# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Krislyz
"""T-pose. Run: blender -b --factory-startup --python tests/blender_tpose.py"""
import math
import os
import sys

import bpy
from mathutils import Vector

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..")))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import strict  # noqa: E402,F401

from kat.modules import armature, validate  # noqa: E402

ctx = bpy.context
bpy.ops.wm.read_factory_settings(use_empty=True)

# An A-pose rig: arms 45 degrees down, legs splayed outwards.
BONES = [
    ("Hips", None, (0, 0, 1.0), (0, 0, 1.1)),
    ("Spine", "Hips", (0, 0, 1.1), (0, 0, 1.3)),
    ("Head", "Spine", (0, 0, 1.3), (0, 0, 1.6)),
    ("LeftUpperArm", "Spine", (0.2, 0, 1.3), (0.5, 0, 1.0)),
    ("LeftLowerArm", "LeftUpperArm", (0.5, 0, 1.0), (0.5, 0, 0.7)),  # a bent elbow: needs its own correction
    ("LeftHand", "LeftLowerArm", (0.5, 0, 0.7), (0.5, 0, 0.6)),
    ("RightUpperArm", "Spine", (-0.2, 0, 1.3), (-0.5, 0, 1.0)),
    ("RightLowerArm", "RightUpperArm", (-0.5, 0, 1.0), (-0.8, 0, 0.7)),
    ("RightHand", "RightLowerArm", (-0.8, 0, 0.7), (-0.9, 0, 0.6)),
    ("LeftUpperLeg", "Hips", (0.1, 0, 1.0), (0.2, 0, 0.55)),
    ("LeftLowerLeg", "LeftUpperLeg", (0.2, 0, 0.55), (0.3, 0, 0.1)),
    ("LeftFoot", "LeftLowerLeg", (0.3, 0, 0.1), (0.3, -0.15, 0.0)),
    ("RightUpperLeg", "Hips", (-0.1, 0, 1.0), (-0.2, 0, 0.55)),
    ("RightLowerLeg", "RightUpperLeg", (-0.2, 0, 0.55), (-0.3, 0, 0.1)),
    ("RightFoot", "RightLowerLeg", (-0.3, 0, 0.1), (-0.3, -0.15, 0.0)),
]
data = bpy.data.armatures.new("Rig")
rig = bpy.data.objects.new("Rig", data)
ctx.scene.collection.objects.link(rig)
ctx.view_layer.objects.active = rig
bpy.ops.object.mode_set(mode="EDIT")
for name, parent, head, tail in BONES:
    eb = data.edit_bones.new(name)
    eb.head, eb.tail = head, tail
    if parent:
        eb.parent = data.edit_bones[parent]
bpy.ops.object.mode_set(mode="OBJECT")

# one vertex sits at the left upper arm's far end and follows it
mesh = bpy.data.meshes.new("Body")
mesh.from_pydata([(0.5, 0, 1.0)], [], [])
body = bpy.data.objects.new("Body", mesh)
ctx.scene.collection.objects.link(body)
body.vertex_groups.new(name="LeftUpperArm").add([0], 1.0, "REPLACE")
body.modifiers.new("Armature", "ARMATURE").object = rig
ctx.view_layer.update()

best, _ = armature.map_bones(rig)
assert abs(validate.arm_elevation(rig, best, "Right") + 45) < 1.0, "the test rig starts in an A-pose"


def segment(a, b):
    pa, pb = rig.pose.bones[a], rig.pose.bones[b]
    return (pb.head - pa.head).normalized()


moved, lacking = armature.make_tpose(ctx, rig)
# straight limbs only need their top bone turned (the lower bone rides along), the bent left elbow needs both
assert {"LeftUpperArm", "LeftLowerArm", "RightUpperArm", "LeftUpperLeg", "RightUpperLeg"} <= set(moved) and lacking == [], (moved, lacking)
assert "RightLowerArm" not in moved, "a straight arm's lower bone is already in place"
for side, sign in (("Left", 1.0), ("Right", -1.0)):
    for a, b in ((f"{side}UpperArm", f"{side}LowerArm"), (f"{side}LowerArm", f"{side}Hand")):
        d = segment(a, b)
        assert (d - Vector((sign, 0, 0))).length < 1e-3, (a, b, tuple(d))
    for a, b in ((f"{side}UpperLeg", f"{side}LowerLeg"), (f"{side}LowerLeg", f"{side}Foot")):
        d = segment(a, b)
        assert (d - Vector((0, 0, -1))).length < 1e-3, (a, b, tuple(d))

# the skinned vertex followed its bone: upper arm length is sqrt(0.3^2 + 0.3^2), now horizontal from the shoulder
ev = body.evaluated_get(ctx.evaluated_depsgraph_get()).data.vertices[0].co
length = math.hypot(0.3, 0.3)
assert (ev - Vector((0.2 + length, 0, 1.3))).length < 1e-3, tuple(ev)

# it only touched the pose: the rest pose is as it was, and applying it makes a T-pose the rest pose
assert abs(rig.data.bones["LeftLowerArm"].head_local.z - 1.0) < 1e-5
armature.apply_pose_as_rest(ctx, rig)
best, _ = armature.map_bones(rig)
for side in ("Left", "Right"):
    assert abs(validate.arm_elevation(rig, best, side)) < 0.1, side
assert abs(rig.data.bones["LeftLowerArm"].head_local.z - 1.3) < 1e-3

# a second run has nothing left to do
moved, _ = armature.make_tpose(ctx, rig)
assert moved == [], moved

# a rig without limbs says what is missing instead of failing
bare = bpy.data.objects.new("Bare", bpy.data.armatures.new("Bare"))
ctx.scene.collection.objects.link(bare)
ctx.view_layer.update()
moved, lacking = armature.make_tpose(ctx, bare)
assert moved == [] and "LeftUpperArm" in lacking

# pose mode buttons: start keeps the pose, reset clears it, stop leaves pose mode
import kat  # noqa: E402
kat.register()
ctx.view_layer.objects.active = rig
bpy.ops.object.mode_set(mode="OBJECT")
assert bpy.ops.kat.pose_start() == {"FINISHED"} and rig.mode == "POSE"
rig.pose.bones[0].rotation_mode = "XYZ"
rig.pose.bones[0].rotation_euler = (0.3, 0, 0)
assert bpy.ops.kat.pose_stop() == {"FINISHED"} and rig.mode == "OBJECT"
assert abs(rig.pose.bones[0].rotation_euler.x - 0.3) < 1e-6, "stopping keeps the pose"
assert bpy.ops.kat.pose_reset() == {"FINISHED"}
assert all(pb.matrix_basis == pb.matrix_basis.__class__() for pb in rig.pose.bones), "reset puts every bone back"
kat.unregister()
print("TPOSE OK")
