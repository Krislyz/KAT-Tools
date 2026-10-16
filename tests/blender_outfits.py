# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Krislyz
"""Anti-clip fit shapes: layers that clip get pushed apart, nothing else moves, the pose comes back.
Run: blender -b --factory-startup --python tests/blender_outfits.py"""
import os
import sys

import bpy
import numpy as np
from mathutils import Matrix, Vector

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import strict  # noqa: E402,F401

import kat  # noqa: E402
from kat.modules import face, outfits  # noqa: E402

kat.register()
scene = bpy.context.scene


def sphere(name, radius, at=(0, 0, 0)):
    bpy.ops.mesh.primitive_uv_sphere_add(radius=radius, location=at, segments=24, ring_count=12)
    ob = bpy.context.object
    ob.name = name
    return ob


def apply_keys(ob, on=True):
    for name in outfits.fit_keys(ob):
        face.key_blocks(ob)[name].value = 1.0 if on else 0.0


def world(ob):
    graph = bpy.context.evaluated_depsgraph_get()
    return outfits.snapshot(ob, graph)[0]


def gap_between(outer, inner):
    bvh = outfits.build_bvh(world(inner), outfits.triangles_of(inner))
    return min(bvh.find_nearest(Vector(p.tolist()))[3] for p in world(outer))


# ---- layers that clip: skin, tank top slightly out of the skin, shirt barely out of the tank top ----
skin = sphere("Body", 1.0)
tank = sphere("Tank", 1.0005)
shirt = sphere("Shirt", 1.0008)
before = world(shirt).copy()
res = outfits.run_fit(bpy.context, [skin], [tank, shirt], 0.002, "QUICK", 0.03, "PUSH")
names = {(r.garment, r.over) for r in res.rows}
assert ("Tank", "Body") in names and ("Shirt", "Body") in names and ("Shirt", "Tank") in names, names
assert res.poses == 1, "no rig: only the rest pose is checked"

assert all(abs(face.key_blocks(ob)[k].value) < 1e-9 for ob in (tank, shirt) for k in outfits.fit_keys(ob)), "keys start off"
assert np.allclose(world(shirt), before, atol=1e-6), "creating the keys must not move anything"
assert face.key_blocks(shirt)[0].name == "Basis"

apply_keys(tank)
apply_keys(shirt)
assert gap_between(tank, skin) > 0.0018, gap_between(tank, skin)
assert gap_between(shirt, tank) > 0.0018, gap_between(shirt, tank)
assert gap_between(shirt, skin) > 0.0018

# worn without the tank top, the shirt only needs the skin key and is not puffed up for the missing layer
face.key_blocks(shirt)[outfits.FIT + "Tank"].value = 0.0
assert 0.0018 < gap_between(shirt, skin) < 0.0045, gap_between(shirt, skin)
tank_only = outfits.FIT + "Body"
assert tank_only in outfits.fit_keys(tank)

# a second run replaces the keys instead of piling them up
apply_keys(tank, False)
apply_keys(shirt, False)
outfits.run_fit(bpy.context, [skin], [tank, shirt], 0.002, "QUICK", 0.03, "PUSH")
assert sorted(outfits.fit_keys(shirt)) == sorted([outfits.FIT + "Body", outfits.FIT + "Tank"]), outfits.fit_keys(shirt)

# clothes that are far apart need nothing
far = sphere("Far", 1.2)
res = outfits.run_fit(bpy.context, [skin], [far], 0.002, "QUICK", 0.03, "PUSH")
assert not res.rows and not outfits.fit_keys(far)

# a layer buried deeper than the limit is left alone, not inflated
deep = sphere("Deep", 0.998)
res = outfits.run_fit(bpy.context, [skin], [deep], 0.002, "QUICK", 0.0045, "PUSH")
assert res.rows and 3.9 < max(r.mm for r in res.rows) <= 4.5, [r.mm for r in res.rows]
buried = sphere("Buried", 0.9)
assert not outfits.run_fit(bpy.context, [skin], [buried], 0.002, "QUICK", 0.01, "PUSH").rows

