# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Krislyz
"""Outfits: the clothes that toggle on and off, ordered from the layer next to the skin outwards, and the
anti-clip tool that gives each one "fit" shape keys so layers never poke through each other or the skin.

How the fit works: the avatar is put through a set of poses (arms down, elbows bent, squat, bend ...). In every pose
each garment's vertices are checked against the skin and the garments under it. A vertex that is closer than the gap,
or is inside, needs to move out; the worst case over all poses is kept. That push is spread over the neighbouring
vertices so the cloth stays smooth, and written to shape keys named KAT.fit.<what it clears>. Switch the keys on
when both pieces are worn. Nothing else on the mesh is touched, and the pose is put back when it is done.
"""
import math
import re
import time
from types import SimpleNamespace

import bpy
import numpy as np
from bpy.props import CollectionProperty, EnumProperty, FloatProperty, IntProperty, PointerProperty
from mathutils import Matrix, Vector
from mathutils.bvhtree import BVHTree

from ..core import geom
from ..core.safe import invoke_guard, operator_guard
from . import armature, face, uiutil

FIT = "KAT.fit."
MIN_PUSH = 0.0005  # metres: less than this and two layers do not touch, so no key is made
NAME_LIMIT = 63
SIDED = {"UpperArm", "LowerArm", "UpperLeg", "LowerLeg"}
CLOTHES_WORDS = ("shirt", "top", "tank", "jacket", "coat", "hoodie", "sweater", "pants", "trouser", "shorts", "skirt",
                 "dress", "cloth", "outfit", "uniform", "socks", "stocking", "tights", "shoe", "boot", "glove",
                 "scarf", "bra", "bikini", "swimsuit", "kimono", "cape", "vest", "belt", "tie", "sleeve", "legging", "cover",
                 "suit", "sock", "necklace")

# (name, [(bone key, goal, degrees, mode)]). "to": end up this many degrees away from the goal direction. "by": turn
# this many degrees towards the goal. Every pose is applied to both sides at once.
BASE_POSES = [
    ("arms down", [("UpperArm", "down", 12, "to")]),
    ("arms up", [("UpperArm", "up", 15, "to")]),
    ("arms forward", [("UpperArm", "fwd", 8, "to")]),
    ("arms back", [("UpperArm", "back", 45, "by")]),
    ("arms twisted", [("UpperArm", "twist", 60, "by")]),
    ("elbows bent", [("LowerArm", "fwd", 130, "by")]),
    ("arms down, elbows bent", [("UpperArm", "down", 12, "to"), ("LowerArm", "fwd", 110, "by")]),
    ("arms forward, elbows bent", [("UpperArm", "fwd", 8, "to"), ("LowerArm", "in", 120, "by")]),
    ("legs forward", [("UpperLeg", "fwd", 10, "to")]),
    ("legs out", [("UpperLeg", "out", 50, "by")]),
    ("legs back", [("UpperLeg", "back", 35, "by")]),
    ("legs crossed", [("UpperLeg", "in", 25, "by")]),
    ("knees bent", [("LowerLeg", "back", 120, "by")]),
    ("bend forward", [("Spine", "fwd", 18, "by"), ("Chest", "fwd", 18, "by"), ("UpperChest", "fwd", 9, "by")]),
    ("lean back", [("Spine", "back", 10, "by"), ("Chest", "back", 10, "by")]),
    ("lean left", [("Spine", "left", 12, "by"), ("Chest", "left", 12, "by")]),
    ("lean right", [("Spine", "right", 12, "by"), ("Chest", "right", 12, "by")]),
    ("twist left", [("Spine", "twist", 15, "by"), ("Chest", "twist", 15, "by")]),
    ("twist right", [("Spine", "twist", -15, "by"), ("Chest", "twist", -15, "by")]),
    ("head forward", [("Neck", "fwd", 25, "by"), ("Head", "fwd", 15, "by")]),
    ("head back", [("Neck", "back", 20, "by"), ("Head", "back", 15, "by")]),
    ("head sideways", [("Neck", "left", 25, "by"), ("Head", "left", 15, "by")]),
    ("head turned", [("Neck", "twist", 40, "by"), ("Head", "twist", 30, "by")]),
]
MIXED_POSES = [
    ("squat", [("UpperLeg", "fwd", 10, "to"), ("LowerLeg", "back", 130, "by"), ("Spine", "fwd", 10, "by")]),
    ("sitting", [("UpperLeg", "fwd", 10, "to"), ("LowerLeg", "back", 90, "by"), ("Spine", "fwd", 8, "by")]),
    ("reaching up", [("UpperArm", "up", 15, "to"), ("Spine", "back", 8, "by"), ("Chest", "back", 8, "by")]),
    ("hands to chest", [("UpperArm", "down", 40, "to"), ("LowerArm", "fwd", 140, "by")]),
    ("deep bend", [("Spine", "fwd", 30, "by"), ("Chest", "fwd", 25, "by"), ("UpperArm", "down", 12, "to")]),
    ("stride", [("UpperLeg", "fwd", 40, "to"), ("LowerLeg", "back", 60, "by"), ("UpperArm", "back", 35, "by")]),
]
SKIN_MODES = [
    ("SHRINK", "Shrink skin", "Move the skin in under each piece of clothing. Invisible, and the clothes keep their shape"),
    ("PUSH", "Push clothes out", "Move the clothes away from the skin instead. Can make them look puffier"),
]
QUALITY = [
    ("REST", "Rest", "Only the pose it is in now"),
    ("QUICK", "Quick", "Around 23 poses"),
    ("NORMAL", "Normal", "Around 29 poses, including combined ones like sitting and squatting"),
    ("THOROUGH", "Thorough", "Around 60 poses, with half-way positions too. Slower"),
]

