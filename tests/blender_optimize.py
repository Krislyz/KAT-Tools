# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Krislyz
"""Optimization: the scan, the checklist, joining that spares outfits, bone merging.
Run: blender -b --factory-startup --python tests/blender_optimize.py"""
import os
import sys

import bpy

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import strict  # noqa: E402,F401

import kat  # noqa: E402
from kat.core import compat  # noqa: E402
from kat.modules import armature, face, optimize, outfits  # noqa: E402

kat.register()
scene = bpy.context.scene

names = {"Hips": (0.9, 1.0, None), "Spine": (1.0, 1.2, "Hips"), "Chest": (1.2, 1.4, "Spine"),
         "Neck": (1.4, 1.5, "Chest"), "Head": (1.5, 1.7, "Neck")}
for i in range(1, 7):  # a hair chain of six loose bones
    names[f"Hair{i}"] = (1.7 + 0.1 * (i - 1), 1.7 + 0.1 * i, "Head" if i == 1 else f"Hair{i - 1}")
data = bpy.data.armatures.new("Rig")
rig = bpy.data.objects.new("Rig", data)
scene.collection.objects.link(rig)
bpy.context.view_layer.objects.active = rig
bpy.ops.object.mode_set(mode="EDIT")
for name, (z0, z1, parent) in names.items():
    eb = data.edit_bones.new(name)
    eb.head, eb.tail = (0, 0, z0), (0, 0, z1)
    if parent:
        eb.parent = data.edit_bones[parent]
bpy.ops.object.mode_set(mode="OBJECT")


def rigged(name, verts, groups, parent=rig):
    me = bpy.data.meshes.new(name)
    me.from_pydata(verts, [], [])
    ob = bpy.data.objects.new(name, me)
    scene.collection.objects.link(ob)
    ob.parent = parent
    mod = ob.modifiers.new("Armature", "ARMATURE")
    mod.object = rig
    for group, idx in groups.items():
        ob.vertex_groups.new(name=group).add(idx, 1.0, "REPLACE")
    return ob


hair = rigged("Hair", [(0, 0, 1.7 + 0.1 * i) for i in range(6)], {f"Hair{i + 1}": [i] for i in range(6)})
body = rigged("Body", [(0, 0, 0.1 * i) for i in range(4)], {"Hips": [0, 1], "Spine": [2, 3], "Unused": []})
shirt = rigged("Shirt", [(0, 0, 0.1 * i) for i in range(3)], {"Chest": [0, 1, 2]})
dup = bpy.data.materials.new("Cloth")
dup2 = bpy.data.materials.new("Cloth.001")
for ob in (body, shirt):
    ob.data.materials.append(dup2 if ob is shirt else dup)
shirt.data.materials.append(dup)  # an empty slot: no polygons use it
body.shape_key_add(name="Basis")
body.shape_key_add(name="does_nothing")
bpy.context.view_layer.update()

counts = optimize.scan(bpy.context, rig)
assert counts["keys"] == 1 and counts["groups"] == 1 and counts["materials"] >= 1, counts
assert counts["join"] == 2, counts  # Hair, Body and Shirt can all join: two fewer meshes
assert optimize.found["rig"] == "Rig"

# outfits stay apart
s = scene.kat_outfits
s.items.add().obj = shirt
assert optimize.scan(bpy.context, rig)["join"] == 1
o = scene.kat
o.opt_join = True
lines = optimize.optimize(bpy.context, rig, o)
assert any("Joined 2 meshes" in line for line in lines), lines
assert sorted(m.name for m in armature.meshes_of(rig)) == ["Body", "Shirt"] or \
    sorted(m.name for m in armature.meshes_of(rig)) == ["Hair", "Shirt"]
target = next(m for m in armature.meshes_of(rig) if m.name != "Shirt")
assert "does_nothing" not in (face.key_blocks(target) or ()), "the empty key went"
assert "Unused" not in target.vertex_groups

# bone merging keeps every weight, hands children to the parent, never touches humanoid bones
plan = ["Hair2", "Hair4", "Hair6"]


def total_weight():
    return round(sum(g.weight for m in armature.meshes_of(rig) for v in m.data.vertices for g in v.groups), 4)


before = total_weight()
assert optimize.merge_bones(bpy.context, rig, plan) == 3
assert total_weight() == before, (before, total_weight())
left = [b.name for b in rig.data.bones]
assert all(n not in left for n in plan) and all(n in left for n in ("Hips", "Head", "Hair1", "Hair3", "Hair5"))
assert rig.data.bones["Hair3"].parent.name == "Hair1", "the child moved up to the surviving parent"
assert all(b.parent is not None for b in rig.data.bones if b.name.startswith("Hair"))
assert "Hair2" not in target.vertex_groups, "merged bones leave no empty group behind"

# weights to the active bone, bones kept, and the Delete box
chains = optimize.loose_chains(rig)
assert list(chains) == ["Hair1"], list(chains)
rig.data.bones.active = rig.data.bones["Hair1"]
before = total_weight()
assert optimize.merge_bones(bpy.context, rig, ["Hair3"], into="ACTIVE", keep=True) == 1
assert total_weight() == before and "Hair3" in rig.data.bones and "Hair3" not in target.vertex_groups
rig.data.bones.active = None
try:
    optimize.merge_bones(bpy.context, rig, ["Hair5"], into="ACTIVE")
