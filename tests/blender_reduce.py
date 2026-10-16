# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Krislyz
"""Reduce triangles: sliders per mesh, shape keys and weights survive, hand skinning is spared.
Run: blender -b --factory-startup --python tests/blender_reduce.py"""
import os
import sys

import bpy
import numpy as np

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import strict  # noqa: E402,F401

import kat  # noqa: E402
from kat.modules import armature, face, reduce  # noqa: E402

kat.register()
scene = bpy.context.scene

data = bpy.data.armatures.new("Rig")
rig = bpy.data.objects.new("Rig", data)
scene.collection.objects.link(rig)
bpy.context.view_layer.objects.active = rig
bpy.ops.object.mode_set(mode="EDIT")
for name, head, tail, parent in (("Hips", (0, 0, -1), (0, 0, 0), None), ("LeftHand", (0, 0, 0), (0, 0, 1), "Hips")):
    eb = data.edit_bones.new(name)
    eb.head, eb.tail = head, tail
    if parent:
        eb.parent = data.edit_bones[parent]
bpy.ops.object.mode_set(mode="OBJECT")


def sphere(name, rings=32, segments=48):
    bpy.ops.mesh.primitive_uv_sphere_add(radius=1.0, segments=segments, ring_count=rings)
    ob = bpy.context.object
    ob.name = name
    ob.parent = rig
    mod = ob.modifiers.new("Armature", "ARMATURE")
    mod.object = rig
    top = ob.vertex_groups.new(name="LeftHand")
    low = ob.vertex_groups.new(name="Hips")
    for v in ob.data.vertices:
        (top if v.co.z > 0.5 else low).add([v.index], 1.0, "REPLACE")
    return ob


body = sphere("Skin")
body.shape_key_add(name="Basis")
smile = body.shape_key_add(name="Bulge")
for i, v in enumerate(body.data.vertices):
    smile.data[i].co = v.co * 1.1  # grows the whole shape by 10%
smile.value = 0.5
bpy.context.view_layer.update()
before = reduce.tris_of(body)
top_before = sum(1 for v in body.data.vertices if v.co.z > 0.5)
low_before = sum(1 for v in body.data.vertices if v.co.z < -0.5)

b, a = reduce.reduce_mesh(bpy.context, body, 0.3, rig)
assert b == before and a < before * 0.75, (b, a)  # half the sphere is hand skinning, which is spared
assert body.name in bpy.data.objects and len(body.data.shape_keys.key_blocks) == 2
key = body.data.shape_keys.key_blocks["Bulge"]
assert abs(key.value - 0.5) < 1e-9 and key.relative_key.name == "Basis"
radius = np.array([v.co.length for v in body.data.vertices])
grown = np.array([key.data[i].co.length for i in range(len(body.data.vertices))])
assert np.allclose(grown / radius, 1.1, atol=0.03), (grown / radius).min()  # the shape key still means the same thing
assert abs(radius.mean() - 1.0) < 0.03, radius.mean()  # and the basis is still the basis, not the first key
assert {g.name for g in body.vertex_groups} == {"LeftHand", "Hips"}, ([g.name for g in body.vertex_groups], "the temporary keep group is gone")
assert all(g.weight <= 1.0001 for v in body.data.vertices for g in v.groups)
top_after = sum(1 for v in body.data.vertices if v.co.z > 0.5)
low_after = sum(1 for v in body.data.vertices if v.co.z < -0.5)
assert top_after / top_before > 1.6 * (low_after / low_before), (top_after / top_before, low_after / low_before)  # hands spared

# a single triangle is possible, and 1 means untouched
plain = sphere("Plain")
assert reduce.reduce_mesh(bpy.context, plain, 1.0, rig) == (reduce.tris_of(plain),) * 2
b, a = reduce.reduce_mesh(bpy.context, plain, 0.0, rig, keep_hands=False)
assert a <= 8, a

# protection guesses
face_mesh = sphere("Body", 100, 128)
hair = sphere("Hair", 100, 128)
gloves = sphere("Gloves", 100, 128)
shirt = sphere("Shirt", 100, 128)
assert reduce.protection(face_mesh, face_mesh) == "face"
assert reduce.protection(hair, face_mesh) == "hair" and reduce.protection(gloves, face_mesh) == "hands"
assert reduce.protection(shirt, face_mesh) == ""
shirt["kat_protect"] = True
assert reduce.protection(shirt, face_mesh) == "protected by you"
shirt["kat_protect"] = False
hair["kat_protect"] = False
assert reduce.protection(shirt, face_mesh) == "" and reduce.protection(hair, face_mesh) == ""
del hair["kat_protect"], shirt["kat_protect"]

# auto-fill keeps hair, face and hands at 1 and cuts the rest evenly to the budget
bpy.context.view_layer.objects.active = rig
s = reduce.settings(bpy.context)
s.budget = "EXCELLENT"
reduce.suggest(bpy.context, rig)
items = {i.obj.name: i for i in s.items}
assert items["Hair"].amount == items["Gloves"].amount == items["Skin"].amount == 1.0  # Skin has the shape keys, so it is taken for the face and items["Shirt"].amount < 1.0, {k: (v.amount, v.note, v.tris) for k, v in items.items()}
assert reduce.predicted(s) <= 32000 * 1.02 or "alone are over it" in reduce.summary["text"], reduce.summary["text"]
assert bpy.ops.kat.reduce() == {"FINISHED"} and all(i.amount == 1.0 for i in s.items), "applied sliders go back to 1"

# Protect works on the mesh picked in the list, with nothing selected in the viewport
for ob in bpy.context.view_layer.objects:
    ob.select_set(False)
s.items.clear()
reduce.fill_list(bpy.context, rig)
pick = next(n for n, i in enumerate(s.items) if i.obj.name == "Plain")
s.index = pick
s.items[pick].amount = 0.2
assert bpy.ops.kat.protect_toggle() == {"FINISHED"}
assert bpy.data.objects["Plain"].get("kat_protect") is True
assert s.items[pick].note == "protected by you" and s.items[pick].amount == 1.0
assert bpy.ops.kat.protect_toggle() == {"FINISHED"} and bpy.data.objects["Plain"].get("kat_protect") is False, "second click allows it again"


# real-time preview: off by default, a Decimate stand-in while on, gone on Reduce
s.items.clear()
reduce.fill_list(bpy.context, rig)
pick = 0
plain = s.items[0].obj
s.items[pick].amount = 0.5
assert reduce.PREVIEW not in plain.modifiers, "preview is opt-in"
s.live = True
assert plain.modifiers[reduce.PREVIEW].ratio == 0.5 and plain.modifiers[0].name == reduce.PREVIEW
s.items[pick].amount = 0.25
assert abs(plain.modifiers[reduce.PREVIEW].ratio - 0.25) < 1e-6, (plain.modifiers[reduce.PREVIEW].ratio, s.items[pick].tris)
s.items[pick].amount = 1.0
assert reduce.PREVIEW not in plain.modifiers and reduce.KEEP_GROUP not in plain.vertex_groups
s.items[pick].amount = 0.5
s.live = False
assert reduce.PREVIEW not in plain.modifiers
s.live = True
before_tris = reduce.tris_of(plain)
reduce.reduce_all(bpy.context, rig)
assert reduce.PREVIEW not in plain.modifiers and reduce.tris_of(plain) < before_tris
kat.unregister()
print("REDUCE OK")