last_fit = {"rows": []}  # what the panel shows after the last run: (garment, shape, millimetres)


class KAT_OutfitItem(bpy.types.PropertyGroup):
    obj: PointerProperty(type=bpy.types.Object, name="Mesh")


def _is_mesh(self, ob):
    return ob.type == "MESH"


class KAT_OutfitSettings(bpy.types.PropertyGroup):
    items: CollectionProperty(type=KAT_OutfitItem)
    index: IntProperty()
    skins: CollectionProperty(type=KAT_OutfitItem)  # head, body, hands ...: measured together as one skin
    skin_index: IntProperty()
    gap: FloatProperty(name="Gap", default=2.0, min=0.0, max=30.0, precision=1, subtype="NONE",
                       description="Millimetres of room kept between layers")
    quality: EnumProperty(name="Poses", items=QUALITY, default="NORMAL")
    skin_mode: EnumProperty(name="Skin", items=SKIN_MODES, default="SHRINK")
    max_push: FloatProperty(name="Limit", default=10.0, min=1.0, max=100.0, precision=0,
                            description="The most any vertex may move, in millimetres. Clipping deeper than this is "
                                        "a weight problem, so it is left alone instead of puffing the cloth up")


def settings(context):
    return context.scene.kat_outfits


def outfit_objects(context):
    """The listed meshes in layer order, inner first. Entries whose object is gone are skipped."""
    return [i.obj for i in settings(context).items if i.obj is not None and i.obj.type == "MESH"]


def fit_keys(ob):
    blocks = face.key_blocks(ob)
    return [b.name for b in blocks if b.name.startswith(FIT)] if blocks else []


def skin_objects(context):
    return [i.obj for i in settings(context).skins if i.obj is not None and i.obj.type == "MESH"]


SKIN_WORDS = {"skin", "body", "head", "face", "hand", "hands", "foot", "feet"}


def is_skin_name(name):
    """Whole words only, so Bodysuit or ArmCover are not mistaken for the body."""
    words = {w.lower() for w in re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])", name)}
    return bool(words & SKIN_WORDS) and not is_clothing_name(name)


def is_clothing_name(name):
    low = name.lower()
    return any(word in low for word in CLOTHES_WORDS)


# ---- geometry ---------------------------------------------------------------------------------------------------

def basis_local(ob):
    blocks = face.key_blocks(ob)
    if blocks:
        return face._coords(blocks[0]).reshape(-1, 3)
    out = np.empty(len(ob.data.vertices) * 3, dtype=np.float32)
    ob.data.vertices.foreach_get("co", out)
    return out.reshape(-1, 3)


def normals_local(ob):
    out = np.empty(len(ob.data.vertices) * 3, dtype=np.float32)
    ob.data.vertex_normals.foreach_get("vector", out)
    return out.reshape(-1, 3)


def edges_of(ob):
    out = np.empty(len(ob.data.edges) * 2, dtype=np.int32)
    ob.data.edges.foreach_get("vertices", out)
    return out[0::2], out[1::2]


def triangles_of(ob):
    me = ob.data
    me.calc_loop_triangles()
    out = np.empty(len(me.loop_triangles) * 3, dtype=np.int32)
    me.loop_triangles.foreach_get("vertices", out)
    return out.reshape(-1, 3)


def snapshot(ob, depsgraph):
    """World-space positions and vertex normals of the object as it looks right now (pose, shape keys and all)."""
    ev = ob.evaluated_get(depsgraph)
    me = ev.to_mesh()
    try:
        n = len(me.vertices)
        if n != len(ob.data.vertices):
            raise ValueError(f"{ob.name}: modifiers change the vertex count. Apply them first")
        co = np.empty(n * 3, dtype=np.float32)
        nr = np.empty(n * 3, dtype=np.float32)
        me.vertices.foreach_get("co", co)
        me.vertex_normals.foreach_get("vector", nr)
    finally:
        ev.to_mesh_clear()
    m = np.array(ob.matrix_world, dtype=np.float64)
    rot = m[:3, :3]
    co_w = co.reshape(-1, 3) @ rot.T + m[:3, 3]
    nr_w = nr.reshape(-1, 3) @ np.linalg.inv(rot)  # normals turn with the inverse transpose
    nr_w /= np.maximum(np.linalg.norm(nr_w, axis=1, keepdims=True), 1e-12)
    return co_w.astype(np.float32), nr_w.astype(np.float32)


def build_bvh(co, tris):
    return BVHTree.FromPolygons(co.tolist(), tris.tolist(), all_triangles=True)


COVER_REACH = 0.1  # metres: how far from a vertex a layer may be and still count as covering it
POKE_REACH = 0.02  # metres: a vertex this close outside a layer, next to covered ones, has already come through it


def grow(mask, a, b, hops):
    for _ in range(hops):
        nxt = mask.copy()
        nxt[a] |= mask[b]
        nxt[b] |= mask[a]
        mask = nxt
    return mask


def facing(hn, idx, ref):
    """The hit surface's normal turned to agree with its layer's outward vertex normals. Triangle winding cannot be
    trusted: double-sided cloth has an inner shell that faces the body."""
    nr, tris = ref
    t = tris[idx]
    s = nr[t[0]] + nr[t[1]] + nr[t[2]]
    return hn if hn[0] * s[0] + hn[1] * s[1] + hn[2] * s[2] >= 0.0 else -hn


