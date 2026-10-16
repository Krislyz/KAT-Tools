# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Krislyz
"""Mesh module against the synthetic rig. Run: blender -b --factory-startup --python tests/blender_mesh.py"""
import os
import runpy
import sys

import bpy
from mathutils import Vector

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..")))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import strict  # noqa: E402,F401

ns = runpy.run_path(os.path.join(HERE, "blender_armature.py"))  # builds the rig, runs its checks
from kat.modules import armature, mesh  # noqa: E402

rig, ob = ns["build"]()
bpy.context.view_layer.objects.active = rig


def world_verts(o):
    return [o.matrix_world @ v.co for v in o.data.vertices]


# apply transforms keeps the mesh still in the world while the rig goes back to scale 1
rig.scale = (100, 100, 100)
bpy.context.view_layer.update()
before = world_verts(ob)
mesh.apply_transforms(bpy.context, rig)
assert tuple(round(s, 5) for s in rig.scale) == (1, 1, 1), rig.scale
bpy.context.view_layer.update()
after = world_verts(ob)
assert max((a - b).length for a, b in zip(before, after)) < 1e-3, "mesh moved when transforms were applied"
rig.scale = (1, 1, 1)

# clean groups: stray_empty has no weights, hair_01 has some
rig2, ob2 = ns["build"]()
n = mesh.clean_groups(ob2, rig2)
assert n == 1 and "stray_empty" not in ob2.vertex_groups and "hair_01" in ob2.vertex_groups
assert mesh.clean_groups(ob2, rig2, drop_non_bone=False) == 0

# limit to 4 and normalise: give vertex 0 six weights
for name in ("mixamorig:Neck", "mixamorig:Head", "mixamorig:Spine", "mixamorig:Hips", "mixamorig:LeftArm"):
    g = ob2.vertex_groups.get(name) or ob2.vertex_groups.new(name=name)
    g.add([0], 0.1 + 0.05 * len(name) % 0.3, "REPLACE")
before_groups = len([g for g in ob2.data.vertices[0].groups if g.weight > 0])
assert before_groups > 4, before_groups
assert mesh.limit_and_normalize(ob2, rig2) >= 1
ws = [g.weight for g in ob2.data.vertices[0].groups if g.weight > 0]
assert len(ws) == 4 and abs(sum(ws) - 1.0) < 1e-4, ws
assert mesh.limit_and_normalize(ob2, rig2) == 0, "second pass should find nothing to fix"

# weld refuses shape-keyed meshes, welds plain ones
try:
    mesh.weld(ob2, 0.001)
    raise AssertionError("weld should refuse a mesh with shape keys")
except ValueError:
    pass
plain = bpy.data.objects.new("Plain", bpy.data.meshes.new("Plain"))
bpy.context.scene.collection.objects.link(plain)
plain.data.from_pydata([(0, 0, 0), (0, 0, 0.00001), (1, 0, 0), (1, 1, 0)], [], [])
assert mesh.weld(plain, 0.001) == 1 and len(plain.data.vertices) == 3

# join: two rigged meshes become one
rig3, ob3 = ns["build"]()
extra = ob3.copy()
extra.data = ob3.data.copy()
extra.name = "Extra"
bpy.context.scene.collection.objects.link(extra)
bpy.context.view_layer.update()
assert len(armature.meshes_of(rig3)) == 2
joined = mesh.join_meshes(bpy.context, rig3)
assert len(armature.meshes_of(rig3, bpy.context.scene)) == 1
assert len(joined.data.vertices) == 128, len(joined.data.vertices)

# the full one-click pass
rig4, ob4 = ns["build"]()
bpy.context.view_layer.objects.active = rig4
# nothing ticked means nothing changes
names_before = sorted(b.name for b in rig4.data.bones)
meshes_before = len(armature.meshes_of(rig4))
idle = mesh.fix_model(bpy.context, rig4)
assert any("No fix options are ticked" in line for line in idle), idle
assert sorted(b.name for b in rig4.data.bones) == names_before and len(armature.meshes_of(rig4)) == meshes_before

# only the steps asked for run
log = mesh.fix_model(bpy.context, rig4, rename=True)
assert "Hips" in rig4.data.bones and len(armature.meshes_of(rig4)) == meshes_before and "unused_bone" in rig4.data.bones
log = mesh.fix_model(bpy.context, rig4, style="unity", rename=True, transforms=True, clean=True, limit=True, join=True, prune=True)
text = "\n".join(log)
assert "All required humanoid bones present" in text, text
assert "Removed" in text and "unused_bone" not in rig4.data.bones and "Hips" in rig4.data.bones
print("MESH OK")
