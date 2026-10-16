# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Krislyz
"""Refit with awkward input, and the layer, band, rigid and cloth steps on their own.
Run: blender -b --factory-startup --python tests/blender_refit_hard.py"""
import os
import sys

import bpy
import numpy as np
from mathutils import Matrix

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import strict  # noqa: E402,F401

import kat  # noqa: E402
from kat.modules import outfits, refit  # noqa: E402

BONES = ["Hips", "Spine", "Chest", "Neck", "Head"]


registered = []


def fresh():
    if registered:
        kat.unregister()
        registered.clear()
    bpy.ops.wm.read_factory_settings(use_empty=True)
    kat.register()
    registered.append(True)


def make_rig(name, z0, height):
    scene = bpy.context.scene
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


def tube(name, radius, z0, z1, rig, rings=20, around=20, weights=True, extra=None):
    verts = [(radius * np.cos(t), radius * np.sin(t), z0 + (z1 - z0) * r / (rings - 1))
             for r in range(rings) for t in np.linspace(0, 2 * np.pi, around, endpoint=False)]
    faces = [(r * around + a, r * around + (a + 1) % around, (r + 1) * around + (a + 1) % around, (r + 1) * around + a)
             for r in range(rings - 1) for a in range(around)]
    if extra:
        verts, faces = extra(verts, faces)
    me = bpy.data.meshes.new(name)
    me.from_pydata(verts, [], faces)
    ob = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(ob)
    arm = rig.data
    if weights:
        groups = {b: ob.vertex_groups.new(name=b) for b in BONES}
        tails = np.array([arm.bones[b].tail_local[2] for b in BONES])
        for v in me.vertices:
            k = int(np.clip(np.searchsorted(tails, v.co.z), 0, len(BONES) - 1))
            groups[BONES[k]].add([v.index], 1.0, "REPLACE")
    ob.parent = rig
    ob.modifiers.new("Armature", "ARMATURE").object = rig
    return ob


def scene_with(*builders, skin_radius=0.15):
    body_rig = make_rig("Body Rig", 0.8, 1.0)
    skin = tube("Skin", skin_radius, 0.82, 1.78, body_rig)
    outfit_rig = make_rig("Outfit Rig", 1.0, 1.4)
    garments = [b(outfit_rig) for b in builders]
    bpy.context.view_layer.update()
    outfits.settings(bpy.context).skins.add().obj = skin
    return body_rig, skin, outfit_rig, garments


def sound(ob):
    co = refit.coords(ob.data)
    assert np.isfinite(co).all(), f"{ob.name} has NaN or inf coordinates"
    assert not [m for m in ob.modifiers if m.type == "CLOTH"], f"{ob.name} kept a cloth modifier"
    assert not [g for g in ob.vertex_groups if g.name == "KAT pin"], f"{ob.name} kept the pin group"


def left_clean(skin, scene):
    assert not [m for m in skin.modifiers if m.type == "COLLISION"], "the skin kept a collider"
    assert (scene.frame_current, scene.frame_start, scene.frame_end) == (1, 1, 250), (scene.frame_current, scene.frame_start, scene.frame_end)


# ---- awkward garments, one at a time and all together ---------------------------------------------------------------
def with_loose(verts, faces):  # loose vertices without edges, and a quad of its own
    n = len(verts)
    verts = verts + [(0.5, 0.5, 1.2), (0.6, 0.5, 1.2), (-0.5, 0.2, 1.0)]
    quad = [(0.3, 0.3, 1.1), (0.32, 0.3, 1.1), (0.32, 0.32, 1.1), (0.3, 0.32, 1.1)]
    return verts + quad, faces + [(n + 3, n + 4, n + 5, n + 6)]


cases = {
    "loose pieces": lambda rig: tube("Loose", 0.12, 1.1, 2.0, rig, extra=with_loose),
    "no weights": lambda rig: tube("Bare", 0.12, 1.1, 2.0, rig, weights=False),
    "tiny": lambda rig: tube("Tiny", 0.12, 1.2, 1.25, rig, rings=3, around=6),
    "scaled and turned": lambda rig: tube("Turned", 0.12, 1.1, 2.0, rig),
    "hidden": lambda rig: tube("Hidden", 0.12, 1.1, 2.0, rig),
}
for name, build in cases.items():
    fresh()
    body_rig, skin, outfit_rig, garments = scene_with(build, lambda rig: tube("Shirt", 0.12, 1.1, 2.0, rig))
    odd = garments[0]
    if name == "scaled and turned":
        odd.matrix_world = Matrix.Translation((0.0, 0.0, 0.0)) @ Matrix.Rotation(0.5, 4, "Z") @ Matrix.Scale(0.9, 4)
    if name == "hidden":
        odd.hide_viewport = True
    done = refit.refit_outfit(bpy.context, outfit_rig, [skin], body_rig, garments, finish=False)
    assert len(done.rows) == 2, (name, done.rows)
    for g in garments:
        sound(g)
    left_clean(skin, bpy.context.scene)
    print("  ok:", name)