def layer_flips(co, nr, edges, bvh, ref, hops=2):
    """+1 or -1 per vertex of a garment: -1 where its normal points into the body. Imported clothes often come with
    a shell turned inside out (double-sided cloth). Judged against the skin at rest, and smoothed over the mesh so a
    vertex touching the skin does not decide alone."""
    score = np.zeros(len(co))
    for k in range(len(co)):
        loc, hn, idx, dist = bvh.find_nearest(Vector(co[k].tolist()))
        if loc is None:
            continue
        hn = facing(hn, idx, ref)
        score[k] = (nr[k][0] * hn[0] + nr[k][1] * hn[1] + nr[k][2] * hn[2]) / (1.0 + dist / 0.02)
    a, b = edges
    for _ in range(hops):
        nxt = score.copy()
        np.add.at(nxt, a, score[b])
        np.add.at(nxt, b, score[a])
        score = nxt
    return np.where(score < 0.0, -1.0, 1.0)


def covered(co, nr, bvh, ref_box, side, edges, ref, hops=2):
    """Which vertices of a layer sit under (side -1) or over (side +1) the reference layer, judged in the pose the
    arrays are in: a ray from the vertex, towards the reference layer, meets its inside (under) or outside (over).
    Only covered vertices are ever moved: a hand that happens to touch a shirt in some pose, or a necklace lying on top of it, is not clothing's
    business. A vertex that has already come through the layer at rest still counts when it hangs together with
    covered ones."""
    lo, hi = ref_box
    near = np.nonzero(np.all((co >= lo - COVER_REACH) & (co <= hi + COVER_REACH), axis=1))[0]
    inside = np.zeros(len(co), dtype=bool)
    through = np.zeros(len(co), dtype=bool)
    for k in near:
        p = Vector(co[k].tolist())
        d = Vector(nr[k].tolist()) * -side  # towards the reference layer
        hit, hn, idx, _ = bvh.ray_cast(p + d * 1e-5, d, COVER_REACH)
        if hit is not None and facing(hn, idx, ref).dot(d) * side < 0.0:
            inside[k] = True  # a necklace lying on a shirt meets it from the wrong side, so it is not covered by it
            continue
        hit, hn, idx, _ = bvh.ray_cast(p - d * 1e-5, -d, POKE_REACH)
        if hit is not None and facing(hn, idx, ref).dot(-d) * side > 0.0:
            through[k] = True
    if not inside.any():  # nothing is under it, so it has sunk into the reference layer as a whole
        return through
    a, b = edges
    return inside | (grow(inside, a, b, hops) & through)


def room_of(co, nr, bvh, side, mask, reach, behind):
    """Metres of free room between each masked vertex and the reference layer, measured along the vertex's own normal:
    how far it can still travel before it meets the layer. Negative: it has come through, by that much. NaN: no layer
    within reach in either direction. Only surfaces are intersected, never their orientation, which imported
    clothes get wrong all the time."""
    room = np.full(len(co), np.nan)
    for k in np.nonzero(mask)[0]:
        p = Vector(co[k].tolist())
        d = Vector(nr[k].tolist()) * -side  # towards the reference layer
        hit = bvh.ray_cast(p + d * 1e-5, d, reach)
        if hit[0] is not None:
            room[k] = hit[3]
            continue
        hit = bvh.ray_cast(p - d * 1e-5, -d, behind)
        if hit[0] is not None:
            room[k] = -hit[3]
    return room


def clearance(co, nr, bvh, gap, max_push, side, out, cover):
    """Raise `out` (metres, per vertex) to how far each covered vertex has to travel along its own normal to sit `gap`
    clear of the reference layer, on the side it belongs: side +1 is outside it (clothes over a layer), -1 is inside
    it (skin under clothes). Returns how many would need more than `max_push`: that is a weight problem, not
    something to fix by inflating the cloth."""
    room = room_of(co, nr, bvh, side, cover, COVER_REACH, max_push)
    need = gap - room  # NaN where there is nothing near
    ok = need > 0.0
    out[ok] = np.maximum(out[ok], np.minimum(need[ok], max_push))
    return int((need > max_push).sum())


# ---- poses ------------------------------------------------------------------------------------------------------

def character_frame(rig, best):
    """(up, forward, left) of the character in the armature's space, worked out from the bones, not from world axes."""
    bones = rig.pose.bones

    def head(key):
        name = best.get(key)
        return bones[name].head.copy() if name and name in bones else None

    hips, top = head("Hips"), head("Head") or head("Neck")
    up = (top - hips) if hips is not None and top is not None else Vector((0.0, 0.0, 1.0))
    up.normalize()
    left_p = head("LeftUpperArm") or head("LeftUpperLeg")
    right_p = head("RightUpperArm") or head("RightUpperLeg")
    left = (left_p - right_p) if left_p is not None and right_p is not None else Vector((1.0, 0.0, 0.0))
    left = left - up * left.dot(up)
    left.normalize()
    forward = left.cross(up).normalized()
    return up, forward, left


def pose_list(quality):
    if quality == "REST":
        return []
    poses = [(name, steps, 1.0) for name, steps in BASE_POSES]
    if quality in ("NORMAL", "THOROUGH"):
        poses += [(name, steps, 1.0) for name, steps in MIXED_POSES]
    if quality == "THOROUGH":
        poses += [(f"{name}, half", steps, 0.5) for name, steps in BASE_POSES + MIXED_POSES]
    return poses