# hidden layers are refused with a reason
far.hide_viewport = True
try:
    outfits.run_fit(bpy.context, [skin], [far], 0.002, "QUICK", 0.03, "PUSH")
    raise AssertionError("hidden garment accepted")
except ValueError as err:
    assert "hidden" in str(err)
far.hide_viewport = False

# an object that was deleted from the scene (still in the file) is refused, not measured as if it were posed
ghost = sphere("Ghost", 1.3)
for coll in list(ghost.users_collection):
    coll.objects.unlink(ghost)
try:
    outfits.run_fit(bpy.context, [skin], [ghost], 0.002, "QUICK", 0.03, "PUSH")
    raise AssertionError("object outside the scene accepted")
except ValueError as err:
    assert "not in this scene" in str(err)
bpy.data.objects.remove(ghost)


# ---- skin mode: the skin moves in under the clothes, the clothes do not move ----
for ob in list(bpy.data.objects):
    bpy.data.objects.remove(ob)
skin = sphere("Skin", 1.0)
tank = sphere("Tank", 1.0005)
shirt = sphere("Shirt", 1.0008)
res = outfits.run_fit(bpy.context, [skin], [tank, shirt], 0.002, "QUICK", 0.03)
assert not outfits.fit_keys(tank), "clothes keep their shape"
assert outfits.fit_keys(skin) == [outfits.FIT + "Tank", outfits.FIT + "Shirt"], outfits.fit_keys(skin)
assert outfits.FIT + "Tank" in outfits.fit_keys(shirt), "layers over layers still push out"
face.key_blocks(skin)[outfits.FIT + "Tank"].value = 1.0
face.key_blocks(skin)[outfits.FIT + "Shirt"].value = 1.0
face.key_blocks(shirt)[outfits.FIT + "Tank"].value = 1.0
assert gap_between(tank, skin) > 0.0018 and gap_between(shirt, skin) > 0.0018 and gap_between(shirt, tank) > 0.0018
assert all(r.mm < 5.0 for r in res.rows), [r.mm for r in res.rows]

# a skin part that only touches the cloth from outside (a hand on a shirt) is not under it and is left alone
hand = sphere("Hand", 0.1, (1.12, 0, 0))
res = outfits.run_fit(bpy.context, [skin, hand], [shirt], 0.002, "QUICK", 0.03)
assert not outfits.fit_keys(hand), "a hand resting on the shirt is not clothing's business"
assert outfits.fit_keys(skin)
bpy.data.objects.remove(hand)

# cloth with its normals turned inwards (double-sided imports) still clears the skin, in the right direction
import bmesh  # noqa: E402
inside_out = sphere("Inside", 1.0006)
bm = bmesh.new()
bm.from_mesh(inside_out.data)
bmesh.ops.reverse_faces(bm, faces=bm.faces)
bm.to_mesh(inside_out.data)
bm.free()
res = outfits.run_fit(bpy.context, [skin], [inside_out], 0.002, "QUICK", 0.03)
assert outfits.fit_keys(skin) == [outfits.FIT + "Inside"], outfits.fit_keys(skin)
face.key_blocks(skin)[outfits.FIT + "Inside"].value = 1.0
assert gap_between(inside_out, skin) > 0.0018, gap_between(inside_out, skin)
assert res.inside_out > 0
bpy.data.objects.remove(inside_out)

# the fit follows the basis: a blendshape that is switched on while fitting changes nothing, and stays on afterwards
bulge_skin = sphere("Bulge", 1.0)
bulge_cloth = sphere("BulgeCloth", 1.0005)
bulge_cloth.shape_key_add(name="Basis")
away = bulge_cloth.shape_key_add(name="Away")
for i, v in enumerate(bulge_cloth.data.vertices):
    away.data[i].co = v.co * 1.2  # lifts the cloth well clear of the skin
