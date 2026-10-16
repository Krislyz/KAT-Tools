# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Krislyz
"""Merge, attach, separate. Run: blender -b --factory-startup --python tests/blender_custom.py"""
import os
import runpy
import sys

import bpy
from mathutils import Vector

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..")))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import strict  # noqa: E402,F401

ns = runpy.run_path(os.path.join(HERE, "blender_armature.py"))
from kat.modules import armature, custom  # noqa: E402

rig, body = ns["build"]()
ctx = bpy.context

# a second armature (a tail) whose root clashes by name with a bone in the first
tail_data = bpy.data.armatures.new("Tail")
tail = bpy.data.objects.new("Tail", tail_data)
ctx.scene.collection.objects.link(tail)
ctx.view_layer.objects.active = tail
bpy.ops.object.mode_set(mode="EDIT")
for name, parent, head, tail_pos in (("hair_01", None, (0, 0.1, 1.0), (0, 0.3, 1.0)), ("tip", "hair_01", (0, 0.3, 1.0), (0, 0.5, 1.0))):
    eb = tail_data.edit_bones.new(name)
    eb.head, eb.tail = head, tail_pos
    if parent:
        eb.parent = tail_data.edit_bones[parent]
bpy.ops.object.mode_set(mode="OBJECT")
fur = bpy.data.objects.new("Fur", bpy.data.meshes.new("Fur"))
ctx.scene.collection.objects.link(fur)
fur.data.from_pydata([(0, 0.1, 1.0), (0, 0.3, 1.0), (0, 0.5, 1.0)], [], [])
fur.vertex_groups.new(name="hair_01").add([0, 1], 1.0, "REPLACE")
fur.modifiers.new("Armature", "ARMATURE").object = tail

before = len(rig.data.bones)
moved = custom.merge_rigs(ctx, rig, tail, attach_to="mixamorig:Hips")
assert moved == 2 and len(rig.data.bones) == before + 2
assert "hair_01" in rig.data.bones, "base bone keeps its name"
clashed = next(b for b in rig.data.bones if b.name.startswith("hair_01.Tail"))
assert clashed.parent.name == "mixamorig:Hips", "other rig's root hangs off the chosen bone"
assert rig.data.bones["tip"].parent == clashed
assert "Tail" not in bpy.data.objects, "joined armature object is gone"
assert fur.modifiers[0].object == rig, "meshes of the merged rig now follow the base"
assert fur.vertex_groups.get(clashed.name) is not None, "vertex groups followed the renamed bone"

# similar bones are merged, not added twice: a Hips in both
pony_data = bpy.data.armatures.new("Pony")
pony = bpy.data.objects.new("Pony", pony_data)
ctx.scene.collection.objects.link(pony)
ctx.view_layer.objects.active = pony
bpy.ops.object.mode_set(mode="EDIT")
for name, parent, head, tail_pos in (("Hips", None, (0, 0, 1.0), (0, 0, 1.2)), ("ponytail", "Hips", (0, 0.1, 1.1), (0, 0.3, 1.1))):
    eb = pony_data.edit_bones.new(name)
    eb.head, eb.tail = head, tail_pos
    if parent:
        eb.parent = pony_data.edit_bones[parent]
bpy.ops.object.mode_set(mode="OBJECT")
strand = bpy.data.objects.new("Strand", bpy.data.meshes.new("Strand"))
ctx.scene.collection.objects.link(strand)
strand.data.from_pydata([(0, 0, 1.0), (0, 0.1, 1.1), (0, 0.3, 1.1)], [], [])
strand.vertex_groups.new(name="Hips").add([0, 1], 0.5, "REPLACE")
strand.vertex_groups.new(name="ponytail").add([1, 2], 1.0, "REPLACE")
strand.modifiers.new("Armature", "ARMATURE").object = pony
count = len(rig.data.bones)
assert "Hips" in custom.merge_pairs(rig, pony) and custom.merge_pairs(rig, pony)["Hips"] == "mixamorig:Hips"
added = custom.merge_rigs(ctx, rig, pony, similar=True)
assert added == 1 and len(rig.data.bones) == count + 1, (added, len(rig.data.bones) - count)
assert not any(b.name.startswith("Hips") for b in rig.data.bones), "no second Hips"
assert rig.data.bones["ponytail"].parent.name == "mixamorig:Hips", "what hung from the merged bone hangs from the base's"
names = {g.name for g in strand.vertex_groups}
assert names == {"mixamorig:Hips", "ponytail"}, names
w = {v.index: g.weight for v in strand.data.vertices for g in v.groups if strand.vertex_groups[g.group].name == "mixamorig:Hips"}
assert w == {0: 0.5, 1: 0.5}, w
assert strand.modifiers[0].object == rig