def apply_pose(context, rig, best, frame, steps, fraction, saved):
    """Turn bones as `steps` say. Records each bone's original local transform in `saved` before touching it."""
    up, forward, left = frame
    for key, goal, degrees, mode in steps:
        for side in (("Left", "Right") if key in SIDED else ("",)):
            name = best.get(side + key)
            if name is None or name not in rig.pose.bones:
                continue
            context.view_layer.update()  # the parent's new pose has to be in place before this bone is read
            pb = rig.pose.bones[name]
            along = pb.tail - pb.head
            if along.length < 1e-9:
                continue
            along.normalize()
            sign = -1.0 if side == "Right" else 1.0
            if goal == "twist":
                axis, angle = along, math.radians(degrees) * fraction
            else:
                target = {"down": -up, "up": up, "fwd": forward, "back": -forward, "out": left * sign,
                          "in": -left * sign, "left": left, "right": -left}[goal]
                axis = along.cross(target)
                if axis.length < 1e-6:
                    axis = along.orthogonal()
                axis.normalize()
                full = along.angle(target, 0.0)
                angle = (full - math.radians(degrees)) * fraction if mode == "to" else math.radians(degrees) * fraction
                if angle <= 1e-4:
                    continue
            saved.setdefault(name, pb.matrix_basis.copy())
            pivot = Matrix.Translation(pb.head)
            pb.matrix = pivot @ Matrix.Rotation(angle, 4, axis) @ pivot.inverted() @ pb.matrix


def restore_pose(context, rig, saved):
    for name, basis in saved.items():
        if name in rig.pose.bones:
            rig.pose.bones[name].matrix_basis = basis
    saved.clear()
    context.view_layer.update()


# ---- the fit itself ---------------------------------------------------------------------------------------------

def find_rig(context, skin):
    for mod in skin.modifiers:
        if mod.type == "ARMATURE" and mod.object:
            return mod.object
    return armature.rig_of(context)


def clear_fit_keys(ob):
    blocks = face.key_blocks(ob)
    for name in fit_keys(ob):
        ob.shape_key_remove(blocks[name])


def write_key(ob, name, push, world_to_local, flip=None):
    """A shape key that moves every vertex `push` metres along its outward normal (negative: against it). `flip` turns
    the normals of inside-out shells around."""
    if face.key_blocks(ob) is None:  # a mesh with no shape keys gets its basis first
        ob.shape_key_add(name="Basis", from_mix=False)
    key = ob.shape_key_add(name=name[:NAME_LIMIT], from_mix=False)
    normals = normals_local(ob) if flip is None else normals_local(ob) * flip[:, None].astype(np.float32)
    moved = basis_local(ob) + normals * (push * world_to_local)[:, None].astype(np.float32)
    key.data.foreach_set("co", moved.reshape(-1))
    key.relative_key = face.key_blocks(ob)[0]
    key.value = 0.0
    return key


def group_snapshot(group, graph):
    """Several meshes seen as one: positions and normals joined end to end."""
    parts = [snapshot(ob, graph) for ob in group]
    return np.concatenate([p[0] for p in parts]), np.concatenate([p[1] for p in parts])


def group_triangles(group):
    out, offset = [], 0
    for ob in group:
        out.append(triangles_of(ob) + offset)
        offset += len(ob.data.vertices)
    return np.concatenate(out) if out else np.zeros((0, 3), dtype=np.int32)


def skin_label(skins):
    return skins[0].name if len(skins) == 1 else "Skin"