away.value = 1.0
res = outfits.run_fit(bpy.context, [bulge_skin], [bulge_cloth], 0.002, "QUICK", 0.03)
assert outfits.fit_keys(bulge_skin), "measured with the Away shape on instead of the basis"
assert away.value == 1.0, "the user's blendshape value is put back"
bpy.data.objects.remove(bulge_skin)
bpy.data.objects.remove(bulge_cloth)

# ---- with a rig: poses are tried, and the pose is put back ----
for ob in list(bpy.data.objects):
    bpy.data.objects.remove(ob)
bones = {  # name: (head, tail, parent)
    "Hips": ((0, 0, 0.9), (0, 0, 1.0), None), "Spine": ((0, 0, 1.0), (0, 0, 1.2), "Hips"),
    "Chest": ((0, 0, 1.2), (0, 0, 1.4), "Spine"), "Neck": ((0, 0, 1.4), (0, 0, 1.5), "Chest"),
    "Head": ((0, 0, 1.5), (0, 0, 1.7), "Neck"),
}
for side, sign in (("Left", 1), ("Right", -1)):
    bones[f"{side}UpperArm"] = ((0.1 * sign, 0, 1.4), (0.35 * sign, 0, 1.4), "Chest")
    bones[f"{side}LowerArm"] = ((0.35 * sign, 0, 1.4), (0.6 * sign, 0, 1.4), f"{side}UpperArm")
    bones[f"{side}Hand"] = ((0.6 * sign, 0, 1.4), (0.7 * sign, 0, 1.4), f"{side}LowerArm")
    bones[f"{side}UpperLeg"] = ((0.1 * sign, 0, 0.9), (0.1 * sign, 0, 0.5), "Hips")
    bones[f"{side}LowerLeg"] = ((0.1 * sign, 0, 0.5), (0.1 * sign, 0, 0.1), f"{side}UpperLeg")
    bones[f"{side}Foot"] = ((0.1 * sign, 0, 0.1), (0.1 * sign, -0.1, 0.0), f"{side}LowerLeg")
arm_data = bpy.data.armatures.new("Rig")
rig = bpy.data.objects.new("Rig", arm_data)
scene.collection.objects.link(rig)
bpy.context.view_layer.objects.active = rig
bpy.ops.object.mode_set(mode="EDIT")
for name, (head, tail, parent) in bones.items():
    eb = arm_data.edit_bones.new(name)
    eb.head, eb.tail = head, tail
    if parent:
        eb.parent = arm_data.edit_bones[parent]
bpy.ops.object.mode_set(mode="OBJECT")


def limb(name, radius, bone):
    """An open tube along the upper arm (rings of vertices, radial normals), fully weighted to its bone."""
    rings, around = 11, 16
    verts = [(0.1 + 0.25 * r / (rings - 1), radius * np.cos(a), 1.4 + radius * np.sin(a))
             for r in range(rings) for a in np.linspace(0, 2 * np.pi, around, endpoint=False)]
    faces = [(r * around + a, r * around + (a + 1) % around, (r + 1) * around + (a + 1) % around, (r + 1) * around + a)
             for r in range(rings - 1) for a in range(around)]
    me = bpy.data.meshes.new(name)
    me.from_pydata(verts, [], faces)
    ob = bpy.data.objects.new(name, me)
    scene.collection.objects.link(ob)
    bpy.context.view_layer.objects.active = ob
    group = ob.vertex_groups.new(name=bone)
    group.add(list(range(len(ob.data.vertices))), 1.0, "REPLACE")
    ob.parent = rig
    mod = ob.modifiers.new("Armature", "ARMATURE")
    mod.object = rig
    return ob