# Keep the original off: no key is made
fresh()
body_rig, skin, outfit_rig, garments = scene_with(lambda rig: tube("Shirt", 0.12, 1.1, 2.0, rig))
refit.refit_outfit(bpy.context, outfit_rig, [skin], body_rig, garments, finish=False, keep_original=False)
assert garments[0].data.shape_keys is None, "nothing kept when it is switched off"
print("  ok: keep the original, switched off")

# a mesh without any faces, and one with a single triangle
fresh()
body_rig, skin, outfit_rig, garments = scene_with(lambda rig: tube("Shirt", 0.12, 1.1, 2.0, rig))
points = bpy.data.objects.new("Points", bpy.data.meshes.new("Points"))
points.data.from_pydata([(0.1 * i, 0, 1.2) for i in range(8)], [], [])
bpy.context.scene.collection.objects.link(points)
points.parent = outfit_rig
refit.refit_outfit(bpy.context, outfit_rig, [skin], body_rig, [garments[0], points], finish=False)
for g in (garments[0], points):
    sound(g)
print("  ok: faces-less mesh")

# ---- settings at their ends, cloth off, and a second outfit over the first ------------------------------------------
for label, kwargs in (("cloth off", dict(cloth=False)), ("soft", dict(stiffness=1.0)), ("rigid", dict(stiffness=5000.0)),
                      ("thick", dict(gap=30.0)), ("flat", dict(gap=0.0, tighten=0.0)), ("tight", dict(tighten=1.0, reach=200.0))):
    fresh()
    body_rig, skin, outfit_rig, garments = scene_with(lambda rig: tube("Shirt", 0.12, 1.1, 2.0, rig))
    done = refit.refit_outfit(bpy.context, outfit_rig, [skin], body_rig, garments, finish=False, **kwargs)
    sound(garments[0])
    left_clean(skin, bpy.context.scene)
    co = refit.coords(garments[0].data)
    assert co[:, 2].min() > 0.7 and co[:, 2].max() < 2.0, (label, co[:, 2].min(), co[:, 2].max())
    print("  ok:", label)

# the three modes: each one finishes, none leaves cloth in the body, and the thorough ones cut the cloth finer
counts = {}
for mode in ("FAST", "BALANCED", "ACCURATE", "no such mode"):
    fresh()
    body_rig, skin, outfit_rig, garments = scene_with(lambda rig: tube("Shirt", 0.12, 1.1, 2.0, rig))
    done = refit.refit_outfit(bpy.context, outfit_rig, [skin], body_rig, garments, finish=False, mode=mode)
    sound(garments[0])
    body = refit.Body(bpy.context, [skin], body_rig)
    offs = np.array([body.query(p)[3] for p in refit.coords(garments[0].data)])
    assert offs.min() > 0.0005, (mode, offs.min())
    counts[mode] = len(garments[0].data.vertices)
assert counts["BALANCED"] <= counts["ACCURATE"], counts  # Fast cuts the cloth where it must; the others lay it over the body smoothly first and may need fewer cuts
assert counts["no such mode"] == counts["BALANCED"], "an unknown mode is the everyday one"
print("  ok: the three modes", counts)

fresh()
body_rig, skin, outfit_rig, garments = scene_with(lambda rig: tube("Shirt", 0.12, 1.1, 2.0, rig))
refit.refit_outfit(bpy.context, outfit_rig, [skin], body_rig, garments, finish=("list", "shapes", "on"))
second_rig = make_rig("Jacket Rig", 1.0, 1.4)
jacket = tube("Jacket", 0.13, 1.1, 2.0, second_rig)
bpy.context.view_layer.update()
done = refit.refit_outfit(bpy.context, second_rig, [skin], body_rig, [jacket], finish=("list", "shapes", "on"))
sound(jacket)
print("  ok: a second outfit over the first")

# a polish step that fails must not take the fit down with it
fresh()
body_rig, skin, outfit_rig, garments = scene_with(lambda rig: tube("Shirt", 0.12, 1.1, 2.0, rig), lambda rig: tube("Jacket", 0.14, 1.1, 2.0, rig))
originals = (refit.cloth_polish, refit.keep_apart, refit.keep_over, refit.tucked_under)


def broken(*args, **kwargs):
    raise RuntimeError("on purpose")


