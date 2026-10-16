# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Krislyz
"""Refit clothes onto another body. Run: blender -b --factory-startup --python tests/blender_refit.py"""
import os
import sys

import bpy
import numpy as np

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import strict  # noqa: E402,F401

import kat  # noqa: E402
from kat.core import refit as core  # noqa: E402
from kat.modules import outfits, refit  # noqa: E402

# ---- the numbers on their own ------------------------------------------------------------------------------------
m = core.bone_matrix([0, 0, 0], [0, 0, 1], [1, 0, 0], [1, 2, 0])  # up, length 1  ->  along y, length 2, head at x=1
assert np.allclose(m @ [0, 0, 0, 1], [1, 0, 0, 1]) and np.allclose(m @ [0, 0, 1, 1], [1, 2, 0, 1]), m
assert np.allclose(core.rotation_between([0, 0, 1], [0, 0, -1]) @ [0, 0, 1], [0, 0, -1])

a, b = core.edge_arrays([[0, 1], [1, 2], [2, 3], [3, 4], [5, 6]])
assert list(core.components(7, a, b)) == [0, 0, 0, 0, 0, 1, 1]
hold = np.zeros(5)
hold[0] = 1000.0
target = np.zeros((5, 3))
target[0] = [1, 0, 0]
u = core.diffuse(a[:4], b[:4], 5, hold, target, anchor=1e-3)
assert abs(u[0, 0] - 1.0) < 0.01 and all(u[i, 0] > u[i + 1, 0] for i in range(4)) and u[4, 0] > 0, u[:, 0]
w = core.limit_weights(np.array([[0.4, 0.3, 0.2, 0.05, 0.05, 0.0]]), 4)
assert (w > 0).sum() == 4 and abs(w.sum() - 1) < 1e-9
r = core.rigid_pieces(np.array([[1.0, 0.0], [0.0, 1.0], [1.0, 0.0]]), np.array([0, 0, -1]))
assert np.allclose(r, [[0.5, 0.5], [0.5, 0.5], [1.0, 0.0]])

# ---- a body and an outfit made for a taller one --------------------------------------------------------------------
bpy.ops.wm.read_factory_settings(use_empty=True)
kat.register()
scene = bpy.context.scene
BONES = ["Hips", "Spine", "Chest", "Neck", "Head"]


def make_rig(name, z0, height, parent=None):
    arm = bpy.data.armatures.new(name)
    rig = bpy.data.objects.new(name, arm)
    scene.collection.objects.link(rig)
    bpy.context.view_layer.objects.active = rig
    bpy.ops.object.mode_set(mode="EDIT")
    step = height / len(BONES)
    for i, bone in enumerate(BONES):
        eb = arm.edit_bones.new(bone)
        eb.head, eb.tail = (0, 0, z0 + i * step), (0, 0, z0 + (i + 1) * step)
        if i:
            eb.parent = arm.edit_bones[BONES[i - 1]]
    bpy.ops.object.mode_set(mode="OBJECT")
    return rig


def tube(name, radius, z0, z1, rings=24, around=24, rig=None):
    verts = [(radius * np.cos(t), radius * np.sin(t), z0 + (z1 - z0) * r / (rings - 1))
             for r in range(rings) for t in np.linspace(0, 2 * np.pi, around, endpoint=False)]
    faces = [(r * around + a, r * around + (a + 1) % around, (r + 1) * around + (a + 1) % around, (r + 1) * around + a)
             for r in range(rings - 1) for a in range(around)]
    me = bpy.data.meshes.new(name)
    me.from_pydata(verts, [], faces)
    ob = bpy.data.objects.new(name, me)
    scene.collection.objects.link(ob)
    # weights: each vertex belongs to the bone whose stretch holds its height, blended a little
    arm = rig.data
    groups = {b: ob.vertex_groups.new(name=b) for b in BONES}
    heads = np.array([arm.bones[b].head_local[2] for b in BONES])
    tails = np.array([arm.bones[b].tail_local[2] for b in BONES])
    for v in me.vertices:
        z = v.co.z
        k = int(np.clip(np.searchsorted(tails, z), 0, len(BONES) - 1))
        groups[BONES[k]].add([v.index], 1.0, "REPLACE")
    ob.parent = rig
    ob.modifiers.new("Armature", "ARMATURE").object = rig
    return ob


body_rig = make_rig("Body Rig", 0.8, 1.0)
skin = tube("Skin", 0.15, 0.82, 1.78, rig=body_rig)
outfit_rig = make_rig("Outfit Rig", 1.0, 1.4)  # an avatar a third taller
shirt = tube("Shirt", 0.12, 1.1, 2.2, rig=outfit_rig)  # too narrow for the body, too long
shirt.shape_key_add(name="Basis")
puff = shirt.shape_key_add(name="Puff")
for i, v in enumerate(shirt.data.vertices):
    puff.data[i].co = v.co + type(v.co)((0.01, 0, 0))
# a small separate piece on the shirt: a tie that must stay rigid
tie = tube("TieBit", 0.02, 1.4, 1.5, rings=4, around=8, rig=outfit_rig)
bpy.context.view_layer.update()