arm_skin = limb("Body", 0.05, "LeftUpperArm")
sleeve = limb("Sleeve", 0.0505, "LeftUpperArm")  # nearly the same radius: clips at every angle
bpy.context.view_layer.update()
res = outfits.run_fit(bpy.context, [arm_skin], [sleeve], 0.002, "NORMAL", 0.03)
assert res.poses > 20, res.poses
assert [r for r in res.rows if r.over == "Sleeve"], "the arm needed to move in under the sleeve"
for name in bones:
    assert rig.pose.bones[name].matrix_basis == Matrix(), f"pose not restored: {name}"
frame = outfits.character_frame(rig, __import__("kat.modules.armature", fromlist=["x"]).map_bones(rig)[0])
up, fwd, left = frame
assert up.z > 0.99 and left.x > 0.99 and fwd.y < -0.99, (up, fwd, left)

# the scene option group is there and the list helpers work
s = scene.kat_outfits
s.skins.add().obj = arm_skin
s.items.add().obj = sleeve
assert outfits.outfit_objects(bpy.context) == [sleeve]

# Unity learns which shape to turn on when
side = outfits.sidecar(bpy.context, [arm_skin, sleeve])
assert side["skin"] == "Body" and side["skins"] == ["Body"] and side["outfits"] == [{"mesh": "Sleeve", "layer": 0}], side
assert {"mesh": "Body", "shape": "KAT.fit.Sleeve", "when": ["Sleeve"]} in side["fits"], side["fits"]
pair = sleeve.shape_key_add(name="KAT.fit.Other")
other_garment = sphere("Other", 1.0)
s.items.add().obj = other_garment
side = outfits.sidecar(bpy.context, [arm_skin, sleeve, other_garment])
assert {"mesh": "Sleeve", "shape": "KAT.fit.Other", "when": ["Sleeve", "Other"]} in side["fits"], side["fits"]
assert not outfits.sidecar(bpy.context, [arm_skin])["outfits"], "only exported meshes are listed"
assert outfits.is_clothing_name("Tank Top.001") and not outfits.is_clothing_name("Body")

# a head and a body that are separate meshes count as one skin
for ob in list(bpy.data.objects):
    bpy.data.objects.remove(ob)
head = sphere("Head", 1.0, (0, 0, 0))
torso = sphere("Torso", 1.0, (2.5, 0, 0))
cloth = sphere("Cloth", 1.0005, (0, 0, 0))
second = sphere("Second", 1.0005, (2.5, 0, 0))
with bpy.context.temp_override(active_object=cloth, object=cloth, selected_objects=[cloth, second],
                               selected_editable_objects=[cloth, second]):
    bpy.ops.object.join()  # one piece of clothing lying over both skin meshes
bpy.context.view_layer.update()
res = outfits.run_fit(bpy.context, [head, torso], [cloth], 0.002, "QUICK", 0.03)
assert {(r.garment, r.over) for r in res.rows} <= {("Head", "Cloth"), ("Torso", "Cloth")}, res.rows
assert outfits.fit_keys(head) == [outfits.FIT + "Cloth"] and outfits.fit_keys(torso) == [outfits.FIT + "Cloth"]
assert not outfits.fit_keys(cloth)
res = outfits.run_fit(bpy.context, [head, torso], [cloth], 0.002, "QUICK", 0.03, "PUSH")
assert not outfits.fit_keys(head) and not outfits.fit_keys(torso), "PUSH moves the clothes, not the skin"
assert outfits.fit_keys(cloth) == [outfits.FIT + "Skin"], outfits.fit_keys(cloth)  # one name for several skin meshes
try:
    outfits.run_fit(bpy.context, [], [cloth], 0.002, "QUICK", 0.03)
    raise AssertionError("no skin accepted")
except ValueError:
    pass
assert outfits.is_skin_name("Body") and outfits.is_skin_name("Head.001") and outfits.is_skin_name("hands")
assert not outfits.is_skin_name("Bodysuit") and not outfits.is_skin_name("ArmCover") and not outfits.is_skin_name("Necklace")
assert outfits.is_clothing_name("Leggings") and outfits.is_clothing_name("ArmCover")

kat.unregister()
print("OUTFITS OK")