def fit_job(context, skins, garments, gap, quality, max_push, skin_mode="SHRINK"):
    """Generator: yields progress 0..1 while it works, returns a result with one row per key written.
    `skins` are the body meshes (head, body, hands ...), measured together as one skin. `garments` run from the layer
    next to the skin outwards. Sizes are in metres.
    skin_mode SHRINK moves the skin in under each garment (the clothes keep their shape), PUSH moves the clothes out."""
    skins = list(skins)
    if not skins:
        raise ValueError("Add at least one skin mesh first")
    groups = [skins] + [[g] for g in garments]
    for ob in skins + list(garments):
        if ob.name not in context.view_layer.objects:  # deleted from the scene or in an excluded collection: not evaluated
            raise ValueError(f"{ob.name} is not in this scene. Take it out of the list, or put it back")
        if ob.hide_viewport or ob.hide_get():
            raise ValueError(f"{ob.name} is hidden. Show it first")
    rig = next((r for r in (find_rig(context, s) for s in skins) if r is not None), None)
    saved, rows, unsolved = {}, [], 0
    held = {}
    for ob in skins + list(garments):  # fit against the basis: no blendshape, earlier fit keys included, may colour it
        blocks = face.key_blocks(ob)
        for block in (list(blocks)[1:] if blocks else []):
            held[(ob, block.name)] = block.value
            block.value = 0.0
    shrink = skin_mode == "SHRINK"
    label = skin_label(skins)
    names = [label] + [g.name for g in garments]  # what each layer is called in key names
    try:
        poses = [("rest", [], 1.0)]
        best, frame = {}, None
        if rig is not None:
            poses += pose_list(quality)
            best = armature.map_bones(rig)[0]
            frame = character_frame(rig, best)
        shots = []  # one pass over the poses, every layer measured in each
        for number, (pose_name, steps, fraction) in enumerate(poses):
            if steps:
                apply_pose(context, rig, best, frame, steps, fraction, saved)
                context.view_layer.update()
            graph = context.evaluated_depsgraph_get()
            shots.append([group_snapshot(group, graph) for group in groups])
            if steps:
                restore_pose(context, rig, saved)
            yield 0.45 * (number + 1) / len(poses)
        tris = [group_triangles(group) for group in groups]
        starts, total_skin = [], 0  # where each skin mesh begins inside the joined skin
        for ob in skins:
            starts.append(total_skin)
            total_skin += len(ob.data.vertices)
        rest_skin = shots[0][0][0]

        def edge_lengths(ob, rest):
            a, b = edges_of(ob)
            return a, b, np.linalg.norm(rest[a] - rest[b], axis=1)

        garment_edges = [None] + [edge_lengths(g, shots[0][i + 1][0]) for i, g in enumerate(garments)]
        skin_edges = [edge_lengths(ob, rest_skin[starts[i]:starts[i] + len(ob.data.vertices)]) for i, ob in enumerate(skins)]
        joined_skin = (np.concatenate([e[0] + starts[i] for i, e in enumerate(skin_edges)]),
                       np.concatenate([e[1] + starts[i] for i, e in enumerate(skin_edges)]))
        skin_bvh = build_bvh(rest_skin, tris[0])
        skin_ref = (shots[0][0][1], tris[0])
        flips = [None]  # per garment: which vertices have their normals turned the wrong way
        for gi in range(1, len(groups)):
            g_co, g_nr = shots[0][gi]
            flips.append(layer_flips(g_co, g_nr, garment_edges[gi][:2], skin_bvh, skin_ref))
            for shot in shots:
                shot[gi] = (shot[gi][0], shot[gi][1] * flips[gi][:, None].astype(np.float32))
        ref_of = lambda li: (shots[0][li][1], tris[li])  # a layer's rest normals, inside-out shells turned around
        last_fit["flips"] = {garments[gi - 1].name: flips[gi] for gi in range(1, len(groups))}

        def stretch_of(ob):
            m = np.array(ob.matrix_world)[:3, :3]
            return np.maximum(np.linalg.norm(m @ normals_local(ob).T, axis=0), 1e-9)  # world length per unit

        pushes = {}  # layer index -> push when all its keys are on (metres, per vertex): the state outer layers see
        skin_needs = {}
        total_steps = len(garments) * len(shots)
        done = 0
        for gi, ob in enumerate(garments, start=1):
            inner = list(range(1, gi)) if shrink else list(range(0, gi))
            needs = {li: np.zeros(len(ob.data.vertices)) for li in inner}
            skin_need = np.zeros(total_skin)
            rest_co, rest_nr = shots[0][gi]  # what is covered is decided once, at rest
            over = {}
            for li in inner:
                ref_co = shots[0][li][0]
                box = (ref_co.min(axis=0), ref_co.max(axis=0))
                over[li] = covered(rest_co, rest_nr, build_bvh(ref_co, tris[li]), box, 1, garment_edges[gi][:2], ref_of(li))
            if shrink:
                box = (rest_co.min(axis=0), rest_co.max(axis=0))
                under = covered(rest_skin, shots[0][0][1], build_bvh(rest_co, tris[gi]), box, -1, joined_skin, ref_of(gi))
            for shot in shots:
                co, nr = shot[gi]
                for li in inner:
                    ref_co, ref_nr = shot[li]
                    if li in pushes:  # a garment below, in the state it will be in once it is fitted
                        ref_co = ref_co + pushes[li][:, None].astype(np.float32) * ref_nr
                    box = (ref_co.min(axis=0), ref_co.max(axis=0))
                    unsolved += clearance(co, nr, build_bvh(ref_co, tris[li]), gap, max_push, 1, needs[li], over[li])
                if shrink:  # the skin, inside this garment as it is without any fitting
                    s_co, s_nr = shot[0]
                    box = (co.min(axis=0), co.max(axis=0))
                    unsolved += clearance(s_co, s_nr, build_bvh(co, tris[gi]), gap, max_push, -1, skin_need, under)
                done += 1
                yield 0.45 + 0.5 * done / total_steps
            a, b, length = garment_edges[gi]
            spread = {li: np.minimum(geom.spread(need, a, b, length), max_push) for li, need in needs.items()}
            if shrink:
                parts = [(li, spread[li]) for li in inner]
            else:  # one key for the skin, then only what each garment needs beyond it
                base = spread[0]
                parts = [(0, base)] + [(li, np.maximum(spread[li] - base, 0.0)) for li in inner if li]
            total = np.zeros(len(ob.data.vertices))
            clear_fit_keys(ob)
            stretch = stretch_of(ob)
            for li, push in parts:
                if push.max(initial=0.0) < MIN_PUSH:
                    continue
                key = write_key(ob, FIT + names[li], push, 1.0 / stretch, flips[gi])
                total += push
                rows.append(SimpleNamespace(garment=ob.name, shape=key.name, over=names[li],
                                            mm=float(push.max()) * 1000.0, vertices=int((push > MIN_PUSH).sum())))
            pushes[gi] = total
            if shrink:  # each skin mesh spreads its own share over its own edges
                skin_needs[gi] = []
                for i, skin_ob in enumerate(skins):
                    share = skin_need[starts[i]:starts[i] + len(skin_ob.data.vertices)]
                    sa, sb, slen = skin_edges[i]
                    skin_needs[gi].append(np.minimum(geom.spread(share, sa, sb, slen), max_push))
            ob.data.update()
        for skin_ob in skins:
            clear_fit_keys(skin_ob)
        for gi, shares in skin_needs.items():
            for skin_ob, push in zip(skins, shares):
                if push.max(initial=0.0) < MIN_PUSH:
                    continue
                key = write_key(skin_ob, FIT + names[gi], -push, 1.0 / stretch_of(skin_ob))
                rows.append(SimpleNamespace(garment=skin_ob.name, shape=key.name, over=names[gi],
                                            mm=float(push.max()) * 1000.0, vertices=int((push > MIN_PUSH).sum())))
        for skin_ob in skins:
            skin_ob.data.update()
    finally:
        if rig is not None:
            restore_pose(context, rig, saved)
        for (ob, name), value in held.items():
            blocks = face.key_blocks(ob)
            if blocks and name in blocks:
                blocks[name].value = value
    last_fit["rows"] = [(r.garment, r.shape, r.mm) for r in rows]
    return SimpleNamespace(rows=rows, inside_out=sum(int((f < 0).sum()) for f in flips[1:]) if flips else 0, unsolved=unsolved, poses=len(poses))


