# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Krislyz
"""Face module. Run: blender -b --factory-startup --python tests/blender_face.py"""
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
from kat.core import visemes  # noqa: E402
from kat.modules import armature, face  # noqa: E402

rig, ob = ns["build"]()
bpy.context.view_layer.objects.active = rig
armature.rename_bones(rig, "unity")

# three mouth shapes: each lifts a different vertex by a different amount
n = len(ob.data.vertices)
for name, vert, dz in (("あ", 0, 1.0), ("お", 1, 0.5), ("い", 2, 0.25)):
    k = ob.shape_key_add(name=name)
    k.data[vert].co = k.data[vert].co + Vector((0, 0, dz))

blocks = face.key_blocks(ob)
names = [b.name for b in blocks]
assert (visemes.guess(names, "a"), visemes.guess(names, "o"), visemes.guess(names, "ch")) == ("あ", "お", "い")

assert face.make_visemes(ob, "あ", "お", "い") == 15
assert all(v in blocks for v in visemes.NAMES)
aa, ou, ih, sil = blocks["vrc.v_aa"], blocks["vrc.v_ou"], blocks["vrc.v_ih"], blocks["vrc.v_sil"]
base = blocks[0]
assert abs(aa.data[0].co.z - (base.data[0].co.z + 1.0)) < 1e-5, "aa should equal the A shape"
assert abs(ou.data[1].co.z - (base.data[1].co.z + 0.5)) < 1e-5, "ou should equal the O shape"
assert abs(ih.data[2].co.z - (base.data[2].co.z + 0.225)) < 1e-5, "ih is 0.9 of the CH shape"
assert all((a.co - b.co).length < 1e-6 for a, b in zip(sil.data, base.data)), "sil is neutral"
assert all(blocks[v].value == 0.0 for v in visemes.NAMES), "viseme keys must start at 0"
face.make_visemes(ob, "あ", "お", "い")  # running twice overwrites, never duplicates
assert sum(1 for b in blocks if b.name.startswith("vrc.v_")) == 15
order = [b.name for b in blocks]
assert order[0] == "Basis" and order[1:16] == sorted(visemes.NAMES), order[:17]  # on top, a-b-c order, not VRChat's
assert order[1] == "vrc.v_aa" and order[2] == "vrc.v_ch", order[:4]

# the face mesh is the one called Body, else the one with the most shape keys
other = ob.copy()
other.data = ob.data.copy()
other.name = "Hair"
bpy.context.scene.collection.objects.link(other)
ob.name = "Body"
assert face.guess_face_mesh([other, ob]) is ob
other.shape_key_add(name="many1")
for i in range(40):
    other.shape_key_add(name=f"many{i}")
ob.name = "Skin"
assert face.guess_face_mesh([other, ob]) is other, "no Body: the mesh with the most shape keys"
ob.name = "Body"
assert face.guess_face_mesh([other, ob]) is ob, "Body wins even with fewer keys"
bpy.data.objects.remove(other)
assert face.guess_face_mesh([]) is None

try:
    face.make_visemes(ob, "あ", "nope", "い")
    raise AssertionError("missing source key must be rejected")
except ValueError:
    pass

# blink copies, empty keys are cleaned, vrc keys survive
face.copy_key(ob, "あ", "vrc.blink_left")
assert "vrc.blink_left" in blocks
ob.shape_key_add(name="does_nothing", from_mix=False)
removed = face.remove_empty_keys(ob)
assert removed == 1 and "does_nothing" not in blocks and "vrc.v_sil" in blocks, removed

# eye bones from vertex groups
for gname, xs in (("eyeL", range(0, 2)), ("eyeR", range(6, 8))):
    g = ob.vertex_groups.new(name=gname)
    g.add(list(xs), 1.0, "REPLACE")
bpy.context.view_layer.objects.active = ob
name = face.make_eye_bone(bpy.context, rig, ob, "eyeL", "LeftEye")
assert name == "LeftEye" and "LeftEye" in rig.data.bones and "LeftEye" in ob.vertex_groups
assert rig.data.bones["LeftEye"].parent.name == "Head"
face.make_eye_bone(bpy.context, rig, ob, "eyeR", "RightEye")
best, _ = armature.map_bones(rig)
assert best["LeftEye"] == "LeftEye" and best["RightEye"] == "RightEye"
# face tracking shapes from an ARKit-style set
rig2, ob2 = ns["build"]()
for name, vert, vec in (("jawOpen", 0, (0, 0, 0.4)), ("mouthSmileLeft", 1, (0.3, 0, 0)), ("mouthFunnel", 2, (0, -0.8, 0)),
                        ("eyeBlinkLeft", 3, (0, 0, -0.2))):
    k = ob2.shape_key_add(name=name, from_mix=False)
    k.data[vert].co = k.data[vert].co + Vector(vec)
    k.value = 0.0
blocks2 = face.key_blocks(ob2)
originals = {b.name: [tuple(p.co) for p in b.data] for b in blocks2}
res = face.create_facetrack(ob2)
assert {"JawOpen", "EyeClosedLeft", "MouthCornerPullLeft", "MouthCornerSlantLeft", "LipFunnelUpperRight"} <= set(res.made), res.made
assert all(blocks2[n].value == 0.0 for n in res.made), "new keys must start at 0"
assert all(originals[n] == [tuple(p.co) for p in blocks2[n].data] for n in originals), "source shapes must stay untouched"


def move(name, vert):
    return blocks2[name].data[vert].co - blocks2[0].data[vert].co


assert (move("JawOpen", 0) - Vector((0, 0, 0.4))).length < 1e-5
pull, slant = move("MouthCornerPullLeft", 1), move("MouthCornerSlantLeft", 1)
assert ((pull + slant) - Vector((0.3, 0, 0))).length < 1e-5, "both halves together reproduce the smile"
funnel = sum((move(n, 2) for n in ("LipFunnelUpperRight", "LipFunnelUpperLeft", "LipFunnelLowerRight", "LipFunnelLowerLeft")), Vector())
assert (funnel - Vector((0, -0.8, 0))).length < 1e-5, "four funnel shapes add up to the source"

again = face.create_facetrack(ob2)
assert again.made == [] and len(again.skipped) == len(res.made), "existing shapes are kept by default"
redo = face.create_facetrack(ob2, overwrite=True)
assert len(redo.made) == len(res.made)
filled = face.create_facetrack(ob2, fill_empty=True)
unified = [n for n in blocks2.keys() if n in ft.UNIFIED] if (ft := __import__("kat.core.facetrack", fromlist=["x"])) else []
assert len(unified) == len(ft.UNIFIED) - len(ft.OPTIONAL), (len(unified), len(ft.UNIFIED))
assert face.remove_empty_keys(ob2) == 0, "face tracking keys survive the empty-key cleanup"
plain = bpy.data.objects.new("Plain", bpy.data.meshes.new("Plain"))
bpy.context.scene.collection.objects.link(plain)
plain.data.from_pydata([(0, 0, 0)], [], [])
plain.shape_key_add(name="Basis")
plain.shape_key_add(name="sculpt1")
nothing = face.create_facetrack(plain)
assert nothing.made == [] and nothing.skipped == []
print("FACE OK")