# the other armature's extra bones can be folded into the matching one instead
fold_data = bpy.data.armatures.new("Fold")
fold = bpy.data.objects.new("Fold", fold_data)
ctx.scene.collection.objects.link(fold)
ctx.view_layer.objects.active = fold
bpy.ops.object.mode_set(mode="EDIT")
for name, parent, head, tail_pos in (("Hips", None, (0, 0, 1.0), (0, 0, 1.2)), ("cape", "Hips", (0, 0.1, 1.1), (0, 0.3, 1.1))):
    eb = fold_data.edit_bones.new(name)
    eb.head, eb.tail = head, tail_pos
    if parent:
        eb.parent = fold_data.edit_bones[parent]
bpy.ops.object.mode_set(mode="OBJECT")
cloak = bpy.data.objects.new("Cloak", bpy.data.meshes.new("Cloak"))
ctx.scene.collection.objects.link(cloak)
cloak.data.from_pydata([(0, 0.1, 1.1), (0, 0.3, 1.1)], [], [])
cloak.vertex_groups.new(name="cape").add([0, 1], 1.0, "REPLACE")
cloak.modifiers.new("Armature", "ARMATURE").object = fold
count = len(rig.data.bones)
assert custom.merge_rigs(ctx, rig, fold, similar=True, keep_extra=False) == 0
assert len(rig.data.bones) == count and "cape" not in rig.data.bones
assert [g.name for g in cloak.vertex_groups] == ["mixamorig:Hips"], [g.name for g in cloak.vertex_groups]

def outfit_rig(name, bones):
    data = bpy.data.armatures.new(name)
    ob = bpy.data.objects.new(name, data)
    ctx.scene.collection.objects.link(ob)
    ctx.view_layer.objects.active = ob
    bpy.ops.object.mode_set(mode="EDIT")
    for bone, parent, head, tail in bones:
        eb = data.edit_bones.new(bone)
        eb.head, eb.tail = head, tail
        if parent:
            eb.parent = data.edit_bones[parent]
    bpy.ops.object.mode_set(mode="OBJECT")
    skin = bpy.data.objects.new(name + "Skin", bpy.data.meshes.new(name + "Skin"))
    ctx.scene.collection.objects.link(skin)
    skin.data.from_pydata([(0, 0, 1.0)] * len(bones), [], [])
    for i, (bone, *_) in enumerate(bones):
        skin.vertex_groups.new(name=bone).add([i], 1.0, "REPLACE")
    skin.modifiers.new("Armature", "ARMATURE").object = ob
    return ob, skin


# twist bones of an outfit go into the bone above when the avatar has none, and stay when asked; stray bones with
# nothing matching above them go to the closest bone of the avatar
rows = [("Hips", None, (0, 0, 1.0), (0, 0, 1.2)), ("sleeve_twist", "Hips", (0, 0, 1.2), (0, 0, 1.3)),
        ("ruffle", "sleeve_twist", (0, 0, 1.3), (0, 0, 1.4)), ("stray", None, (0, 0, 1.69), (0, 0, 1.75))]
count = len(rig.data.bones)
w_rig, w_skin = outfit_rig("Twisty", rows)
assert custom.merge_rigs(ctx, rig, w_rig, similar=True, keep_extra=True) == 2
assert "sleeve_twist" not in rig.data.bones and "ruffle" in rig.data.bones and "stray" in rig.data.bones
assert rig.data.bones["ruffle"].parent.name == "mixamorig:Hips"
assert {g.name for g in w_skin.vertex_groups} == {"mixamorig:Hips", "ruffle", "stray"}
rows_b = [(n if n == "Hips" else n + "_b", p if p is None or p == "Hips" else p + "_b", h, t) for n, p, h, t in rows]
w_rig, w_skin = outfit_rig("Twisty2", rows_b)
assert custom.merge_rigs(ctx, rig, w_rig, similar=True, keep_extra=True, keep_twist=True) == 3
assert "sleeve_twist_b" in rig.data.bones
rows_c = [(n if n == "Hips" else n + "_c", p if p is None or p == "Hips" else p + "_c", h, t) for n, p, h, t in rows]
w_rig, w_skin = outfit_rig("Twisty3", rows_c)
count = len(rig.data.bones)
assert custom.merge_rigs(ctx, rig, w_rig, similar=True, keep_extra=False) == 0 and len(rig.data.bones) == count
assert {g.name for g in w_skin.vertex_groups} == {"mixamorig:Hips", "stray"}, "the stray root went to the closest base bone"