s = scene.kat_outfits
s.skins.add().obj = skin
done = refit.refit_outfit(bpy.context, outfit_rig, [skin], body_rig, [shirt, tie], gap=2.0, tighten=0.5, reach=25.0)
by = {r.name: r for r in done.rows}

# the outfit rig is gone and the garments hang on the body rig
assert "Outfit Rig" not in bpy.data.objects
for g in (shirt, tie):
    assert g.parent == body_rig and g.find_armature() == body_rig
    assert {vg.name for vg in g.vertex_groups} <= set(BONES), [vg.name for vg in g.vertex_groups]

# no vertex is inside the body any more, and the shirt sits on it at the gap
rep = by["Shirt"].report
assert rep.inside_after <= 0.005 * rep.vertices, vars(rep)  # the smooth pre-fit already took the cloth out of the body, so nothing may be left inside; a few corners may touch the tie that was fitted first
graph = bpy.context.evaluated_depsgraph_get()
body = refit.Body(bpy.context, [skin], body_rig)
offs = np.array([body.query(p)[3] for p in refit.coords(shirt.data)])
assert offs.min() > 0.0005, offs.min()
assert np.median(offs) < 0.02, "the shirt is drawn onto the body, not left floating"

# length came from the body's skeleton: the shirt starts near the body's chest, not the taller avatar's
z = refit.coords(shirt.data)[:, 2]
assert z.min() > 0.8 and z.max() < 1.9, (z.min(), z.max())

# shape keys kept their meaning: the Puff key is still a 1 cm step along x of the basis
blocks = shirt.data.shape_keys.key_blocks
step = refit.coords(shirt.data, blocks["Puff"]) - refit.coords(shirt.data, blocks[0])
assert np.allclose(step, [0.01, 0, 0], atol=2e-3), step[:3]

# the original is kept as a shape key: where the garment was before the fit, untouched by it, off by default
for g in (shirt, tie):
    keys = g.data.shape_keys.key_blocks
    assert refit.ORIGINAL in keys and keys[refit.ORIGINAL].value == 0.0, [k.name for k in keys]
    assert len(keys[refit.ORIGINAL].data) == len(g.data.vertices), "one point per vertex, after the cutting too"
was = refit.coords(shirt.data, shirt.data.shape_keys.key_blocks[refit.ORIGINAL])
now = refit.coords(shirt.data, shirt.data.shape_keys.key_blocks[0])
assert np.linalg.norm(was - now, axis=1).max() > 0.01, "the original is not the fit"
assert np.hypot(was[:, 0], was[:, 1]).max() < 0.13, "the original is the shirt as it came, narrower than the body"
assert bpy.ops.kat.refit_view(original=True) == {"FINISHED"}
assert all(g.data.shape_keys.key_blocks[refit.ORIGINAL].value == 1.0 for g in (shirt, tie))
assert bpy.ops.kat.refit_view(original=False) == {"FINISHED"}
assert all(g.data.shape_keys.key_blocks[refit.ORIGINAL].value == 0.0 for g in (shirt, tie))

# weights: at most 4 per vertex, adding up to one, and the little piece is one rigid block
for g in (shirt, tie):
    for v in g.data.vertices:
        ws = [x.weight for x in v.groups]
        assert 0 < len(ws) <= 4 and abs(sum(ws) - 1) < 1e-3, ws
tie_sets = {tuple(sorted((tie.vertex_groups[x.group].name, round(x.weight, 3)) for x in v.groups)) for v in tie.data.vertices}
assert len(tie_sets) == 1, tie_sets
assert by["TieBit"].rigid >= 1

# finishing: props are hidden and left out, the rest is listed inner layer first with its fit shapes on
clip, hidden = refit.finish_outfit(bpy.context, [skin], [shirt, tie], "tie, bag", log=lambda *a: None)
assert hidden == ["TieBit"] and tie.hide_viewport and tie.hide_render and not shirt.hide_viewport, hidden
assert [o.name for o in outfits.outfit_objects(bpy.context)] == ["Shirt"]
assert refit.is_prop(shirt, "bag") is False and refit.is_prop(tie, "tie") and not refit.is_prop(shirt, "shir"), "whole words only"
for ob in bpy.data.objects:
    for key in (ob.data.shape_keys.key_blocks if ob.type == "MESH" and ob.data.shape_keys else []):
        if key.name == outfits.FIT + "Shirt":
            assert key.value == 1.0, "fit shapes of what is worn are on"

# every step is optional: with none chosen, nothing is hidden, listed or made
tie.hide_viewport = tie.hide_render = False
bpy.context.scene.kat_outfits.items.clear()
none, nothing = refit.finish_outfit(bpy.context, [skin], [shirt, tie], "tie", log=lambda *a: None, steps=())
assert none is None and nothing == [] and not tie.hide_viewport and not outfits.outfit_objects(bpy.context)
only_list, _ = refit.finish_outfit(bpy.context, [skin], [shirt, tie], "tie", log=lambda *a: None, steps=("list",))
assert only_list is None and {o.name for o in outfits.outfit_objects(bpy.context)} == {"Shirt", "TieBit"}
assert not tie.hide_viewport, "props are hidden only when asked"

kat.unregister()
print("REFIT OK")