refit.cloth_polish = refit.keep_apart = refit.keep_over = refit.tucked_under = broken
try:
    messages = []
    done = refit.refit_outfit(bpy.context, outfit_rig, [skin], body_rig, garments, finish=False, log=messages.append)
finally:
    refit.cloth_polish, refit.keep_apart, refit.keep_over, refit.tucked_under = originals
assert len(done.rows) == 2 and sum("skipped: on purpose" in m for m in messages) >= 4, messages
for g in garments:
    sound(g)
print("  ok: a failing polish step leaves the fit standing")

# ---- bands, small pieces and what is rigid ---------------------------------------------------------------------------
fresh()
body_rig = make_rig("Body Rig", 0.8, 1.0)
big = tube("Big", 0.12, 1.0, 2.0, body_rig, rings=30, around=30)


def with_pieces(verts, faces):
    n = len(verts)
    ring = []
    for t in np.linspace(0, 2 * np.pi, 40, endpoint=False):  # a ring 2 cm tall, round the middle: a belt
        ring += [(0.14 * np.cos(t), 0.14 * np.sin(t), 1.5), (0.14 * np.cos(t), 0.14 * np.sin(t), 1.52)]
    ring_faces = [(n + 2 * i, n + 2 * ((i + 1) % 40), n + 2 * ((i + 1) % 40) + 1, n + 2 * i + 1) for i in range(40)]
    m = n + len(ring)
    bit = [(0.2, 0.0, 1.5), (0.21, 0.0, 1.5), (0.21, 0.0, 1.51), (0.2, 0.0, 1.51)]
    return verts + ring + bit, faces + ring_faces + [(m, m + 1, m + 2, m + 3)]


piece = tube("Pieces", 0.12, 1.0, 2.0, body_rig, rings=30, around=30, extra=with_pieces)
a, b = refit.garment_edges(piece)
label = refit.core.components(len(piece.data.vertices), a, b)
small, band = refit.loose_pieces(refit.coords(piece.data), label)
assert small.sum() == 1 and band.sum() == 1 and not (small & band).any(), (small, band, np.bincount(label))
big_piece = np.argmax(np.bincount(label))
assert not small[big_piece] and not band[big_piece], "the garment itself is neither"
print("  ok: bands and small pieces told apart")

# ---- garments do not overlap -----------------------------------------------------------------------------------------
fresh()
body_rig, skin, outfit_rig, garments = scene_with(lambda rig: tube("Under", 0.2, 1.0, 1.6, rig), lambda rig: tube("Over", 0.2, 1.0, 1.6, rig))
under, over = garments
# squeeze the outer one into the inner one, and tuck a hem of it in
co = refit.coords(over.data)
radial = np.hypot(co[:, 0], co[:, 1])
co[:, 0] *= 0.19 / radial
co[:, 1] *= 0.19 / radial
refit.set_coords(over.data, co)
over.data.update()
tris = outfits.triangles_of(under)
bvh = outfits.build_bvh(refit.coords(under.data).astype(np.float32), tris)
pushed = refit.keep_apart(over, {}, {"Under": bvh})
after = refit.coords(over.data)
assert pushed > 0 and np.hypot(after[:, 0], after[:, 1]).min() > 0.2, (pushed, np.hypot(after[:, 0], after[:, 1]).min())
print("  ok: an outer garment is pushed out of the one under it")

# a rigid piece lifted over what is under it moves as one block and keeps its shape
tie = tube("TieBit", 0.02, 1.2, 1.5, outfit_rig, rings=6, around=8)
refit.set_coords(tie.data, refit.coords(tie.data) + np.array([0.19, 0.0, 0.0]))  # against the wall of the garment under it
x0 = refit.coords(tie.data)
body = refit.Body(bpy.context, [skin], body_rig)
lift = refit.lift_rigid(tie, body, bvh)
x1 = refit.coords(tie.data)
assert lift > 0 and np.allclose((x1 - x0) - (x1 - x0)[0], 0.0, atol=1e-5), "one block, not vertex by vertex"
print("  ok: a rigid piece is lifted as one block")

# the cloth step on its own never leaves a modifier or a group, and never makes coordinates up
tube_ob = tube("Plain", 0.17, 1.0, 1.6, outfit_rig)
bpy.context.view_layer.update()
res = refit.cloth_polish(bpy.context, tube_ob, body, 0.002)
assert hasattr(res, "used") and res.far >= 0.0 and 0.0 <= res.jitter <= 1.0 and 0.0 <= res.flips <= 1.0, vars(res)
sound(tube_ob)
left_clean(skin, bpy.context.scene)
print("  ok: the cloth step cleans up after itself")

kat.unregister()
print("REFIT HARD OK")