# a hidden outfit mesh gets its weights too
rows_h = [(n if n == "Hips" else n + "_h", p if p is None or p == "Hips" else p + "_h", h, t) for n, p, h, t in rows]
h_rig, h_skin = outfit_rig("Hidden", rows_h)
h_skin.hide_viewport = True
ctx.view_layer.update()
assert not h_skin.visible_get()
custom.merge_rigs(ctx, rig, h_rig, similar=True, keep_extra=False)
assert {g.name for g in h_skin.vertex_groups} <= {b.name for b in rig.data.bones}, [g.name for g in h_skin.vertex_groups]
assert h_skin.modifiers[0].object == rig
assert sum(g.weight for v in h_skin.data.vertices for g in v.groups) > 3.99, "all four weights arrived"

# with Apply transforms ticked, and a mesh whose modifier still points at the merged armature
a_rig, a_skin = outfit_rig("Applied", [(n if n == "Hips" else n + "_a", p if p is None or p == "Hips" else p + "_a", h, t) for n, p, h, t in rows])
a_rig.scale = (0.85, 0.85, 0.85)
custom.merge_rigs(ctx, rig, a_rig, similar=True, keep_extra=False, apply=True)
assert {g.name for g in a_skin.vertex_groups} <= {b.name for b in rig.data.bones}, [g.name for g in a_skin.vertex_groups]
assert a_skin.modifiers[0].object == rig
assert [m.name for m in a_skin.modifiers] == [rig.name], "the modifier is named after the armature it went to"

# an outfit can be picked by its mesh: the armature it is rigged to is the one merged
m_rig, m_skin = outfit_rig("ByMesh", [("Hips", None, (0, 0, 1.0), (0, 0, 1.2)), ("bow", "Hips", (0, 0, 1.2), (0, 0, 1.3))])
from types import SimpleNamespace  # noqa: E402
fake = SimpleNamespace(scene=SimpleNamespace(kat_custom=SimpleNamespace(base=None, other=None)),
                       selected_objects=[rig, m_skin], active_object=rig)
assert custom.chosen_rigs(fake) == (rig, [m_rig])
fake.selected_objects = [rig]
assert custom.chosen_rigs(fake) == (rig, [])

ghost = bpy.data.objects.new("Ghost", bpy.data.armatures.new("Ghost"))  # not in the scene
for args in ((ghost, "nope"), (ghost, "")):
    try:
        custom.merge_rigs(ctx, rig, ghost, attach_to=args[1])
    except ValueError:
        pass
    else:
        raise AssertionError("bad merge request must be rejected before anything changes")

# attach a hat to the head bone and check it stays where it was
hat = bpy.data.objects.new("Hat", bpy.data.meshes.new("Hat"))
ctx.scene.collection.objects.link(hat)
hat.data.from_pydata([(0, 0, 1.7), (0.1, 0, 1.7), (0, 0.1, 1.8)], [], [(0, 1, 2)])
hat.location = (0.5, 0, 0)
ctx.view_layer.update()
world_before = [hat.matrix_world @ v.co for v in hat.data.vertices]
custom.attach_mesh(ctx, rig, hat, "mixamorig:Head")
ctx.view_layer.update()
world_after = [hat.matrix_world @ v.co for v in hat.data.vertices]
assert max((a - b).length for a, b in zip(world_before, world_after)) < 1e-5
assert [g.name for g in hat.vertex_groups] == ["mixamorig:Head"] and hat.parent == rig
assert hat.modifiers[0].object == rig
head_w = [v.groups[0].weight for v in hat.data.vertices]
assert head_w == [1.0, 1.0, 1.0]
assert hat in armature.meshes_of(rig, ctx.scene)

# separate by material: two quads with two materials become two objects
quad = bpy.data.objects.new("Quads", bpy.data.meshes.new("Quads"))
ctx.scene.collection.objects.link(quad)
quad.data.from_pydata([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0), (2, 0, 0), (3, 0, 0), (3, 1, 0), (2, 1, 0)], [],
                      [(0, 1, 2, 3), (4, 5, 6, 7)])
for name in ("MatA", "MatB"):
    quad.data.materials.append(bpy.data.materials.new(name))
quad.data.polygons[1].material_index = 1
new = custom.separate(ctx, quad, "MATERIAL")
assert len(new) == 1 and len(quad.data.polygons) == 1 and len(new[0].data.polygons) == 1
assert {quad.material_slots[0].material.name, new[0].material_slots[0].material.name} == {"MatA", "MatB"}

loose = bpy.data.objects.new("Loose", bpy.data.meshes.new("Loose"))
ctx.scene.collection.objects.link(loose)
loose.data.from_pydata([(0, 0, 0), (1, 0, 0), (1, 1, 0), (5, 5, 5), (6, 5, 5), (6, 6, 5)], [], [(0, 1, 2), (3, 4, 5)])
assert len(custom.separate(ctx, loose, "LOOSE")) == 1
print("CUSTOM OK")
