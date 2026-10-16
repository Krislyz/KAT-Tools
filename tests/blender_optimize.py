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
plan = optimize.merge_plan(rig, 0.5)
assert plan == ["Hair2", "Hair4", "Hair6"], plan
assert optimize.merge_plan(rig, 0.0) == [] and len(optimize.merge_plan(rig, 1.0)) == 6


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

# one chain at a time, weights to the active bone, bones kept, and the Delete box
chains = optimize.merge_chains(rig)
assert list(chains) == ["Hair1"], list(chains)
assert optimize.merge_plan(rig, 1.0, "Hair1") == optimize.merge_plan(rig, 1.0) and optimize.merge_plan(rig, 1.0, "Nope") == []
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

# the Chain menu: Blender asks for its items at odd moments, so the callback only returns a list that exists, and a list
# that gets replaced stays alive (Blender keeps reading the strings of the old one)
import gc  # noqa: E402
from kat.modules import options  # noqa: E402
bpy.context.view_layer.objects.active = rig
before = options.chain_items()
assert before[0][0] == "ALL"
options.refresh_chains(bpy.context)
after = options.chain_items()
assert any(i[0] != "ALL" for i in after), "the hair chain of the test rig is listed"
assert before is after or before in options._replaced, "a replaced list is kept alive"
for _ in range(50):
    o.opt_merge_chain = after[-1][0]
    gc.collect()
    assert o.opt_merge_chain == after[-1][0]
    options.refresh_chains(bpy.context)
assert len(options._replaced) <= 256

kat.unregister()
print("OPTIMIZE OK")
