# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Krislyz
"""Regression tests for problems an independent review found.
Run: blender -b --factory-startup --python tests/blender_safety.py"""
import math
import os
import runpy
import sys
import tempfile

import bpy

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..")))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import strict  # noqa: E402,F401

ns = runpy.run_path(os.path.join(HERE, "blender_armature.py"))
import kat  # noqa: E402
from kat.core import prefs  # noqa: E402
from kat.modules import armature, mesh  # noqa: E402

prefs.data_dir = lambda: tempfile.mkdtemp(prefix="kat_safety_")
ctx = bpy.context


def fresh():
    rig, ob = ns["build"]()
    ctx.view_layer.objects.active = rig
    ctx.view_layer.update()
    return rig, ob


# 1. a mesh in an excluded collection is left alone, not merged away or mis-baked
rig, body = fresh()
hidden_body = body.copy()
hidden_body.data = body.data.copy()
hidden_body.name = "Body2"
coll = bpy.data.collections.new("Hidden")
ctx.scene.collection.children.link(coll)
coll.objects.link(hidden_body)
ctx.view_layer.layer_collection.children["Hidden"].exclude = True
ctx.view_layer.update()
assert armature.meshes_of(rig) == [body]
assert armature.unreachable_meshes(rig) == [hidden_body]
mesh.join_meshes(ctx, rig)
assert "Body2" in bpy.data.objects, "excluded mesh must survive a join"
try:
    armature.apply_pose_as_rest(ctx, rig)
    raise AssertionError("apply pose must refuse while a rigged mesh is out of reach")
except ValueError as err:
    assert "Body2" in str(err)
from kat.modules import export  # noqa: E402
fbx_path = os.path.join(tempfile.mkdtemp(prefix="kat_exp_"), "refused.fbx")
try:
    export.export_unity(ctx, rig, fbx_path)
    raise AssertionError("export must refuse when a rigged mesh would be missing from it")
except ValueError as err:
    assert "Body2" in str(err)
assert not os.path.exists(fbx_path) and not os.path.exists(fbx_path.replace(".fbx", ".kat.json"))
assert set(export.fbx_options()) == set(export.FBX_OPTIONS) | {"use_mesh_modifiers", "use_triangles"}, "this Blender still has every FBX option we use"
log = mesh.fix_model(ctx, rig, rename=True, transforms=True)
assert any(line.startswith("Skipped hidden or excluded meshes: Body2") for line in log), log

# 2. a muted shape key keeps its shape through apply pose
rig, body = fresh()
body.data.shape_keys.key_blocks["smile"].mute = True
rig.pose.bones["mixamorig:LeftArm"].rotation_mode = "XYZ"
rig.pose.bones["mixamorig:LeftArm"].rotation_euler = (0, 0, math.radians(40))
ctx.view_layer.update()
armature.apply_pose_as_rest(ctx, rig)
blocks = body.data.shape_keys.key_blocks
assert blocks["smile"].mute is True, "mute flag must be put back"
gap = [(a.co - b.co).length for a, b in zip(blocks["smile"].data, blocks[0].data)]
assert min(gap) > 0.1, "muted key collapsed onto the basis"
assert body.show_only_shape_key is False

# 3. bones used by bone-parented props, constraints and drivers are never pruned
rig, body = fresh()
ctx.view_layer.objects.active = rig


def add_bones(edit, _):
    for name in ("Prop", "Prop2", "Prop3"):
        eb = edit.new(name)
        eb.head, eb.tail = (0, 0.5, 1.0), (0, 0.6, 1.0)
        eb.parent = edit["mixamorig:Hips"]
        eb.use_deform = False


armature.edit_bones(ctx, rig, [], add_bones)
hat = bpy.data.objects.new("Hat", bpy.data.meshes.new("Hat"))
ctx.scene.collection.objects.link(hat)
hat.parent, hat.parent_type, hat.parent_bone = rig, "BONE", "Prop"
follower = bpy.data.objects.new("Follower", None)
ctx.scene.collection.objects.link(follower)
con = follower.constraints.new("COPY_LOCATION")
con.target, con.subtarget = rig, "Prop2"
driven = bpy.data.objects.new("Driven", None)
ctx.scene.collection.objects.link(driven)
var = driven.driver_add("location", 0).driver.variables.new()
var.type = "TRANSFORMS"
var.targets[0].id, var.targets[0].bone_target = rig, "Prop3"
doomed = set(armature.prunable_bones(rig, armature.meshes_of(rig)))
assert {"Prop", "Prop2", "Prop3"}.isdisjoint(doomed), doomed
assert "unused_bone" in doomed

# 4. in_mode puts back the active object and its mode, even when it fails
rig, body = fresh()
ctx.view_layer.objects.active = rig
bpy.ops.object.mode_set(mode="POSE")
with armature.in_mode(ctx, rig, "EDIT"):
    assert rig.mode == "EDIT"
assert rig.mode == "POSE" and ctx.view_layer.objects.active == rig, "pose mode must come back"
bpy.ops.object.mode_set(mode="OBJECT")
ctx.view_layer.objects.active = body
with armature.in_mode(ctx, rig, "POSE"):
    pass
assert ctx.view_layer.objects.active == body and rig.mode == "OBJECT"
rig.hide_set(True)
try:
    armature.check_ready(ctx, rig)
    raise AssertionError("hidden rig must be refused up front")
except ValueError:
    pass
try:
    with armature.in_mode(ctx, rig, "EDIT"):
        pass
except RuntimeError:
    pass
assert ctx.view_layer.objects.active == body, "active object must be restored when mode_set fails"
rig.hide_set(False)

# 5. operators leave Edit mode on their own and report problems instead of raising tracebacks
kat.register()
rig, body = fresh()
ctx.view_layer.objects.active = body
bpy.ops.object.mode_set(mode="EDIT")
assert bpy.ops.kat.bones_check() == {"FINISHED"} and body.mode == "OBJECT"
body.data.uv_layers.clear() if hasattr(body.data.uv_layers, "clear") else None
try:
    bpy.ops.kat.atlas()
    raise AssertionError("atlas on a mesh with no UVs must report an error")
except RuntimeError as err:
    assert "UV" in str(err) or "materials" in str(err), err
kat.unregister()
print("SAFETY OK")