except ValueError:
    pass
else:
    raise AssertionError("merging into the active bone needs one")
con = rig.pose.bones["Hair5"].constraints.new("COPY_LOCATION")
o.opt_del_constraints, o.opt_del_bones, o.opt_del_groups = True, False, False
assert bpy.ops.kat.bones_clean() == {"FINISHED"} and not rig.pose.bones["Hair5"].constraints
o.opt_del_constraints, o.opt_del_groups = False, True
assert bpy.ops.kat.bones_clean() == {"FINISHED"}
assert bpy.ops.kat.bones_connect() == {"FINISHED"}

# presets tick the right steps and set the targets
from kat.modules import reduce  # noqa: E402
assert bpy.ops.kat.optimize_preset(kind="SAFE") == {"FINISHED"}
assert o.opt_keys and o.opt_groups and o.opt_materials and not o.opt_triangles and not o.opt_join
assert bpy.ops.kat.optimize_preset(kind="QUEST") == {"FINISHED"}
assert all(getattr(o, p) for p, _, _, _ in optimize.STEPS)
assert reduce.settings(bpy.context).budget == "QUEST_GOOD" and o.tex_limit == "512" and o.platform == "QUEST"
assert bpy.ops.kat.optimize_preset(kind="PC") == {"FINISHED"} and reduce.settings(bpy.context).budget == "GOOD" and o.platform == "PC"

# merge the selected bones into their parents (what the panel button does), in Object mode and in Edit mode
bpy.context.view_layer.objects.active = rig
compat.select_bones(rig, ["Hair5"])
before = total_weight()
assert bpy.ops.kat.merge_bones() == {"FINISHED"}
assert "Hair5" not in rig.data.bones and total_weight() == before
rig.data.bones.active = rig.data.bones["Hair1"]  # the active bone is merged too, it is not the target
compat.select_bones(rig, ["Hair1"])
assert bpy.ops.kat.merge_bones() == {"FINISHED"} and "Hair1" not in rig.data.bones and total_weight() == before
compat.select_bones(rig, ["Hips"])
try:
    bpy.ops.kat.merge_bones()
except RuntimeError:
    pass
else:
    raise AssertionError("a bone without a parent cannot merge")

# roots for groups of loose bones: skirt_222_2201.. under Hips become one chain-group under skirt_root_222_22
bpy.ops.object.mode_set(mode="EDIT")
for i in (1, 2, 3):
    eb = rig.data.edit_bones.new(f"skirt_222_220{i}")
    eb.head, eb.tail = (0.1 * i, 0, 0.9), (0.1 * i, 0, 0.7)
    eb.parent = rig.data.edit_bones["Hips"]
    sub = rig.data.edit_bones.new(f"skirt_222_220{i}_end")
    sub.head, sub.tail = eb.tail, (0.1 * i, 0, 0.5)
    sub.parent = eb
eb = rig.data.edit_bones.new("tail_1")  # alone under Hips: no group
eb.head, eb.tail = (0, 0.1, 0.9), (0, 0.1, 0.7)
eb.parent = rig.data.edit_bones["Hips"]
bpy.ops.object.mode_set(mode="OBJECT")
for name in [f"skirt_222_220{i}" for i in (1, 2, 3)] + ["tail_1"]:
    target.vertex_groups.new(name=name)  # a mesh weighted to them, so they count as deform bones
assert optimize.category_name(["skirt_222_2201", "skirt_222_2202", "skirt_222_2210"]) == "skirt_root_222_22"
assert optimize.category_name(["hair_a", "hair_b"]) == "hair_root"
found = optimize.categories(rig)
assert [(n, p, len(t)) for n, p, t in found] == [("skirt_root_222_220", "Hips", 3)], found
before = total_weight()
assert bpy.ops.kat.group_bones() == {"FINISHED"}
root = rig.data.bones["skirt_root_222_220"]
assert root.parent.name == "Hips" and not root.use_deform
assert all(rig.data.bones[f"skirt_222_220{i}"].parent == root for i in (1, 2, 3))
assert rig.data.bones["skirt_222_2201_end"].parent.name == "skirt_222_2201"
assert total_weight() == before and optimize.categories(rig) == [], "a second run finds nothing new"

# a hidden mesh keeps its weights too: they go to the parent, the bone goes
target.vertex_groups["skirt_222_2203"].add([0], 0.75, "REPLACE")
target.hide_viewport = True
bpy.context.view_layer.update()
assert not target.visible_get()
assert optimize.merge_bones(bpy.context, rig, ["skirt_222_2203"]) == 1
assert "skirt_222_2203" not in target.vertex_groups
root_group = target.vertex_groups["skirt_root_222_220"]
assert abs(root_group.weight(0) - 0.75) < 1e-5, "the hidden mesh's weight arrived"
target.hide_viewport = False

# the same from Edit mode: the selection there is the edit bones', and the armature is left in Edit mode
bpy.ops.object.mode_set(mode="EDIT")
for eb in rig.data.edit_bones:
    eb.select = eb.name == "skirt_222_2201_end"
assert bpy.ops.kat.merge_bones() == {"FINISHED"}
assert rig.mode == "EDIT" and "skirt_222_2201_end" not in rig.data.edit_bones
bpy.ops.object.mode_set(mode="OBJECT")

kat.unregister()
print("OPTIMIZE OK")
