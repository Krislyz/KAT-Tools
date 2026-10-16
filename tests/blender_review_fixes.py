# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Krislyz
"""Regression tests for review findings. Run: blender -b --factory-startup --python tests/blender_review_fixes.py"""
import os
import sys

import bpy
import numpy as np

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import strict  # noqa: E402,F401

import kat  # noqa: E402
from kat.core import pack  # noqa: E402
from kat.modules import atlas, outfits, reduce, world  # noqa: E402

bpy.ops.wm.read_factory_settings(use_empty=True)
kat.register()
ctx = bpy.context


def cube(name, at):
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=at)
    ob = ctx.object
    ob.name = name
    return ob


def textured(name, mat, at, uv=True):
    ob = cube(name, at)
    if not uv:
        while ob.data.uv_layers:
            ob.data.uv_layers.remove(ob.data.uv_layers[0])
    ob.data.materials.append(mat)
    return ob


def mat_with_image(name, rgba):
    img = bpy.data.images.new(name, 32, 32, alpha=True)
    img.pixels.foreach_set(np.tile(np.array(rgba, dtype=np.float32), 32 * 32))
    mat = bpy.data.materials.new(name)
    node = next(n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
    tex = mat.node_tree.nodes.new("ShaderNodeTexImage")
    tex.image = img
    mat.node_tree.links.new(tex.outputs["Color"], node.inputs["Base Color"])
    return mat


def uv_of(ob, name=None):
    layer = ob.data.uv_layers[name] if name else ob.data.uv_layers[0]
    out = np.empty(len(layer.data) * 2, dtype=np.float32)
    layer.data.foreach_get("uv", out)
    return out


# 1. lightmap UVs with several meshes selected: only the targets change, texture UVs survive
a, b = cube("A", (0, 0, 0)), cube("B", (3, 0, 0))
before_a, before_b = uv_of(a), uv_of(b)
for o in ctx.view_layer.objects:
    o.select_set(True)
ctx.view_layer.objects.active = a
assert world.add_lightmap_uv(ctx, a)
assert np.allclose(uv_of(b), before_b) and len(b.data.uv_layers) == 1, "B was unwrapped with A"
assert np.allclose(uv_of(a, world.LIGHTMAP_UV if hasattr(world, "LIGHTMAP_UV") else a.data.uv_layers[-1].name), uv_of(a, a.data.uv_layers[-1].name))
assert np.allclose(uv_of(a, a.data.uv_layers[0].name), before_a), "A's texture UVs changed"
assert b.select_get() and ctx.view_layer.objects.active is a, "selection not restored"

# 2. LOD copies are not combinable
lod0, lod1 = cube("Ball_LOD0", (6, 0, 0)), cube("Ball_LOD1", (6, 0, 0))
assert not world.combinable(lod0) and not world.combinable(lod1)

# 3 + 4. atlas with UV-less mesh, alone and sharing a material with a UV mesh
shared = mat_with_image("Shared", (1, 0, 0, 1))
other = mat_with_image("Other", (0, 1, 0, 1))
with_uv = textured("WithUV", other, (9, 0, 0))
no_uv = textured("NoUV", shared, (12, 0, 0), uv=False)
mixed = textured("MixedUV", shared, (15, 0, 0))
extra = textured("Extra", mat_with_image("Extra", (0, 0, 1, 1)), (18, 0, 0))
result = atlas.build([with_uv, no_uv, mixed, extra], size=256)
assert result is not None
assert not no_uv.data.uv_layers, "UV-less mesh was given UVs"

# 5. tiny tiles never shrink below padding
placed = pack.fit([(64, 64)] * 40, 64, gap=0, smallest=4 * atlas.PAD)
assert placed is None or all(w > 2 * atlas.PAD for w, _ in placed[1])

# 6. reduce list keeps slider amounts when refilled
rig = bpy.data.objects.new("Rig", bpy.data.armatures.new("Rig"))
ctx.scene.collection.objects.link(rig)
mesh = cube("Cloth", (0, 0, 3))
mesh.parent = rig
mesh.modifiers.new("Armature", "ARMATURE").object = rig
s = reduce.fill_list(ctx, rig)
s.items[0].amount = 0.25
s = reduce.fill_list(ctx, rig)
assert abs(s.items[0].amount - 0.25) < 1e-6, s.items[0].amount

# 7. character_frame with only a hips bone mapped does not raise
arm = bpy.data.armatures.new("Frame")
frame_rig = bpy.data.objects.new("Frame", arm)
ctx.scene.collection.objects.link(frame_rig)
ctx.view_layer.objects.active = frame_rig
bpy.ops.object.mode_set(mode="EDIT")
bone = arm.edit_bones.new("Hips")
bone.head, bone.tail = (0, 0, 1), (0, 0, 1.2)
bpy.ops.object.mode_set(mode="OBJECT")
outfits.character_frame(frame_rig, {"Hips": "Hips"})  # missing keys must not raise


# 8. meshes rigged to the armature but living in another scene are not "hidden meshes"
from kat.modules import armature  # noqa: E402
other = bpy.data.scenes.new("Snapshot")
ghost = bpy.data.objects.new("Ghost", bpy.data.meshes.new("Ghost"))
ghost.modifiers.new("Armature", "ARMATURE").object = rig
other.collection.objects.link(ghost)
orphan = bpy.data.objects.new("Orphan", bpy.data.meshes.new("Orphan"))
orphan.modifiers.new("Armature", "ARMATURE").object = rig
assert not armature.unreachable_meshes(rig), [o.name for o in armature.unreachable_meshes(rig)]
kat.unregister()
print("REVIEW FIXES OK")