def run_fit(context, skins, garments, gap, quality, max_push, skin_mode="SHRINK"):
    """Run the whole job at once. Used by scripts and tests; the operator does it in small slices."""
    job = fit_job(context, skins, garments, gap, quality, max_push, skin_mode)
    try:
        while True:
            next(job)
    except StopIteration as done:
        return done.value


def mean_distance(skins, ob, context):
    """How far a garment sits from the skin on average. The smaller, the closer to the body."""
    graph = context.evaluated_depsgraph_get()
    skin_co, _ = group_snapshot(skins, graph)
    co, _ = snapshot(ob, graph)
    bvh = build_bvh(skin_co, group_triangles(skins))
    sample = co[:: max(1, len(co) // 2000)]
    dists = [d for d in (bvh.find_nearest(Vector(p.tolist()))[3] for p in sample) if d is not None]
    return sum(dists) / len(dists) if dists else float("inf")


def sidecar(context, meshes):
    """What Unity needs to switch outfits: which meshes toggle, and which fit shape to turn on when. A shape on a
    garment named KAT.fit.X is on when the garment is worn (and X too, if X is another outfit); a shape on a skin
    mesh named KAT.fit.X is on when X is worn."""
    s = settings(context)
    listed = [ob for ob in outfit_objects(context) if ob in meshes]
    names = [ob.name for ob in listed]
    skins = skin_objects(context)
    fits = []
    for ob in meshes:
        if ob not in listed and ob not in skins:
            continue
        for key in fit_keys(ob):
            over = key[len(FIT):]
            if ob in listed:
                when = [ob.name] + ([over] if over in names else [])
            elif over in names:
                when = [over]
            else:
                continue
            fits.append({"mesh": ob.name, "shape": key, "when": when})
    skin_names = [ob.name for ob in skins]
    return {"skin": skin_names[0] if skin_names else "", "skins": skin_names,
            "outfits": [{"mesh": n, "layer": i} for i, n in enumerate(names)], "fits": fits}


# ---- operators and panel ----------------------------------------------------------------------------------------

class KAT_UL_outfits(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        ob = item.obj
        if ob is None:
            layout.label(text="(missing)", icon="ERROR")
            return
        row = layout.row(align=True)
        row.label(text=f"{index + 1}")
        row.label(text=ob.name, icon="MATCLOTH")
        fits = len(fit_keys(ob))
        if fits:
            sub = row.row()
            sub.alignment = "RIGHT"
            sub.label(text=str(fits), icon="MOD_CLOTH")


class KAT_UL_skins(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        ob = item.obj
        if ob is None:
            layout.label(text="(missing)", icon="ERROR")
            return
        row = layout.row(align=True)
        row.label(text=ob.name, icon="OUTLINER_OB_MESH")
        fits = len(fit_keys(ob))
        if fits:
            sub = row.row()
            sub.alignment = "RIGHT"
            sub.label(text=str(fits), icon="MOD_CLOTH")


class KAT_OT_outfit_add(bpy.types.Operator):
    bl_idname = "kat.outfit_add"
    bl_label = "Add selected meshes"
    bl_description = "Add the selected meshes to the outfit list, outermost last"
    bl_options = {"REGISTER", "UNDO"}

    @operator_guard
    def execute(self, context):
        s = settings(context)
        have = {i.obj for i in s.items}
        skins = set(skin_objects(context))
        added = 0
        for ob in context.selected_objects:
            if ob.type == "MESH" and ob not in have and ob not in skins:
                s.items.add().obj = ob
                added += 1
        if not added:
            raise ValueError("Select the clothing meshes (not the skin) first")
        s.index = len(s.items) - 1
        self.report({"INFO"}, f"Added {added}. Order them from the layer next to the skin outwards")
        return {"FINISHED"}


class KAT_OT_outfit_detect(bpy.types.Operator):
    bl_idname = "kat.outfit_detect"
    bl_label = "Find clothes by name"
    bl_description = "Add meshes whose names look like clothing (shirt, skirt, jacket, shoes ...) to the list"
    bl_options = {"REGISTER", "UNDO"}

    @operator_guard
    def execute(self, context):
        s = settings(context)
        rig = armature.rig_of(context)
        pool = armature.meshes_of(rig) if rig else [o for o in context.scene.objects if o.type == "MESH"]
        have = {i.obj for i in s.items} | set(skin_objects(context))
        found = [o for o in pool if o not in have and is_clothing_name(o.name)]
        for ob in found:
            s.items.add().obj = ob
        if not found:
            raise ValueError("No mesh name looks like clothing. Select them and use Add selected")
        self.report({"INFO"}, f"Added {len(found)}: " + ", ".join(o.name for o in found))
        return {"FINISHED"}


class KAT_OT_outfit_remove(bpy.types.Operator):
    bl_idname = "kat.outfit_remove"
    bl_label = "Remove from list"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        s = settings(context)
        if 0 <= s.index < len(s.items):
            s.items.remove(s.index)
            s.index = max(0, min(s.index, len(s.items) - 1))
        return {"FINISHED"}


class KAT_OT_outfit_move(bpy.types.Operator):
    bl_idname = "kat.outfit_move"
    bl_label = "Move in the list"
    bl_options = {"REGISTER", "UNDO"}
    direction: EnumProperty(items=[("UP", "Up", ""), ("DOWN", "Down", "")])

    def execute(self, context):
        s = settings(context)
        new = s.index + (-1 if self.direction == "UP" else 1)
        if 0 <= s.index < len(s.items) and 0 <= new < len(s.items):
            s.items.move(s.index, new)
            s.index = new
        return {"FINISHED"}


class KAT_OT_outfit_sort(bpy.types.Operator):
    bl_idname = "kat.outfit_sort"
    bl_label = "Order by distance from the skin"
    bl_description = "Put the pieces closest to the skin first and the outermost last"
    bl_options = {"REGISTER", "UNDO"}

    @operator_guard
    def execute(self, context):
        s = settings(context)
        skins = skin_objects(context)
        if not skins:
            raise ValueError("Add the skin meshes first")
        objs = outfit_objects(context)
        ranked = sorted(objs, key=lambda ob: mean_distance(skins, ob, context))
        s.items.clear()
        for ob in ranked:
            s.items.add().obj = ob
        self.report({"INFO"}, "Order: " + " > ".join(o.name for o in ranked))
        return {"FINISHED"}


class KAT_OT_skin_add(bpy.types.Operator):
    bl_idname = "kat.skin_add"
    bl_label = "Add selected as skin"
    bl_description = "Add the selected meshes (head, body, hands ...) to the skin. They are measured together as one skin"
    bl_options = {"REGISTER", "UNDO"}

    @operator_guard
    def execute(self, context):
        s = settings(context)
        have = {i.obj for i in s.skins} | set(outfit_objects(context))
        added = 0
        for ob in context.selected_objects:
            if ob.type == "MESH" and ob not in have:
                s.skins.add().obj = ob
                added += 1
        if not added:
            raise ValueError("Select the body meshes first (ones already listed as clothes are skipped)")
        s.skin_index = len(s.skins) - 1
        return {"FINISHED"}


class KAT_OT_skin_remove(bpy.types.Operator):
    bl_idname = "kat.skin_remove"
    bl_label = "Remove from skin"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        s = settings(context)
        if 0 <= s.skin_index < len(s.skins):
            s.skins.remove(s.skin_index)
            s.skin_index = max(0, min(s.skin_index, len(s.skins) - 1))
        return {"FINISHED"}


class KAT_OT_outfit_skin(bpy.types.Operator):
    bl_idname = "kat.outfit_skin"
    bl_label = "Find the skin by name"
    bl_description = "Add meshes called Skin, Body, Head or Face (and similar) that are not clothes"
    bl_options = {"REGISTER", "UNDO"}

    @operator_guard
    def execute(self, context):
        s = settings(context)
        rig = armature.rig_of(context)
        pool = armature.meshes_of(rig) if rig else [o for o in context.scene.objects if o.type == "MESH"]
        have = {i.obj for i in s.skins} | set(outfit_objects(context))
        found = [o for o in pool if o not in have and o.type == "MESH" and is_skin_name(o.name)]
        for ob in found:
            s.skins.add().obj = ob
        if not found:
            raise ValueError("No mesh is called Skin, Body, Head or Face. Select the body meshes and use Add selected")
        self.report({"INFO"}, "Skin: " + ", ".join(o.name for o in found))
        return {"FINISHED"}


class KAT_OT_fit(bpy.types.Operator):
    bl_idname = "kat.fit"
    bl_label = "Create fit shapes"
    bl_description = ("Pose the avatar through many positions and make shape keys that keep each piece of clothing "
                      "clear of the skin and the pieces under it. Esc stops it")
    bl_options = {"REGISTER", "UNDO"}

    _timer = None
    _job = None

    def setup(self, context):
        s = settings(context)
        skins = skin_objects(context)
        if not skins:
            raise ValueError("Add the skin meshes first")
        garments = outfit_objects(context)
        if not garments:
            raise ValueError("Add the clothes to the list first")
        return s, skins, garments

    @invoke_guard
    def invoke(self, context, event):
        s, skins, garments = self.setup(context)
        self._job = fit_job(context, skins, garments, s.gap / 1000.0, s.quality, s.max_push / 1000.0, s.skin_mode)
        wm = context.window_manager
        wm.progress_begin(0, 100)
        self._timer = wm.event_timer_add(0.02, window=context.window)
        wm.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def _finish(self, context):
        wm = context.window_manager
        wm.progress_end()
        if self._timer is not None:
            wm.event_timer_remove(self._timer)
            self._timer = None

    def modal(self, context, event):
        if event.type == "ESC":
            self._job.close()  # runs the job's clean-up: the pose and the old key values come back
            self._finish(context)
            self.report({"WARNING"}, "Stopped. Nothing was changed")
            return {"CANCELLED"}
        if event.type != "TIMER":
            return {"PASS_THROUGH"}
        end = time.monotonic() + 0.05  # a slice of work at a time, so the window stays alive
        try:
            while time.monotonic() < end:
                progress = next(self._job)
                context.window_manager.progress_update(int(progress * 100))
        except StopIteration as done:
            self._finish(context)
            self.report_result(done.value)
            return {"FINISHED"}
        except (ValueError, RuntimeError, TypeError, KeyError, AttributeError) as err:
            self._finish(context)
            self._job.close()
            self.report({"ERROR"}, str(err))
            return {"CANCELLED"}
        return {"RUNNING_MODAL"}

    def report_result(self, result):
        if not result.rows:
            self.report({"INFO"}, "Nothing clips, so no fit shapes were needed")
        else:
            names = sorted({r.garment for r in result.rows})
            self.report({"INFO"}, f"Made {len(result.rows)} fit shapes on {', '.join(names)} from {result.poses} poses")
        if result.unsolved:
            self.report({"INFO"}, f"{result.unsolved} vertex checks clipped deeper than the limit and were left alone")
        if result.inside_out:
            self.report({"INFO"}, f"{result.inside_out} cloth vertices had inside-out normals, which were turned around")

    @operator_guard
    def execute(self, context):
        s, skins, garments = self.setup(context)
        self.report_result(run_fit(context, skins, garments, s.gap / 1000.0, s.quality, s.max_push / 1000.0, s.skin_mode))
        return {"FINISHED"}


class KAT_OT_fit_clear(bpy.types.Operator):
    bl_idname = "kat.fit_clear"
    bl_label = "Remove fit shapes"
    bl_description = "Delete every KAT.fit.* shape key from the skin and the listed clothes"
    bl_options = {"REGISTER", "UNDO"}

    @operator_guard
    def execute(self, context):
        gone = 0
        for ob in outfit_objects(context) + skin_objects(context):
            gone += len(fit_keys(ob))
            clear_fit_keys(ob)
        last_fit["rows"] = []
        self.report({"INFO"}, f"Removed {gone} fit shapes")
        return {"FINISHED"}


class KAT_PT_outfits(bpy.types.Panel):
    bl_label = "Outfits"
    bl_idname = "KAT_PT_outfits"
    bl_order = 1050
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "KAT"
    bl_options = {"DEFAULT_CLOSED"}

    def draw_header(self, context):
        self.layout.label(icon="MATCLOTH")

    def draw(self, context):
        s = settings(context)
        layout = self.layout
        box = layout.box()
        box.label(text="Skin (measured together as one)", icon="OUTLINER_OB_MESH")
        row = box.row()
        row.alert = not s.skins
        row.template_list("KAT_UL_skins", "", s, "skins", s, "skin_index", rows=2)
        col = row.column(align=True)
        col.operator("kat.skin_add", text="", icon="ADD")
        col.operator("kat.skin_remove", text="", icon="REMOVE")
        box.operator("kat.outfit_skin", icon="VIEWZOOM")
        box = layout.box()
        box.label(text="Clothes, from the skin outwards", icon="MATCLOTH")
        row = box.row()
        row.template_list("KAT_UL_outfits", "", s, "items", s, "index", rows=4)
        col = row.column(align=True)
        col.operator("kat.outfit_add", text="", icon="ADD")
        col.operator("kat.outfit_remove", text="", icon="REMOVE")
        col.separator()
        col.operator("kat.outfit_move", text="", icon="TRIA_UP").direction = "UP"
        col.operator("kat.outfit_move", text="", icon="TRIA_DOWN").direction = "DOWN"
        row = box.row(align=True)
        row.operator("kat.outfit_detect", icon="VIEWZOOM")
        row.operator("kat.outfit_sort", text="", icon="SORTSIZE")
        if not s.items:
            uiutil.paragraph(layout, context, "Add the pieces that switch on and off. KAT keeps them as separate meshes "
                                              "when optimizing and makes the toggles in Unity.", "INFO")
            return


class KAT_PT_fitshapes(bpy.types.Panel):
    bl_label = "Fit shapes"
    bl_idname = "KAT_PT_fitshapes"
    bl_parent_id = "KAT_PT_outfits"
    bl_order = 1037
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "KAT"
    bl_options = {"DEFAULT_CLOSED"}

    def draw_header(self, context):
        self.layout.label(icon="MOD_CLOTH")

    def draw(self, context):
        s = settings(context)
        box = self.layout
        uiutil.paragraph(box, context, "Poses the avatar and makes the shapes that keep each piece off the skin and off "
                                       "the pieces under it.", "INFO")
        box.prop(s, "skin_mode", expand=True)
        box.prop(s, "gap", text="Gap (mm)")
        box.prop(s, "quality", expand=True)
        box.prop(s, "max_push", text="Limit (mm)")
        row = box.row()
        row.scale_y = 1.3
        row.enabled = bool(s.skins) and bool(s.items)
        row.operator("kat.fit", icon="MOD_CLOTH")
        if last_fit["rows"]:
            col = box.column(align=True)
            for garment, shape, mm in last_fit["rows"][:12]:
                col.label(text=f"{garment}: {shape.replace(FIT, 'over ')}  {mm:.1f} mm", icon="DOT")
        box.operator("kat.fit_clear", icon="X")


classes = (KAT_OutfitItem, KAT_OutfitSettings, KAT_UL_outfits, KAT_UL_skins, KAT_OT_outfit_add, KAT_OT_outfit_detect,
           KAT_OT_outfit_remove, KAT_OT_outfit_move, KAT_OT_outfit_sort, KAT_OT_skin_add, KAT_OT_skin_remove,
           KAT_OT_outfit_skin, KAT_OT_fit, KAT_OT_fit_clear, KAT_PT_outfits, KAT_PT_fitshapes)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.kat_outfits = PointerProperty(type=KAT_OutfitSettings)


def unregister():
    del bpy.types.Scene.kat_outfits
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
