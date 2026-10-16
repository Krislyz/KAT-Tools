# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Krislyz
"""Refit clothes made for one avatar onto another.

The outfit comes in with its own armature. Refit does four things, each one usable on its own:
  1. Fit skeleton: every bone of the outfit's rig is matched to the bone it stands for on your rig (humanoid names,
     then plain name matches, then the nearest mapped parent), and the garment is carried along with it. Limb length,
     torso length and shoulder width are yours afterwards.
  2. Fit to body: clothes that are inside the body are pushed out to a thin gap, tight clothes that float off it are
     drawn onto it, loose clothes are left loose. The push is spread over the whole garment, so folds, seams and details
     keep their shape.
  3. Weights: the outfit's bone weights are moved onto your bones and blended with the body's own weights where the cloth
     lies on the skin, so it bends the way your body bends.
  4. Rigid parts: ties, buckles, badges, anything small or named like that moves as one block and cannot be stretched.
The outfit's rig is removed at the end and the garments are bound to yours.
"""
import contextlib
import re
import time
from types import SimpleNamespace

import bmesh
import bpy
import numpy as np
from bpy.props import BoolProperty, EnumProperty, FloatProperty, IntProperty, PointerProperty, StringProperty
from mathutils import Matrix, Vector
from mathutils.kdtree import KDTree

from ..core import refit as core
from ..core.safe import operator_guard
from . import armature, face, outfits, textures, uiutil

DEEP = 0.015       # metres: pushed deeper than this, a vertex moves the way the cloth faces
LAYER_STEP = 1.5   # millimetres of extra room for each garment layer over the one before
SLOPE = 0.45       # how steeply cloth may rise next to a place that is pushed out: 0.45 m up per metre along
HIDDEN = 0.012     # metres: an edge whose middle is deeper in the body than this is not cut
CLOTH_NEAR = 0.012     # metres: cloth further from the skin than this is not settled by the cloth pass
CLOTH_MAX = 150000      # vertices: a garment with more is not run through the cloth step (it would take minutes)
CLOTH_SHARD = 0.004    # metres: a vertex that moved this much differently from its neighbours is a shard
CLOTH_JITTER = 0.005   # share of the vertices that may be shards
CLOTH_FLIPS = 0.01     # share of triangles the cloth may turn inside out
TUCK_REACH = 0.03  # metres: a vertex this close behind another garment's surface counts as tucked into it
TUCK_LEAST = 60    # vertices a tucked hem has at least
BAND_SHARE = 0.12    # a piece of its own up to this share of a garment, and thin, is a band
BAND_THIN = 0.04     # metres: how thin its thinnest side is
MEMBRANE_REACH = 0.012  # metres: what the membrane step still pushes out of a wall; the fit got closer than that, a deeper one is not a wall of this garment
MOST_MOVE = 0.03   # metres: a vertex asked to move further than this is left to the skeleton fit
LONG_EDGE = 0.006          # metres: edges longer than this are checked in the middle too
MIDDLES = (0.25, 0.5, 0.75)
SOLID_WORDS = "shoe, shose, boot, helmet, bag, glass, goggle, acc, hat, mask"
PROP_WORDS = "helmet, bag, can, phone, prop"
RIGID_WORDS = ("tie", "button", "buckle", "badge", "zip", "clasp", "emblem", "logo", "buckles", "pin", "ring")
last = {"text": ""}


MODE_ITEMS = [("FAST", "Fast", "Coarser and fewer rounds, no cloth step, quick poses. For a first look"),
              ("BALANCED", "Intermediate", "The everyday setting: about a minute for an outfit"),
              ("ACCURATE", "Accurate", "A finer mesh, many more rounds, a check after each garment and every pose. Takes several times longer")]


class KAT_RefitSettings(bpy.types.PropertyGroup):
    rig: PointerProperty(name="Outfit rig", type=bpy.types.Object, poll=lambda self, ob: ob.type == "ARMATURE",
                         description="The armature that came with the clothes")
    gap: FloatProperty(name="Thickness", default=2.0, min=0.0, max=30.0, precision=1,
                       description="Millimetres between the cloth and the skin")
    tighten: FloatProperty(name="Tighten", default=0.2, min=0.0, max=1.0, subtype="FACTOR",
                           description="How much cloth that floats a little off the body is drawn onto it. 0 leaves it as it "
                                       "is, 1 makes anything within reach hug the skin. Loose clothes are left alone")
    reach: FloatProperty(name="Reach", default=25.0, min=1.0, max=200.0, precision=0,
                         description="Millimetres from the skin within which cloth counts as tight")
    mode: EnumProperty(name="Mode", items=MODE_ITEMS, default="BALANCED",
                       description="How thorough the refit is. Accurate takes several times longer than Intermediate")
    cloth: BoolProperty(name="Settle with cloth", default=True,
                        description="After the fit, run Blender's cloth over each garment so it settles on the body. Stiff and "
                                    "held to its shape, so it keeps its look. Takes a few seconds more")
    stiffness: FloatProperty(name="Stiffness", default=500.0, min=1.0, max=5000.0, precision=0,
                             description="How firmly the cloth keeps its shape while it settles. High is a tough suit, low is silk")
    rigid: BoolProperty(name="Rigid small parts", default=True,
                        description="Ties, buckles, badges and other small separate pieces move as one block, so the body "
                                    "cannot deform them")
    solid_words: StringProperty(name="Hard parts", default=SOLID_WORDS,
                                description="Parts whose name has one of these words keep their shape and are only moved "
                                            "out of the body, not bent onto it (shoes, helmets, bags)")
    rigid_words: StringProperty(name="Also rigid", default=", ".join(RIGID_WORDS[:7]),
                                description="Parts whose name has one of these words are rigid, however big they are")
    keep_original: BoolProperty(name="Keep the original", default=True,
                                description="Keep each garment as it was before the fit as a shape key called KAT.original, so the fit "
                                            "can be switched off again (View original and View fit in the panel, or a toggle in Unity)")
    hide_props: BoolProperty(name="Hide carried props", default=True,
                             description="Hide the parts named in Props: they are fitted, but not worn, so they are also "
                                         "left out of the layers")
    list_layers: BoolProperty(name="List in Outfits", default=True,
                              description="Put the clothes in the Outfits panel, inner layer first")
    make_shapes: BoolProperty(name="Make fit shapes", default=True,
                              description="Pose the avatar and make the shapes that keep layers and skin from clipping "
                                          "(what Create fit shapes does). Takes a few seconds")
    shapes_on: BoolProperty(name="Switch shapes on", default=True,
                            description="Turn the fit shapes of what is worn on, so the result is visible in Blender. "
                                        "Unity switches them by itself, so turn this off for a model going straight there")
    props_words: StringProperty(name="Carried props", default=PROP_WORDS,
                                description="Parts whose name has one of these words are carried, not worn (a helmet in "
                                            "hand, a bag, a drink): they are fitted but hidden and left out of the layers")


def settings(context):
    return context.scene.kat_refit


# ---- helpers ----------------------------------------------------------------------------------------------------

@contextlib.contextmanager
def basis_state(objects, rigs):
    """Rest pose and the basis shape: the state clothes are fitted in, whatever the scene is showing."""
    saved_pose = [(r, r.data.pose_position) for r in rigs]
    saved_keys = []
    for ob in objects:
        blocks = face.key_blocks(ob)
        if blocks:
            for k in list(blocks)[1:]:
                saved_keys.append((k, k.value))
                k.value = 0.0
    for r, _ in saved_pose:
        r.data.pose_position = "REST"
    bpy.context.view_layer.update()
    try:
        yield
    finally:
        for r, pose in saved_pose:
            r.data.pose_position = pose
        for k, value in saved_keys:
            k.value = value
        bpy.context.view_layer.update()


# The whole trunk counts as long as it is from the hips to the neck: the short bones in between have lengths that mean
# nothing from one rig to the next (a spine bone can be a third of the one on another model), and the pelvis would be
# scaled differently from the legs under it.
NEXT = {"Hips": "Neck", "Spine": "Neck", "Chest": "Neck", "UpperChest": "Neck", "Neck": "Head",
        "UpperLeg": "LowerLeg", "LowerLeg": "Foot", "Foot": "Toes", "UpperArm": "LowerArm", "LowerArm": "Hand"}


def world_bones(rig):
    """(head, tail) in world space of every bone. A bone's own tail is not trusted: file formats do not store one, so
    the importer makes one up. It points at the next bone down the chain instead; a bone at the end of a chain
    continues the direction its parent had."""
    m = rig.matrix_world
    heads = {b.name: np.array(m @ b.head_local) for b in rig.data.bones}
    names, _ = armature.map_bones(rig)
    nxt = {}
    for key, bone in names.items():
        side = "Left" if key.startswith("Left") else "Right" if key.startswith("Right") else ""
        base = key[len(side):]
        want = NEXT.get(base)
        if want is None:
            continue
        for cand in (side + want, want):
            if cand in names:
                nxt[bone] = names[cand]
                break
    out = {}
    for b in rig.data.bones:
        head = heads[b.name]
        tail = None
        if b.name in nxt:
            tail = heads[nxt[b.name]]
        elif b.children:
            mean = np.mean([heads[c.name] for c in b.children], axis=0)
            if np.linalg.norm(mean - head) > 1e-4:
                tail = mean
        if tail is None or np.linalg.norm(tail - head) < 1e-4:
            if b.parent is not None and np.linalg.norm(head - heads[b.parent.name]) > 1e-4:
                way = head - heads[b.parent.name]
                tail = head + way * 0.6
            else:
                tail = np.array(m @ b.tail_local)
        out[b.name] = (head, tail)
    return out


def norm_name(name):
    low = name.lower()
    side = "l" if re.search(r"(^|[^a-z])(l|left)($|[^a-z])", low) else "r" if re.search(r"(^|[^a-z])(r|right)($|[^a-z])", low) else ""
    base = re.sub(r"(^|[^a-z])(l|left|r|right)($|[^a-z])", "_", low)
    return re.sub(r"[^a-z0-9]", "", base), side


def match_bones(src_rig, dst_rig):
    """{outfit bone: your bone} for the bones both rigs have: humanoid ones first, then equal names (breasts, tails, ears)."""
    sb, _ = armature.map_bones(src_rig)
    db, _ = armature.map_bones(dst_rig)
    out = {sb[k]: db[k] for k in sb if k in db}
    by_name = {}
    for b in dst_rig.data.bones:
        by_name.setdefault(norm_name(b.name), b.name)
    taken = set(out.values())
    for b in src_rig.data.bones:
        if b.name in out:
            continue
        hit = by_name.get(norm_name(b.name))
        if hit and hit not in taken and norm_name(b.name)[0]:
            out[b.name] = hit
            taken.add(hit)
    return out


def skeleton_matrices(src_rig, dst_rig):
    """Per outfit bone the 4x4 that moves it onto the matching bone of your rig, and the bone of yours it follows.
    A bone with no match of its own follows its nearest parent that has one."""
    direct = match_bones(src_rig, dst_rig)
    sw, dw = world_bones(src_rig), world_bones(dst_rig)
    mats, follows = {}, {}
    named, _ = armature.map_bones(src_rig)
    # Only the long bones of the body (trunk, arms, legs, neck) say anything about size and direction. The rest (head,
    # hands, toes, fingers, breasts, ribbons) have no length of their own worth comparing between two rigs, and the
    # heads of two "breast" bones need not be at the same place: they move like the bone they hang from, and only take
    # the matching bone's weights.
    chain = {named[k] for k in named if k.removeprefix("Left").removeprefix("Right") in NEXT and k.removeprefix("Left").removeprefix("Right") != "Head"}
    hips = named.get("Hips")
    order = sorted(src_rig.data.bones, key=lambda b: len(b.parent_recursive))
    for b in order:
        if b.name in direct and (b.name in chain or b.parent is None or b.parent.name not in mats):
            mats[b.name] = core.bone_matrix(*sw[b.name], *dw[direct[b.name]])
            follows[b.name] = direct[b.name]
        elif b.name in direct:
            mats[b.name] = mats[b.parent.name]
            follows[b.name] = direct[b.name]
        elif b.parent is not None and b.parent.name in mats:
            mats[b.name] = mats[b.parent.name]
            follows[b.name] = follows[b.parent.name]
    fallback = mats.get(hips, np.eye(4))
    fallback_bone = follows.get(hips, next(iter(follows.values()), ""))
    for b in order:
        if b.name not in mats:
            mats[b.name] = fallback
            follows[b.name] = fallback_bone
    return mats, follows, direct


def vertex_weights(ob, index, keep=6):
    """(idx, w) arrays, one row per vertex, of the strongest `keep` groups that name a bone in `index`."""
    n = len(ob.data.vertices)
    idx = np.zeros((n, keep), dtype=np.int64)
    w = np.zeros((n, keep), dtype=np.float64)
    names = [g.name for g in ob.vertex_groups]
    for v in ob.data.vertices:
        row = sorted(((g.weight, index[names[g.group]]) for g in v.groups if names[g.group] in index), reverse=True)[:keep]
        for k, (weight, bone) in enumerate(row):
            idx[v.index, k], w[v.index, k] = bone, weight
    return idx, w


def apply_world(ob):
    """Bake the object's transform into its mesh (shape keys too) and unparent it, so its coordinates are world ones."""
    if ob.data.users > 1:
        ob.data = ob.data.copy()
    mw = ob.matrix_world.copy()
    ob.parent = None
    ob.matrix_world = Matrix.Identity(4)
    ob.data.transform(mw, shape_keys=True)
    ob.data.update()


def coords(me, block=None):
    out = np.empty(len(me.vertices) * 3, dtype=np.float32)
    (block.data if block else me.vertices).foreach_get("co", out)
    return out.reshape(-1, 3).astype(np.float64)


def set_coords(me, co, block=None):
    (block.data if block else me.vertices).foreach_set("co", co.astype(np.float32).reshape(-1))


# How much work a refit does. Everything the modes change is about being thorough, never about being different: a finer
# mesh to collide with, more rounds of pushing out and settling, more checks, and more poses for the fit shapes.
MODES = {
    "FAST": dict(sigma=0.08, prefit=0, bend=False, membrane=0, rim=0, relax=0, densify=(0.016, 2, 0.3, HIDDEN), iterations=6, refits=1, cloth=False, frames=8, cloth_quality=4, apart=1, verify=0, poses="QUICK"),
    "BALANCED": dict(sigma=0.08, prefit=8, bend=False, membrane=120, rim=12, relax=0, densify=(0.012, 3, 0.6, HIDDEN), iterations=8, refits=2, cloth=True, frames=15, cloth_quality=6, apart=2, verify=0, poses=None),
    "ACCURATE": dict(sigma=0.08, prefit=10, bend=False, membrane=250, rim=60, relax=600, densify=(0.008, 4, 2.0, 0.012), iterations=14, refits=4, cloth=True, frames=40, cloth_quality=10, apart=5, verify=4, poses="THOROUGH"),
}
SKELETON_SMOOTH = 20       # rounds of smoothing over what the skeleton fit did to the cloth
ORIGINAL = "KAT.original"  # the shape a garment had before it was fitted, kept as a shape key so the fit can be switched off


def move_garment(ob, new_basis):
    """Put the basis shape at `new_basis`; every shape key is moved by the same per-vertex step, so it keeps its meaning.
    The one exception is the original shape: it is where the garment was, and stays there."""
    me = ob.data
    blocks = face.key_blocks(ob)
    old = coords(me, blocks[0]) if blocks else coords(me)
    step = new_basis - old
    if blocks:
        for k in blocks:
            if k.name != ORIGINAL:
                set_coords(me, coords(me, k) + step, k)
    set_coords(me, new_basis)
    me.update()


def keep_original_shape(ob):
    """Store the garment as it is now (your skeleton's proportions, nothing fitted to the body yet) as a shape key. With
    the key at 1 the garment is shown as it came; at 0 it is the fit. Made again if the garment is refitted."""
    if ob.data.shape_keys is None:
        ob.shape_key_add(name="Basis")
    blocks = ob.data.shape_keys.key_blocks
    if ORIGINAL in blocks:
        ob.shape_key_remove(blocks[ORIGINAL])
    key = ob.shape_key_add(name=ORIGINAL, from_mix=False)
    key.value = 0.0
    key.slider_min, key.slider_max = 0.0, 1.0
    return key


# ---- 1. skeleton ------------------------------------------------------------------------------------------------

def smooth_weights(ob, idx, w, bones, rounds):
    """The bone weights of a garment averaged with those of the neighbouring vertices `rounds` times, so the skeleton fit
    bends the cloth smoothly. Returns the (idx, w) pair again, with as many bones per vertex as it came with."""
    x = coords(ob.data)
    n = len(x)
    a, b = garment_edges(ob)
    rep = core.weld(x)
    keep = rep[a] != rep[b]
    wa, wb = rep[a][keep], rep[b][keep]
    deg = np.maximum(np.bincount(wa, minlength=n) + np.bincount(wb, minlength=n), 1)[:, None]
    dense = np.zeros((n, bones))
    np.add.at(dense, (np.repeat(np.arange(n), idx.shape[1]), idx.ravel()), w.ravel())
    for _ in range(rounds):
        total = np.zeros_like(dense)
        np.add.at(total, wa, dense[wb])
        np.add.at(total, wb, dense[wa])
        dense = (0.5 * dense + 0.5 * total / deg)[rep]
    order = np.argsort(-dense, axis=1)[:, :idx.shape[1]]
    top = np.take_along_axis(dense, order, axis=1)
    return order, top / np.maximum(top.sum(axis=1, keepdims=True), 1e-9)


def smooth_field(ob, field, rounds):
    """A per-vertex field (how far each vertex was moved) averaged with its neighbours over the cloth `rounds` times. Vertices
    that are one point (seams) stay together."""
    x = coords(ob.data)
    n = len(x)
    a, b = garment_edges(ob)
    rep = core.weld(x)
    keep = rep[a] != rep[b]
    wa, wb = rep[a][keep], rep[b][keep]
    deg = np.maximum(np.bincount(wa, minlength=n) + np.bincount(wb, minlength=n), 1)[:, None]
    field = field.copy()
    for _ in range(rounds):
        total = np.zeros_like(field)
        np.add.at(total, wa, field[wb])
        np.add.at(total, wb, field[wa])
        field = (0.5 * field + 0.5 * total / deg)[rep]
    return field


def fit_skeleton(garments, src_rig, dst_rig, hard=()):
    """Carry the garments onto the proportions of your skeleton. Returns {outfit bone: your bone it follows}."""
    mats, follows, direct = skeleton_matrices(src_rig, dst_rig)
    names = list(mats)
    index = {n: i for i, n in enumerate(names)}
    stack = np.array([mats[n] for n in names])
    for ob in garments:
        apply_world(ob)
    for ob in garments:
        idx, w = vertex_weights(ob, index)
        rows = w.sum(axis=1) < 1e-9
        hips = armature.map_bones(src_rig)[0].get("Hips")
        idx[rows, 0], w[rows, 0] = index.get(hips, 0), 1.0
        blocks = face.key_blocks(ob)
        base = coords(ob.data, blocks[0]) if blocks else coords(ob.data)
        if ob in hard:  # a shoe is carried by one transform per piece: bending it over the toe joint would ruin it
            n = len(base)
            dense = np.zeros((n, len(names)))
            np.add.at(dense, (np.repeat(np.arange(n), idx.shape[1]), idx.ravel()), w.ravel())
            right = base[:, 0] > 0.0  # a pair of shoes is two things; a helmet is one. Laces and soles go with their shoe.
            label = right.astype(int) if 0.2 < right.mean() < 0.8 else np.zeros(n, dtype=int)
            for p in np.unique(label):
                rows = label == p
                mean = dense[rows].mean(axis=0)
                dense[rows] = 0.0
                dense[np.ix_(rows, [int(mean.argmax())])] = 1.0  # the bone that carries most of it carries all of it
            idx = np.argsort(-dense, axis=1)[:, :idx.shape[1]]
            w = np.take_along_axis(dense, idx, axis=1)
        if ob not in hard:  # skin weights are never perfectly clean: a vertex that leans on a bone its neighbours do not creases the cloth
            idx, w = smooth_weights(ob, idx, w, len(names), SKELETON_SMOOTH)
        warped = core.warp(base, idx, w, stack)
        move_garment(ob, warped)
    return follows


def densify(ob, body, gap, longest=0.012, passes=3, cap=0.6, hidden=None):
    """Cut in half the long edges whose middle lies inside the body, a few times. A low-poly part (a shin that is one wide
    strip) cannot follow a rounder body: the cloth between its corners cuts straight through, however well the corners
    sit. With vertices in the middle it can. Weights, UVs and shape keys of the new vertices come from their neighbours.
    Returns how many vertices were added; stops early once that is `cap` of the original count."""
    me = ob.data
    start = len(me.vertices)
    blocks = face.key_blocks(ob)
    for _ in range(passes):
        x = coords(me, blocks[0]) if blocks else coords(me)
        ea, eb = outfits.edges_of(ob)
        pick = np.nonzero(np.linalg.norm(x[ea] - x[eb], axis=1) > longest)[0]
        # a middle far inside is not a skin crossing: it is a cap or a pocket bag inside the body, hidden, and best left alone
        inside = [e for e in pick if gap - (HIDDEN if hidden is None else hidden) < body.query((x[ea[e]] + x[eb[e]]) / 2.0)[3] < gap - 5e-5]
        if not inside or len(me.vertices) - start >= cap * start:
            break
        bm = bmesh.new()
        bm.from_mesh(me)
        bm.edges.ensure_lookup_table()
        bmesh.ops.bisect_edges(bm, edges=[bm.edges[e] for e in inside], cuts=1)  # subdivide_edges crashes Blender here
        wide = [f for f in bm.faces if len(f.verts) > 4]
        if wide:
            bmesh.ops.triangulate(bm, faces=wide, quad_method="BEAUTY", ngon_method="BEAUTY")
        bm.to_mesh(me)
        bm.free()
        me.update()
        blocks = face.key_blocks(ob)
    return len(me.vertices) - start


# ---- 2. surface -------------------------------------------------------------------------------------------------

class Body:
    """The target skin, in the rest pose: a BVH to ask 'how far outside, and which way', and its weights."""

    def __init__(self, context, skins, rig):
        graph = context.evaluated_depsgraph_get()
        self.co, self.nr = outfits.group_snapshot(skins, graph)
        self.tris = outfits.group_triangles(skins)
        self.bvh = outfits.build_bvh(self.co, self.tris)
        self.ref = (self.nr, self.tris)
        self.skins, self.rig = skins, rig
        self.bones = [b.name for b in rig.data.bones]
        self.index = {n: i for i, n in enumerate(self.bones)}
        self._weights = None

    def weights(self):
        if self._weights is None:
            parts = []
            for ob in self.skins:
                w = np.zeros((len(ob.data.vertices), len(self.bones)), dtype=np.float32)
                names = [g.name for g in ob.vertex_groups]
                for v in ob.data.vertices:
                    for g in v.groups:
                        col = self.index.get(names[g.group])
                        if col is not None:
                            w[v.index, col] = g.weight
                parts.append(w)
            self._weights = np.concatenate(parts)
        return self._weights

    def query(self, p):
        """(nearest point, outward normal, triangle, signed distance: positive outside) of a world point."""
        loc, nrm, idx, dist = self.bvh.find_nearest(Vector(p.tolist()))
        n = outfits.facing(nrm, idx, self.ref)
        off = (p[0] - loc[0]) * n[0] + (p[1] - loc[1]) * n[1] + (p[2] - loc[2]) * n[2]
        return np.array(loc), np.array(n), idx, off

    query_skin = query


def garment_edges(ob):
    a, b = outfits.edges_of(ob)
    return core.edge_arrays(np.stack([a, b], axis=1))


def outward_vertices(ob, body, x):
    """Which vertices face away from the body. Double-sided cloth has a lining whose normals point at the skin: it is
    not what the body collides with, and it follows the outer shell. If most of a garment faces the body (inside-out
    normals), it is the whole garment that counts."""
    nr = outfits.normals_local(ob)
    flips = outfits.layer_flips(x.astype(np.float32), nr, garment_edges(ob), body.bvh, body.ref)
    active = flips > 0
    return active if active.sum() >= 0.5 * len(x) else np.ones(len(x), dtype=bool)


def unfold(x0, x, tris, a, b, rounds=25):
    """Pushing can fold the cloth over itself where the body folds (a navel). Faces that now look the other way from
    before, and the vertices around them, are relaxed toward the average of their neighbours. Returns which vertices were touched."""
    n = len(x)

    def normals(p):
        """Unit normals, and whether the triangle is a proper one: slivers have no usable normal."""
        c = np.cross(p[tris[:, 1]] - p[tris[:, 0]], p[tris[:, 2]] - p[tris[:, 0]])
        area = np.linalg.norm(c, axis=1)
        longest = np.maximum.reduce([np.linalg.norm(p[tris[:, i]] - p[tris[:, (i + 1) % 3]], axis=1) for i in range(3)])
        return c / (area[:, None] + 1e-15), area > 0.1 * longest ** 2

    (n0, ok0), (n1, ok1) = normals(x0), normals(x)
    flipped = ((n0 * n1).sum(axis=1) < 0.0) & ok0 & ok1
    if not flipped.any():
        return np.zeros(n, dtype=bool)
    rep = core.weld(x0)  # vertices split along a seam are one vertex here, or the seam would open
    keep = rep[a] != rep[b]
    wa, wb = rep[a][keep], rep[b][keep]
    bad = np.zeros(n, dtype=bool)
    bad[rep[tris[flipped].ravel()]] = True
    for _ in range(3):
        grown = bad.copy()
        grown[wa[bad[wb]]] = True
        grown[wb[bad[wa]]] = True
        bad = grown
    bad = bad[rep]
    deg = np.maximum(np.bincount(wa, minlength=n) + np.bincount(wb, minlength=n), 1)[:, None]
    move = x - x0
    for _ in range(rounds):  # the way each vertex moved is relaxed, not where it is: a hem keeps its length
        total = np.zeros_like(move)
        np.add.at(total, wa, move[wb])
        np.add.at(total, wb, move[wa])
        move[bad] = 0.5 * move[bad] + 0.5 * (total / deg)[rep[bad]]
    x[bad] = x0[bad] + move[bad]
    return bad


def fresh_normals(context, ob):
    """Custom normals are the shading the artist baked for the shape the garment had; on the fitted shape they light it in
    blotches (patches that look dented). They are dropped, so the shading follows the fitted shape; sharp edges stay.
    Returns whether there was anything to drop."""
    if not ob.data.has_custom_normals:
        return False
    with context.temp_override(object=ob, active_object=ob):
        bpy.ops.mesh.customdata_custom_splitnormals_clear()
    return True


def smooth_rim(x0, x, tris, rep, rounds=10, normals=None):
    """A hem or a collar is the edge of the cloth, and each vertex on it was fitted on its own: it comes out as teeth. How
    far each rim vertex moved is smoothed along the rim, so the edge stays an edge and only the jitter goes. With `normals` it
    is the move along the cloth's own normal that is smoothed (the move is a vector, and a ring of vectors that point away
    from each other would average to nothing and pull the whole edge back). Returns the rim."""
    n = len(x)
    pairs = np.sort(np.concatenate([rep[tris][:, [0, 1]], rep[tris][:, [1, 2]], rep[tris][:, [2, 0]]]), axis=1)
    edges, inverse, count = np.unique(pairs, axis=0, return_inverse=True, return_counts=True)
    ra, rb = edges[count == 1].T
    rim = np.zeros(n, dtype=bool)
    rim[ra] = rim[rb] = True
    if not rim.any():
        return rim
    deg = np.maximum(np.bincount(ra, minlength=n) + np.bincount(rb, minlength=n), 1)[:, None]
    move = x - x0
    if normals is not None:
        along = (move * normals).sum(axis=1)[:, None]
        rest = move - along * normals
        field = along
    else:
        field = move
    inside = rim[rep]
    for _ in range(rounds):
        total = np.zeros_like(field)
        np.add.at(total, ra, field[rb])
        np.add.at(total, rb, field[ra])
        smooth = (total / deg)[rep]
        field[inside] = 0.5 * field[inside] + 0.5 * smooth[inside]
    if normals is not None:
        move = rest + field * normals
    x[inside] = x0[inside] + move[inside]
    return inside


def loose_pieces(x0, label):
    """Separate pieces of a garment that are not cloth to be fitted: (small, band) as masks over the pieces. Small pieces
    (buckles, belt loops, pockets) are carried as they are. Bands (a belt, a frill, a cuff that is a piece of its own: a
    ring a few centimetres tall) ride along with the cloth under every vertex of them."""
    sizes = np.bincount(label)
    n = len(x0)
    small = sizes <= max(40, 0.03 * n)
    band = np.zeros(len(sizes), dtype=bool)
    for p in np.nonzero(~small & (sizes <= BAND_SHARE * n))[0]:
        pts = x0[label == p]
        band[p] = float(np.sort(pts.max(axis=0) - pts.min(axis=0))[0]) < BAND_THIN
    return small, band


def layer_room(x0, body, over, gap, step, depth=3):
    """The gap of every vertex: `gap`, plus `step` for each sheet of the other garments that lies between it and the skin.
    A shirt under a jacket sits one step further out; a shirt tucked into trousers stays inside them, because there it
    has nothing over the skin to be on top of."""
    n = len(x0)
    room = np.full(n, float(gap))
    if over is None:
        return room
    for i in range(n):
        loc = body.query(x0[i])[0]
        way = loc - x0[i]
        dist = float(np.linalg.norm(way))
        if dist < 2e-4:
            continue
        way = way / dist
        pos, left, count = Vector((x0[i] + way * 1e-4).tolist()), dist, 0
        for _ in range(depth):
            hit = over.ray_cast(pos, Vector(way.tolist()), left)
            if hit[0] is None:
                break
            count += 1
            left -= (hit[0] - pos).length + 2e-4
            pos = hit[0] + Vector((way * 2e-4).tolist())
            if left <= 0:
                break
        room[i] += count * step
    return room


def fit_to_body(ob, body, gap, reach, tighten, iterations=8, hold=25.0, over=None, bend=False):
    """Push the garment's basis shape onto the body: out of it, and tight parts onto it. Returns a small report."""
    me = ob.data
    blocks = face.key_blocks(ob)
    x0 = coords(me, blocks[0]) if blocks else coords(me)
    n = len(x0)
    a, b = garment_edges(ob)
    rep = core.weld(x0)
    keep = rep[a] != rep[b]
    wa, wb = rep[a][keep], rep[b][keep]
    active = outward_vertices(ob, body, x0)
    facing_out = outfits.normals_local(ob)
    # A garment can have more than one shell (an inner waistband, a layer under a decal). Whatever has another sheet of the
    # same garment in front of it, facing the same way, is buried: it is not fitted, it goes where the sheet over it goes.
    own = outfits.build_bvh(x0.astype(np.float32), outfits.triangles_of(ob))
    tris_own = outfits.triangles_of(ob)
    under = {}  # buried vertex -> (the triangle of the sheet over it, where in it)
    for i in np.nonzero(active)[0]:
        hit = own.ray_cast(Vector((x0[i] + facing_out[i] * 1e-4).tolist()), Vector(facing_out[i].tolist()), 0.03)
        if hit[0] is not None and hit[1].dot(Vector(facing_out[i].tolist())) > 0.5:
            tri = tris_own[hit[2]]
            p0, p1, p2 = x0[tri[0]], x0[tri[1]], x0[tri[2]]
            v0, v1, v2 = p1 - p0, p2 - p0, np.array(hit[0]) - p0
            d00, d01, d11, d20, d21 = v0 @ v0, v0 @ v1, v1 @ v1, v2 @ v0, v2 @ v1
            den = max(d00 * d11 - d01 * d01, 1e-18)
            bv, bw = (d11 * d20 - d01 * d21) / den, (d00 * d21 - d01 * d20) / den
            under[i] = (tri, np.clip([1.0 - bv - bw, bv, bw], 0.0, 1.0))
            active[i] = False
    off0 = np.empty(n)
    for i in range(n):
        off0[i] = body.query(x0[i])[3]
    gv = layer_room(x0, body, over, gap, LAYER_STEP / 1000.0)
    goal = np.where(off0 < gv, gv, off0 + tighten * core.fall(off0, reach) * (gv - off0))
    flagged = ((off0 < gv) | (core.fall(off0, reach) > 0)) & active
    x = x0.copy()
    inside_before = int(((off0 < gv) & active).sum())
    # Long edges and big triangles can cut through a body their corners are all outside of. Their middles are checked
    # too, and a middle that is inside pushes the corners it belongs to.
    lengths = np.linalg.norm(x0[a] - x0[b], axis=1)
    longs = np.nonzero((lengths > LONG_EDGE) & active[a] & active[b])[0]
    for step in range(iterations):
        want = np.zeros((n, 3))
        pin = np.zeros(n)
        size = np.zeros(n)
        residual = 0.0
        push = np.zeros(n)
        for i in np.nonzero(flagged)[0]:
            loc, nrm, _, off = body.query(x[i])
            move = goal[i] - off
            if nrm @ facing_out[i] < 0.2:  # over a pit (navel): the nearest wall faces sideways, not at the cloth
                continue
            if abs(move) > MOST_MOVE:  # that deep is the wrong part of the body (a collar by the chin), not a fit
                continue
            if move < 0 and step < iterations - 3:  # tightening waits until nothing is inside, or it undoes the lifting
                continue
            if abs(move) > 5e-5:
                way = nrm
                if move > DEEP:  # far inside: the cloth's own outward way is a smooth one, the surface's flips from front to back
                    way = facing_out[i]
                    move = move / max(0.5, float(way @ nrm))
                want[i] = way * move
                pin[i] = hold
                size[i] = abs(move)
                residual = max(residual, abs(move))
                if move > 0:
                    push[i] = move
        # Cloth that has to climb 5 cm at one point cannot do it in a step: the cloth around it is lifted too, less and
        # less with distance, so the rise is a slope and not a cliff (cliffs fold into shards).
        lift = np.zeros(n)
        np.maximum.at(lift, rep, push)
        run = np.linalg.norm(x[wa] - x[wb], axis=1) * SLOPE
        for _ in range(60):
            nxt = lift.copy()
            np.maximum.at(nxt, wa, lift[wb] - run)
            np.maximum.at(nxt, wb, lift[wa] - run)
            if np.allclose(nxt, lift, atol=1e-6):
                break
            lift = nxt
        lift = lift[rep]
        for i in np.nonzero((lift > 5e-5) & (push == 0) & active)[0]:
            loc, nrm, _, off = body.query(x[i])
            if nrm @ facing_out[i] < 0.2:  # a cap inside the garment (the bottom of a shirt): it follows, it is not lifted
                continue
            want[i] = nrm * lift[i]
            pin[i] = hold * 0.2
            size[i] = lift[i] * 0.5
        for e in longs:
            for t in MIDDLES:
                p = x[a[e]] * (1.0 - t) + x[b[e]] * t
                loc, nrm, _, off = body.query(p)
                room = 0.5 * (gv[a[e]] + gv[b[e]])
                if room - MOST_MOVE < off < room - 5e-5:  # a middle deeper than that is a chord through the body, not a skin crossing
                    move = room - off
                    for v in (a[e], b[e]):
                        if move > size[v]:
                            want[v] = nrm * move
                            pin[v] = hold
                            size[v] = move
                    residual = max(residual, move)
        if not pin.any():
            break
        order = np.argsort(size)  # seam copies share the strongest request
        pin_r, want_r = np.zeros(n), np.zeros((n, 3))
        pin_r[rep[order]], want_r[rep[order]] = pin[order], want[order]
        spread = (2e-5, 2e-5, 1e-4, 1e-4)  # first the big picture (a whole chest too deep), then the details
        x += core.diffuse(wa, wb, n, pin_r, want_r, anchor=spread[step] if step < len(spread) else 4e-4, bend=bend)[rep]
        if residual < 2e-4:
            break
    if not active.all():  # the lining rides along with the shell it belongs to, or the shell would cut through it
        shell = np.nonzero(active)[0]
        tree = KDTree(len(shell))
        for k, i in enumerate(shell):
            tree.insert(x0[i].tolist(), k)
        tree.balance()
        for i in np.nonzero(~active)[0]:
            x[i] = x0[i] + (x[shell[tree.find(x0[i].tolist())[1]]] - x0[shell[tree.find(x0[i].tolist())[1]]])
        for _ in range(3):  # a buried vertex goes where the sheet over it went, by the triangle and not the nearest corner
            for i, (tri, bary) in under.items():
                x[i] = x0[i] + (bary / max(bary.sum(), 1e-9)) @ (x[tri] - x0[tri])
    label = core.components(n, a, b)
    sizes = np.bincount(label)
    small, band = loose_pieces(x0, label)
    carried = small | band
    if len(sizes) > 1 and carried.any() and not carried.all():
        # small loose pieces (pockets, buckles, belt loops) are hard: they are carried along with the cloth they sit on,
        # not bent. They have no edges to the big pieces, so on their own they would stay where they were.
        big = np.nonzero(~carried[label])[0]
        tree = KDTree(len(big))
        for k, i in enumerate(big):
            tree.insert(x0[i].tolist(), k)
        tree.balance()
        for p in np.nonzero(band)[0]:  # a band rides on what is under each vertex of it
            for i in np.nonzero(label == p)[0]:
                near = tree.find_n(x0[i].tolist(), 8)
                w = np.array([1.0 / (d + 1e-3) for _, _, d in near])
                x[i] = x0[i] + (w[:, None] * np.array([x[big[k]] - x0[big[k]] for _, k, _ in near])).sum(axis=0) / w.sum()
        for p in np.nonzero(small)[0]:
            rows = label == p
            c0, c1 = x0[rows].mean(axis=0), x[rows].mean(axis=0)
            near = [big[k] for _, k, _ in tree.find_n(c0.tolist(), 16)]
            carried = (x[near] - x0[near]).mean(axis=0)
            d0, d1 = x0[rows] - c0, x[rows] - c1
            s = float(np.clip((d0 * d1).sum() / max((d0 * d0).sum(), 1e-12), 0.8, 1.0))  # a strap shrinks with its sleeve
            x[rows] = c0 + carried + s * d0
    touched = unfold(x0, x, outfits.triangles_of(ob), a, b) & active
    moved = (np.linalg.norm(x - x0, axis=1) > 0.003) & active & ~touched  # crinkles left by the pushing are ironed out
    deg = np.maximum(np.bincount(wa, minlength=n) + np.bincount(wb, minlength=n), 1)[:, None]
    for _ in range(4):
        step = x - x0
        total = np.zeros_like(step)
        np.add.at(total, wa, step[wb])
        np.add.at(total, wb, step[wa])
        x[moved] = x0[moved] + 0.5 * step[moved] + 0.5 * (total / deg)[rep[moved]]
    # Smoothing must not sink the cloth into the body again. The push back out is smoothed too: projecting every vertex
    # on its own copies the skin's facets and turns a straight hem into teeth.
    rim = smooth_rim(x0, x, outfits.triangles_of(ob), rep, normals=facing_out) & active
    redo = np.nonzero((touched | moved | rim) & active)[0]
    back = np.zeros((n, 3))
    for i in redo:
        loc, nrm, _, off = body.query(x[i])
        if off < gv[i]:
            back[i] = nrm * (gv[i] - off)
    for _ in range(3):
        total = np.zeros_like(back)
        np.add.at(total, wa, back[wb])
        np.add.at(total, wb, back[wa])
        mix = 0.5 * back[redo] + 0.5 * (total / deg)[rep[redo]]
        bigger = np.linalg.norm(mix, axis=1) > np.linalg.norm(back[redo], axis=1)
        back[redo] = np.where(bigger[:, None], mix, back[redo])
    x[redo] += back[redo]
    move_garment(ob, x)
    after = np.array([body.query(p)[3] for p in x[active]])
    return SimpleNamespace(moved=float(np.linalg.norm(x - x0, axis=1).max()), inside_before=inside_before,
                           inside_after=int((after < gv[active] - 5e-4).sum()), vertices=n, room=gv)


def tucked_under(ob, before):
    """Which vertices of `ob` sit inside the garments in `before` ({name: BVH of that garment's shape before the fit}): a
    shirt hem tucked into trousers. Looks outward from each vertex: if it meets the other garment from behind, the vertex
    is inside it. Returns {name: (mask over the vertices now, their coordinates now)}."""
    x = coords(ob.data)
    out = {}
    nr = outfits.normals_local(ob)
    a, b = garment_edges(ob)
    deg = np.maximum(np.bincount(a, minlength=len(x)) + np.bincount(b, minlength=len(x)), 1)
    for name, bvh in before.items():
        mask = np.zeros(len(x), dtype=bool)
        for i in range(len(x)):
            d = Vector(nr[i].tolist())
            hit = bvh.ray_cast(Vector((x[i] + nr[i] * 1e-4).tolist()), d, TUCK_REACH)
            mask[i] = hit[0] is not None and hit[1].dot(d) > 0.3
        for _ in range(2):  # a hem is tucked along its length: a vertex that is the only one is a stray ray, not a tuck
            votes = np.bincount(a, weights=mask[b], minlength=len(x)) + np.bincount(b, weights=mask[a], minlength=len(x))
            mask = mask & (votes >= 0.5 * deg)
        both = mask[a] & mask[b]
        if both.any():  # a tuck runs along a whole hem; a small patch is a flap of the cloth that happens to lie inside it
            label = core.components(len(x), a[both], b[both])
            sizes = np.bincount(label[mask], minlength=label.max() + 1)
            mask = mask & (sizes[label] >= TUCK_LEAST)
        if mask.sum() > 3:
            out[name] = (mask, x.copy())
    return out


def envelope(ob, rounds=6, hops=4, most=0.012):
    """The shape of a garment as something to lie over: the cloth smoothed (so the wrinkles and tips of what is under are not
    followed one by one), then lifted back to cover every crest within `hops` rows. A jacket over a shirt rides on the
    shirt's folds, it does not crawl into each one. Returns the coordinates."""
    x = coords(ob.data)
    n = len(x)
    a, b = garment_edges(ob)
    rep = core.weld(x)
    keep = rep[a] != rep[b]
    wa, wb = rep[a][keep], rep[b][keep]
    deg = np.maximum(np.bincount(wa, minlength=n) + np.bincount(wb, minlength=n), 1)[:, None]
    smooth = x.copy()
    for _ in range(rounds):
        total = np.zeros_like(smooth)
        np.add.at(total, wa, smooth[wb])
        np.add.at(total, wb, smooth[wa])
        smooth = (0.5 * smooth + 0.5 * total / deg)[rep]
    nr = outfits.normals_local(ob)
    crest = np.clip(((x - smooth) * nr).sum(axis=1), 0.0, most)
    for _ in range(hops):
        top = crest.copy()
        np.maximum.at(top, wa, crest[wb])
        np.maximum.at(top, wb, crest[wa])
        crest = top[rep]
    return smooth + nr * crest[:, None]


def smooth_prefit(ob, body, layers, tucked, gap, rounds, sigma=0.04, centres=600, step=0.0015):
    """Lay the garment over the body with one smooth movement of space, before any vertex is pushed on its own. The cloth is
    still in the shape it was designed in (after the skeleton fit). Each round asks every vertex how far it would have to
    go to be out of the skin (`gap`) and over the garments under it (`layers`, {name: BVH}; what is in `tucked` is meant to
    be under them and is left alone), then moves the whole garment by a smooth field that follows those requests: a sum of
    soft bumps (Gaussians `sigma` wide) fitted by least squares, so the cloth keeps its folds, hems and collars and only
    swells where the body is bigger. What is still short after the last round is left to the fit. Returns how many
    vertices were still asked to move."""
    x = coords(ob.data)
    n = len(x)
    if rounds <= 0 or n < 50:
        return None
    rep = core.weld(x)
    shell = outward_vertices(ob, body, x)
    facing = outfits.normals_local(ob)
    pick = [0]
    dist = np.linalg.norm(x - x[0], axis=1)
    for _ in range(min(centres, n) - 1):  # centres spread evenly over the cloth
        i = int(np.argmax(dist))
        pick.append(i)
        dist = np.minimum(dist, np.linalg.norm(x - x[i], axis=1))
    centre = x[pick]
    unique = np.unique(rep)
    keep = np.array([not any(m[i] for m in tucked.values()) for i in range(n)]) if tucked else np.ones(n, dtype=bool)

    def asked(p):
        push = np.zeros((n, 3))
        for i in unique:
            if not shell[i]:
                continue
            _, nrm, _, off = body.query(p[i])
            best = None
            if off < gap and nrm @ facing[i] > 0.2 and gap - off < 0.08:
                best = nrm * (gap - off)
            if keep[i]:
                for bvh in layers.values():
                    loc, face_n, _, _ = bvh.find_nearest(Vector(p[i].tolist()), 0.06)
                    if loc is None:
                        continue
                    way = np.array(face_n)
                    if abs(way @ nrm) < 0.5:
                        continue
                    if way @ nrm < 0.0:
                        way = -way
                    height = float((p[i] - np.array(loc)) @ way)
                    if height < step and step - height < 0.06:
                        q = way * (step - height)
                        if body.query(p[i] + q)[3] > 0.0005 and (best is None or np.linalg.norm(q) > np.linalg.norm(best)):
                            best = q
            if best is not None:
                push[i] = best
        return push[rep]

    left = 0
    for _ in range(rounds):
        push = asked(x)
        left = int((np.linalg.norm(push, axis=1) > 1e-4).sum())
        if not left:
            break
        phi = np.exp(-((x[:, None, :] - centre[None, :, :]) ** 2).sum(-1) / (2.0 * sigma * sigma))
        coef = np.linalg.solve(phi.T @ phi + 1e-4 * np.eye(len(pick)), phi.T @ push)
        x = (x + 1.2 * (phi @ coef))[rep]
    move_garment(ob, x)
    return left


def tuck_masks(tucks, x):
    """`tucks` (from tucked_under) as masks over the vertices `x` now: vertices added after the fit take what their
    nearest old one was."""
    n = len(x)
    out = {}
    for name, (mask, was) in tucks.items():
        full = np.zeros(n, dtype=bool)
        old = np.nonzero(mask)[0]
        if len(old):
            tree = KDTree(len(old))
            for k, i in enumerate(old):
                tree.insert(was[i].tolist(), k)
            tree.balance()
            for i in range(n):
                if i < len(mask):
                    full[i] = mask[i]
                elif tree.find(x[i].tolist())[2] < 0.015:
                    full[i] = True
        out[name] = full
    return out


def membrane_fit(ob, body, room, designed, layers, tucked, iterations=200, hold=0.01, step=0.0015, margin=0.0005, floor=0.001):
    """Carry the garment onto the body the way a stretched membrane lies over obstacles. What the fit did to each vertex
    (its distance from where it was designed) is kept as a smooth field: every round the field is averaged with its
    neighbours, a little is pulled back toward what the fit made, and then every vertex that would be inside a wall is
    put exactly on the wall. The walls are the skin (`room[i]` away, per vertex), the garments under it (`layers`,
    {name: BVH of that garment as it is now}: stay `step` over them) and what was tucked into them (`tucked`: stay `step`
    behind their surface). Vertices that touch a wall are held there; everything else relaxes between them, so a hem is a
    curve instead of teeth, and the designed folds stay. Returns how many vertices are on a wall at the end."""
    x = coords(ob.data)
    n = len(x)
    if designed is None or len(designed) != n or iterations <= 0:
        return None
    a, b = garment_edges(ob)
    rep = core.weld(x)
    keep = rep[a] != rep[b]
    wa, wb = rep[a][keep], rep[b][keep]
    deg = np.maximum(np.bincount(wa, minlength=n) + np.bincount(wb, minlength=n), 1)[:, None]
    nr = outfits.normals_local(ob)
    shell = outward_vertices(ob, body, x)
    unique = np.unique(rep)
    room = np.asarray(room, dtype=float)[:n].copy()
    for _ in range(8):  # the room is a count of sheets under each vertex: whole steps that differ from one vertex to the next are teeth
        total = np.zeros(n)
        np.add.at(total, wa, room[wb])
        np.add.at(total, wb, room[wa])
        room = (0.5 * room + 0.5 * total / deg[:, 0])[rep]
    capless = np.array([body.query(x[i])[1] @ nr[i] >= 0.2 for i in range(n)])  # a cap inside the garment follows the cloth
    fit = x - designed
    field = fit.copy()

    def wall(i, p):
        """The push that puts vertex i, now at p, on the nearest wall it is beyond (None if it is clear), and how far it
        is from the walls."""
        best, near = None, 1.0
        _, nrm, _, off = body.query(p)
        if shell[i]:
            want = room[i] + margin
            near = min(near, abs(off - want))
            if off < want and want - off < MEMBRANE_REACH and nrm @ nr[i] > 0.2:
                best = nrm * (want - off)
        for name, bvh in layers.items() if shell[i] and capless[i] else ():  # a lining follows its outer sheet, below
            loc, face_n, _, dist = bvh.find_nearest(Vector(p.tolist()), 0.04)
            if loc is None:
                continue
            way = np.array(face_n)
            if abs(way @ nrm) < 0.5:  # the nearest thing is the edge of that garment, not its face: no way to tell over from under
                continue
            if way @ nrm < 0.0:  # out is the way the skin faces
                way = -way
            height = float((p - np.array(loc)) @ way)
            if tucked.get(name) is not None and tucked[name][i]:
                want, ok = -step - height, height > -step and height + step < MEMBRANE_REACH
            else:
                want, ok = step - height, height < step and step - height < MEMBRANE_REACH
            near = min(near, dist)
            if ok:
                push = way * want
                if body.query(p + push)[3] < floor:
                    continue
                if best is None or np.linalg.norm(push) > np.linalg.norm(best):
                    best = push
        return best, near

    link = np.full(n, -1)  # a lining (faces the body) goes where the outer sheet next to it goes
    outer = np.nonzero(shell)[0]
    if len(outer) and not shell.all():
        tree = KDTree(len(outer))
        for k, i in enumerate(outer):
            tree.insert(designed[i].tolist(), k)
        tree.balance()
        for i in np.nonzero(~shell)[0]:
            _, k, d = tree.find(designed[i].tolist())
            if d < 0.01:
                link[i] = outer[k]
    inner = np.nonzero(link >= 0)[0]

    def settle(cur):
        """Put every vertex that is beyond a wall on it; the linings follow. Returns how many were pushed."""
        done = np.zeros((n, 3))
        for i in candidates:
            push, _ = wall(i, cur[i])
            if push is not None:
                done[i] = push
        if len(inner):
            done[inner] = done[link[inner]]
        field[:] += done
        field[:] = field[rep]
        return int((np.linalg.norm(done, axis=1) > 1e-9).sum())

    candidates = unique
    on_wall = 0
    for step_no in range(iterations):
        if step_no % 15 == 0:  # who is close enough to a wall to matter before the next look
            cur = designed + field
            keep_i = []
            for i in unique:
                push, near = wall(i, cur[i])
                if push is not None or near < 0.01:
                    keep_i.append(i)
            candidates = np.array(keep_i, dtype=int)
        total = np.zeros_like(field)
        np.add.at(total, wa, field[wb])
        np.add.at(total, wb, field[wa])
        field = field + 0.5 * (total / deg - field) + hold * (fit - field)
        field[:] = field[rep]
        on_wall = settle(designed + field)
    candidates = unique
    for _ in range(3):  # last: nothing is left inside a wall
        settle(designed + field)
    after = designed + field
    if bumps(after - designed, wa, wb, deg, rep) > bumps(x - designed, wa, wb, deg, rep):
        return None  # it came out rougher than the fit it started from: that fit stays
    move_garment(ob, after)
    return on_wall


def bumps(field, wa, wb, deg, rep, limit=0.003):
    """How many vertices stick out of the smooth field they sit in: what is left of `field` after the average of its
    neighbours is taken away, longer than `limit`."""
    total = np.zeros_like(field)
    np.add.at(total, wa, field[wb])
    np.add.at(total, wb, field[wa])
    return int((np.linalg.norm(field - total / deg, axis=1)[np.unique(rep)] > limit).sum())


def near_rim(ob, rep, hops=2):
    """Vertices within `hops` rows of the open edge of the cloth (a hem, a collar, a cuff)."""
    n = len(rep)
    tris = rep[outfits.triangles_of(ob)]
    pairs = np.sort(np.concatenate([tris[:, [0, 1]], tris[:, [1, 2]], tris[:, [2, 0]]]), axis=1)
    edges, count = np.unique(pairs, axis=0, return_counts=True)
    near = np.zeros(n, dtype=bool)
    ra, rb = edges[count == 1].T
    near[ra] = near[rb] = True
    ea, eb = edges.T
    for _ in range(hops):
        grown = near.copy()
        grown[ea[near[eb]]] = True
        grown[eb[near[ea]]] = True
        near = grown
    return near[rep]


def ease_push(fix, touched, wa, wb, deg, rep, rounds=6):
    """A push spread over the cloth around it as one smooth field: the pushes asked for are averaged with each other over
    the cloth (a vertex that asked for nothing does not pull the average down), so the push rises and falls gently where it
    is needed instead of standing at each vertex on its own. Whatever is still short is asked for again next round."""
    weight = touched.astype(float)[:, None]
    push = fix * weight
    for _ in range(rounds):
        tw = np.zeros_like(weight)
        tp = np.zeros_like(push)
        np.add.at(tw, wa, weight[wb])
        np.add.at(tw, wb, weight[wa])
        np.add.at(tp, wa, push[wb])
        np.add.at(tp, wb, push[wa])
        weight = (0.5 * weight + 0.5 * tw / deg)[rep]
        push = (0.5 * push + 0.5 * tp / deg)[rep]
    return 1.25 * push / np.maximum(weight, 1e-3) * np.minimum(weight * 100.0, 1.0)


def keep_apart(ob, tucks, outer, step=0.0015, rounds=2, body=None, floor=0.001):
    """Garments do not overlap: what was fitted before is a collider, like the skin. A vertex of `ob` that ended up
    inside another garment is pushed out of it, along the way its own cloth faces. The exception is what was tucked into
    that garment before the fit (`tucks`): that is put back inside, `step` behind its surface. `outer` is {name: BVH of
    that garment as it is now}. With `body`, nothing is pushed to where it would be closer to the skin than `floor`: a tucked
    hem goes in behind the garment, never through the body. The push is spread to the cloth around it. Returns how many
    vertices moved."""
    n = len(coords(ob.data))
    a, b = garment_edges(ob)
    x = coords(ob.data)
    rep = core.weld(x)
    keep = rep[a] != rep[b]
    wa, wb = rep[a][keep], rep[b][keep]
    deg = np.maximum(np.bincount(wa, minlength=n) + np.bincount(wb, minlength=n), 1)[:, None]
    marked = set()
    mask_of = tuck_masks(tucks, x)
    for _ in range(rounds):
        x = coords(ob.data)
        nr = outfits.normals_local(ob)
        fix = np.zeros((n, 3))
        for name, bvh in outer.items():
            tucked = mask_of.get(name)
            for i in range(n):
                if body is not None and body.query(x[i])[1] @ nr[i] < 0.2:  # a cap inside the garment follows the cloth, it is not pushed
                    continue
                d = Vector(nr[i].tolist())
                hit = bvh.ray_cast(Vector((x[i] + nr[i] * 1e-4).tolist()), d, TUCK_REACH)
                inside = hit[0] is not None and hit[1].dot(d) > 0.3
                if tucked is not None and tucked[i]:
                    if inside:  # inside it already: is it deep enough?
                        behind = (hit[0] - Vector(x[i].tolist())).length
                        if behind < step:
                            way = -nr[i] * (step - behind)
                        else:
                            continue
                    else:  # outside it: back in, behind the surface
                        loc, face_n, _, dist = bvh.find_nearest(Vector(x[i].tolist()), 0.05)
                        if loc is None:
                            continue
                        inward = -np.array(face_n) if face_n.dot(d) > 0 else np.array(face_n)
                        way = np.array(loc) + inward * step - x[i]
                        if np.linalg.norm(way) >= 0.04:
                            continue
                elif inside:  # inside a garment that is under it: out, to just over its surface
                    way = nr[i] * ((hit[0] - Vector(x[i].tolist())).length + step)
                    if body is not None:  # out the way the skin faces: a hem's own normal points down, and down is through the trousers
                        sn = body.query(x[i])[1]
                        out = bvh.ray_cast(Vector((x[i] + sn * 1e-4).tolist()), Vector(sn.tolist()), TUCK_REACH)
                        if out[0] is not None and out[1].dot(Vector(sn.tolist())) > 0.3:
                            way = sn * ((out[0] - Vector(x[i].tolist())).length + step)
                    if np.linalg.norm(way) > 0.03:
                        continue
                else:
                    continue
                if body is not None and body.query(x[i] + way)[3] < floor:
                    continue
                if np.linalg.norm(way) > np.linalg.norm(fix[i]):
                    fix[i] = way
        touched = np.linalg.norm(fix, axis=1) > 1e-6
        if not touched.any():
            break
        marked |= set(np.nonzero(touched)[0].tolist())
        calm = near_rim(ob, rep)
        for m in mask_of.values():
            calm &= ~m  # a tucked hem is meant to move
        touched &= ~calm  # an edge pushed vertex by vertex is a row of shards: it follows what is behind it
        fix = ease_push(fix, touched, wa, wb, deg, rep)
        move_garment(ob, x + fix)
    return len(marked)


def keep_over(ob, room, gap, inner, step=LAYER_STEP / 1000.0, body=None):
    """Cloth that lies over another garment stays over it. A vertex with room beyond the gap (the fit found a garment
    between it and the skin) is held `step` outside the surface of `inner` (the garments fitted before, as they are now),
    which the fit alone only gets right to within a few millimetres: a shirt over trousers shows the trousers through it
    in teeth. The push is spread to the cloth around it. Returns how many vertices moved."""
    x = coords(ob.data)
    n = len(x)
    layered = np.nonzero(room[:n] > gap + 1e-4)[0]
    if not len(layered):
        return 0
    a, b = garment_edges(ob)
    rep = core.weld(x)
    keep = rep[a] != rep[b]
    wa, wb = rep[a][keep], rep[b][keep]
    deg = np.maximum(np.bincount(wa, minlength=n) + np.bincount(wb, minlength=n), 1)[:, None]
    nr = outfits.normals_local(ob)
    fix = np.zeros((n, 3))
    for i in layered:
        if body is not None and body.query(x[i])[1] @ nr[i] < 0.2:  # a cap inside the garment follows the cloth
            continue
        loc, face_n, _, dist = inner.find_nearest(Vector(x[i].tolist()), 0.02)
        if loc is None:
            continue
        way = np.array(face_n)
        toward = body.query(x[i])[1] if body is not None else nr[i]
        if abs(way @ toward) < 0.5:  # the edge of that garment, not its face
            continue
        if way @ toward < 0.0:
            way = -way
        deficit = step - float((x[i] - np.array(loc)) @ way)
        if 1e-5 < deficit < 0.015:
            fix[i] = way * deficit
    touched = np.linalg.norm(fix, axis=1) > 1e-6
    if not touched.any():
        return 0
    for _ in range(3):
        total = np.zeros_like(fix)
        np.add.at(total, wa, fix[wb])
        np.add.at(total, wb, fix[wa])
        mix = 0.5 * fix + 0.5 * (total / deg)[rep]
        fix = np.where((np.linalg.norm(mix, axis=1) > np.linalg.norm(fix, axis=1))[:, None], mix, fix)
    move_garment(ob, x + fix)
    return int(touched.sum())


def shell_constraints(x_rest, tris, rep):
    """What a sheet of cloth wants to keep, from the shape it was designed in (`x_rest`, vertices welded by `rep`): the
    length of every edge, and across every pair of triangles that share an edge the distance between the two far corners
    (that is what keeps a fold a fold, whichever way the cloth is turned). Returns (edge a, edge b, rest length, corner p,
    corner q, rest distance)."""
    t = rep[tris]
    edges = np.concatenate([t[:, [0, 1]], t[:, [1, 2]], t[:, [2, 0]]])
    far = np.concatenate([t[:, 2], t[:, 0], t[:, 1]])
    key = np.sort(edges, axis=1)
    order = np.lexsort((key[:, 1], key[:, 0]))
    key, far = key[order], far[order]
    same = np.all(key[1:] == key[:-1], axis=1)
    before = np.concatenate([[False], same[:-1]])
    after = np.concatenate([same[1:], [False]])
    first = np.nonzero(same & ~before & ~after)[0]  # an edge shared by exactly two triangles
    p, q = far[first], far[first + 1]
    ok = p != q
    p, q = p[ok], q[ok]
    uniq = np.unique(key, axis=0)
    ea, eb = uniq[:, 0], uniq[:, 1]
    keep = ea != eb
    ea, eb = ea[keep], eb[keep]
    l0 = np.linalg.norm(x_rest[ea] - x_rest[eb], axis=1)
    d0 = np.linalg.norm(x_rest[p] - x_rest[q], axis=1)
    solid = l0 > 1e-4  # edges that were nothing to begin with (a seam, a collapsed corner) are not something to keep
    folded = d0 > 1e-4
    return (ea[solid], eb[solid], l0[solid], p[folded], q[folded], d0[folded])


def distortion(x, c):
    """How far the cloth is from the shape it was designed in: the mean stretch of its edges and the mean change of its
    folds, both as a share. Lower is better."""
    ea, eb, l0, p, q, d0 = c
    stretch = float(np.mean(np.minimum(np.abs(np.linalg.norm(x[ea] - x[eb], axis=1) / np.maximum(l0, 1e-9) - 1.0), 1.0)))  # a speck of an edge cannot dominate
    fold = float(np.mean(np.minimum(np.abs(np.linalg.norm(x[p] - x[q], axis=1) / np.maximum(d0, 1e-9) - 1.0), 1.0))) if len(p) else 0.0
    return stretch, fold


def relax_garment(ob, body, room, designed, iterations=400, hold=0.03, stiff_edge=0.5, stiff_fold=0.25, log=print):
    """Give the cloth back as much of its designed shape as the body allows. The fit pushed it out of the way and spread
    the push, which leaves ripples, stretched triangles and crumpled hems that nobody designed. Here the cloth is relaxed
    like a real sheet: every edge and every fold pulls toward its designed length, a weak spring holds the cloth near
    where the fit put it, and the skin is a hard wall (`room` metres away, per vertex). Many rounds, each one small, so it
    settles instead of jumping. `designed` is the shape before the fit. Returns (distortion before, after) or None."""
    x = coords(ob.data)
    n = len(x)
    if designed is None or len(designed) != n or iterations <= 0:
        return None
    a, b = garment_edges(ob)
    rep = core.weld(x)
    tris = outfits.triangles_of(ob)
    c = shell_constraints(designed, tris, rep)
    ea, eb, l0, p, q, d0 = c
    before = distortion(x, c)
    anchor = x.copy()
    shell = outward_vertices(ob, body, x)
    facing = outfits.normals_local(ob)
    count_e = np.bincount(ea, minlength=n) + np.bincount(eb, minlength=n)
    count_f = np.bincount(p, minlength=n) + np.bincount(q, minlength=n)
    walls = np.nonzero(shell)[0]
    near = walls.copy()
    for step in range(iterations):
        push = np.zeros((n, 3))
        d = x[ea] - x[eb]
        l = np.linalg.norm(d, axis=1) + 1e-12
        pull = (stiff_edge * 0.5 * (l - l0) / l)[:, None] * d
        for k in range(3):
            push[:, k] -= np.bincount(ea, weights=pull[:, k], minlength=n)
            push[:, k] += np.bincount(eb, weights=pull[:, k], minlength=n)
        move = push / np.maximum(count_e, 1)[:, None]
        if len(p):
            d = x[p] - x[q]
            l = np.linalg.norm(d, axis=1) + 1e-12
            pull = (stiff_fold * 0.5 * (l - d0) / l)[:, None] * d
            fold = np.zeros((n, 3))
            for k in range(3):
                fold[:, k] -= np.bincount(p, weights=pull[:, k], minlength=n)
                fold[:, k] += np.bincount(q, weights=pull[:, k], minlength=n)
            move += fold / np.maximum(count_f, 1)[:, None]
        x = x + move
        x += hold * (anchor - x)
        x = x[rep]  # seam copies stay one vertex
        if step % 25 == 0:
            near = np.array([i for i in walls if body.query(x[i])[3] < room[i] + 0.012], dtype=int)
        for i in near:  # the skin is a wall
            loc, nrm, _, off = body.query(x[i])
            if off < room[i] and nrm @ facing[i] > 0.2 and room[i] - off < MOST_MOVE:
                x[i] = x[i] + nrm * (room[i] - off)
        x = x[rep]
    move_garment(ob, x)
    return before, distortion(x, c)


def straighten_rim(ob, designed, rounds):
    """A hem or a collar was cut straight and is a ragged line after everything that was done to it. How far each vertex
    of the edge is from where it was designed is smoothed along the edge, so the line comes back and the cloth keeps its
    place. Layers and the skin are enforced again afterwards. Returns how many rim vertices there are."""
    x = coords(ob.data)
    if designed is None or len(designed) != len(x) or rounds <= 0:
        return 0
    rep = core.weld(x)
    rim = smooth_rim(designed, x, outfits.triangles_of(ob), rep, rounds, normals=outfits.normals_local(ob))
    move_garment(ob, x)
    return int(rim.sum())


def keep_off_skin(ob, body, floor, rounds=3):
    """The last word on the body: after the layers and the cloth have had theirs, no vertex of the outer shell is left
    closer to the skin than `floor`. What is inside is pushed out the way the cloth faces, and the push is spread to the
    cloth around it. Returns how many vertices moved."""
    x = coords(ob.data)
    n = len(x)
    a, b = garment_edges(ob)
    rep = core.weld(x)
    keep = rep[a] != rep[b]
    wa, wb = rep[a][keep], rep[b][keep]
    deg = np.maximum(np.bincount(wa, minlength=n) + np.bincount(wb, minlength=n), 1)[:, None]
    shell = outward_vertices(ob, body, x)
    facing = outfits.normals_local(ob)
    fix = np.zeros((n, 3))
    for i in np.nonzero(shell)[0]:
        _, nrm, _, off = body.query(x[i])
        if off < floor and floor - off < MOST_MOVE and nrm @ facing[i] > 0.2:  # not over a pit, not a far-off part of the body
            fix[i] = nrm * (floor - off)
    touched = np.linalg.norm(fix, axis=1) > 1e-6
    if not touched.any():
        return 0
    for _ in range(rounds):
        total = np.zeros_like(fix)
        np.add.at(total, wa, fix[wb])
        np.add.at(total, wb, fix[wa])
        mix = 0.5 * fix + 0.5 * (total / deg)[rep]
        fix = np.where((np.linalg.norm(mix, axis=1) > np.linalg.norm(fix, axis=1))[:, None], mix, fix)
    move_garment(ob, x + fix)
    return int(touched.sum())


def cloth_polish(context, ob, body, room, stiffness=500.0, pin=0.85, frames=15, most=0.02, quality=6):
    """Settle a fitted garment with Blender's own cloth: stiff and mostly pinned to the shape it has, so it keeps its
    shape, with the skin as the only thing it collides with. `room` is the distance from the skin in metres (more for
    garments worn over others). Small separate pieces stay where they are. Only the basis moves: shape keys keep their
    deltas. A vertex the cloth would have put into the body stays where the fit left it, and cloth that comes out torn is
    thrown away. Returns how far it moved, how rough and how folded the result was, and whether it was used."""
    skins = body.skins
    scene = context.scene
    me = ob.data
    blocks = face.key_blocks(ob)
    x0 = coords(me, blocks[0]) if blocks else coords(me)
    n = len(x0)
    a, b = garment_edges(ob)
    label = core.components(n, a, b)
    sizes = np.bincount(label)
    small_p, band_p = loose_pieces(x0, label)
    small = np.nonzero((small_p | band_p)[label])[0] if len(sizes) > 1 else np.zeros(0, dtype=int)
    big = np.setdiff1d(np.arange(n), small)
    saved = (scene.frame_current, scene.frame_start, scene.frame_end)
    colliders = []
    shown = (ob.show_only_shape_key, ob.active_shape_key_index)
    if blocks:  # the cloth sees the basis: shape keys that are switched on (blendshapes) must not be baked into it
        ob.active_shape_key_index = 0
        ob.show_only_shape_key = True
    # Only cloth that lies against the skin and faces away from it is settled. A lining (it faces the body) and cloth that
    # hangs loose away from the skin have nothing to settle on: they are held where they are.
    cloth_n = outfits.normals_local(ob)
    free = np.zeros(n, dtype=bool)
    for i in big:
        _, skin_n, _, off = body.query(x0[i])
        free[i] = off < CLOTH_NEAR and skin_n @ cloth_n[i] > 0.2
    group = ob.vertex_groups.new(name="KAT pin")
    group.add(np.nonzero(~free)[0].tolist(), 1.0, "REPLACE")
    group.add(np.nonzero(free)[0].tolist(), pin, "REPLACE")
    mod = ob.modifiers.new("KAT cloth", "CLOTH")
    try:
        for s in skins:
            if not any(m.type == "COLLISION" for m in s.modifiers):
                colliders.append((s, s.modifiers.new("KAT collider", "COLLISION")))
            s.collision.thickness_outer = 0.0005
            s.collision.use_culling = False
        cs = mod.settings
        cs.quality = quality
        cs.mass = 0.5
        cs.vertex_group_mass = group.name
        cs.pin_stiffness = 1.0
        cs.tension_stiffness = cs.compression_stiffness = cs.shear_stiffness = cs.bending_stiffness = stiffness
        cs.tension_damping = cs.compression_damping = cs.shear_damping = 30.0
        cs.bending_damping = 5.0
        cs.air_damping = 5.0
        cs.effector_weights.gravity = 0.0  # it is settled onto the body, not dropped
        mod.collision_settings.use_collision = True
        mod.collision_settings.use_self_collision = False
        mod.collision_settings.distance_min = float(room)
        mod.point_cache.frame_start, mod.point_cache.frame_end = 1, frames
        scene.frame_start, scene.frame_end = 1, frames
        scene.frame_set(1)
        for f in range(2, frames + 1):
            scene.frame_set(f)
        ev = ob.evaluated_get(context.evaluated_depsgraph_get())
        tmp = ev.to_mesh()
        new = np.empty(len(tmp.vertices) * 3, dtype=np.float32)
        tmp.vertices.foreach_get("co", new)
        ev.to_mesh_clear()
    finally:
        ob.modifiers.remove(mod)
        ob.vertex_groups.remove(group)
        for s, m in colliders:
            s.modifiers.remove(m)
        ob.show_only_shape_key, ob.active_shape_key_index = shown
        scene.frame_start, scene.frame_end = saved[1], saved[2]
        scene.frame_set(saved[0])
    new = new.reshape(-1, 3).astype(np.float64)
    if len(new) != n or not np.isfinite(new).all():
        return SimpleNamespace(far=0.0, jitter=0.0, flips=0.0, used=False)
    step = new - x0
    length = np.linalg.norm(step, axis=1, keepdims=True)
    step *= np.minimum(1.0, most / np.maximum(length, 1e-12))  # a settling, not a rearranging
    def out_of_body(s):
        for i in np.nonzero(np.linalg.norm(s, axis=1) > 5e-4)[0]:  # what the cloth pushed into the body was not an improvement
            before, after = body.query(x0[i])[3], body.query(x0[i] + s[i])[3]
            if after < min(before, room) - 3e-4:
                s[i] = 0.0

    out_of_body(step)
    # Cloth that is loose or folded (leg warmers, a skirt) can come out of the simulation in shards. A settling moves
    # neighbours alike: where it does not, the movement is smoothed, and if the cloth still comes out torn it is not used.
    rep = core.weld(x0)
    keep = rep[a] != rep[b]
    wa, wb = rep[a][keep], rep[b][keep]
    deg = np.maximum(np.bincount(wa, minlength=n) + np.bincount(wb, minlength=n), 1)[:, None]
    tris = outfits.triangles_of(ob)

    def around(v):
        total = np.zeros_like(v)
        np.add.at(total, wa, v[wb])
        np.add.at(total, wb, v[wa])
        return (total / deg)[rep]

    def normals(p):
        """Unit normals, and whether the triangle is a proper one: slivers have no usable normal."""
        c = np.cross(p[tris[:, 1]] - p[tris[:, 0]], p[tris[:, 2]] - p[tris[:, 0]])
        area = np.linalg.norm(c, axis=1)
        longest = np.maximum.reduce([np.linalg.norm(p[tris[:, i]] - p[tris[:, (i + 1) % 3]], axis=1) for i in range(3)])
        return c / (area[:, None] + 1e-15), area > 0.1 * longest ** 2

    before_n, before_ok = normals(x0)

    def judge(s):
        """How much of the cloth moved unlike its neighbours (shards), and how much of it turned inside out."""
        jitter = float((np.linalg.norm(s - around(s), axis=1) > CLOTH_SHARD).mean())
        after_n, after_ok = normals(x0 + s)
        flips = float((((before_n * after_n).sum(axis=1) < 0.0) & before_ok & after_ok).mean())
        return jitter, flips

    jitter, flips = judge(step)
    if jitter > CLOTH_JITTER or flips > CLOTH_FLIPS:
        for _ in range(8):
            step = 0.5 * step + 0.5 * around(step)
        out_of_body(step)
        jitter, flips = judge(step)
    used = jitter <= CLOTH_JITTER and flips <= CLOTH_FLIPS
    if used:
        move_garment(ob, x0 + step)
    return SimpleNamespace(far=float(np.linalg.norm(step, axis=1).max()) if used else 0.0, jitter=jitter, flips=flips, used=used)


def fit_rigid(ob, body, gap, limit=0.006, iterations=12):
    """For hard parts (shoes, helmets, bags): keep the shape, move the whole piece out of the body. Each round the
    piece is pushed along the average way the body presses on it, a centimetre at most, up to `limit` in all. Hard parts
    that wrap a body part (a shoe around a foot) are not made to fit by moving them: the skin shrinks under them instead
    (the anti-clip shapes), so the push is small."""
    me = ob.data
    blocks = face.key_blocks(ob)
    x = coords(me, blocks[0]) if blocks else coords(me)
    active = outward_vertices(ob, body, x)
    sample = np.nonzero(active)[0][:: max(1, int(active.sum()) // 4000)]
    total = np.zeros(3)
    inside = 0
    for _ in range(iterations):
        push = np.zeros(3)
        weight = 0.0
        inside = 0
        for i in sample:
            _, nrm, _, off = body.query(x[i] + total)
            if off < gap - 1e-4:
                depth = gap - off
                push += nrm * depth * depth
                weight += depth
                inside += 1
        if not inside:
            break
        step = push / max(weight, 1e-9)
        if np.linalg.norm(step) > 0.01:
            step *= 0.01 / np.linalg.norm(step)
        total += step
        if np.linalg.norm(total) >= limit:
            break
    if np.linalg.norm(total) > 0:
        move_garment(ob, x + total)
    after = [body.query(x[i] + total)[3] for i in sample]
    return SimpleNamespace(moved=float(np.linalg.norm(total)), inside_before=0, inside_after=int(np.sum(np.array(after) < gap - 5e-4)),
                           vertices=len(x))


def lift_rigid(ob, body, inner, step=0.002, reach=0.03, limit=0.02):
    """A rigid piece worn over other garments (a tie over a shirt) is moved as one block until it lies `step` outside
    what is under it. Its shape is never touched: a tie fitted vertex by vertex comes out wavy. Returns how far it moved."""
    x = coords(ob.data)
    sample = np.arange(0, len(x), max(1, len(x) // 3000))
    total = np.zeros(3)
    for _ in range(6):
        push, count = np.zeros(3), 0
        for i in sample:
            p = x[i] + total
            loc, face_n, _, _ = inner.find_nearest(Vector(p.tolist()), reach)
            if loc is None:
                continue
            way = np.array(face_n)
            if way @ body.query(p)[1] < 0.0:  # a lining faces the body: out is away from the skin
                way = -way
            off = float((p - np.array(loc)) @ way)
            if off < step:
                push += way * (step - off)
                count += 1
        if not count:
            break
        total += push / count
        if np.linalg.norm(total) > limit:
            total *= limit / np.linalg.norm(total)
            break
    if np.linalg.norm(total) > 0:
        move_garment(ob, x + total)
    return float(np.linalg.norm(total))


def is_solid(ob, words):
    label = ob.name.lower()
    return any(w.strip().lower() in label for w in words.split(",") if w.strip())


# ---- 3. weights -------------------------------------------------------------------------------------------------

def refit_weights(ob, body, follows, hug_reach, rigid_words, rigid_auto):
    """Bone weights for `ob` on your rig: the outfit's own (moved onto your bones), blended with the body's where the
    cloth lies on the skin. Small or named-rigid pieces get one weight set for the whole piece."""
    me = ob.data
    n = len(me.vertices)
    nb = len(body.bones)
    own = np.zeros((n, nb))
    names = [g.name for g in ob.vertex_groups]
    for v in me.vertices:
        for g in v.groups:
            target = follows.get(names[g.group])
            col = body.index.get(target) if target else None
            if col is not None:
                own[v.index, col] += g.weight
    sums = own.sum(axis=1, keepdims=True)
    have = sums[:, 0] > 1e-9
    own = np.where(have[:, None], own / np.maximum(sums, 1e-12), 0.0)

    blocks = face.key_blocks(ob)
    co = coords(me, blocks[0]) if blocks else coords(me)
    sw = body.weights()
    skin = np.zeros((n, nb))
    near = np.zeros(n)
    for i in range(n):
        loc, _, tri, off = body.query_skin(co[i])
        ids = body.tris[tri]
        p = body.co[ids].astype(np.float64)
        v0, v1, v2 = p[1] - p[0], p[2] - p[0], loc - p[0]
        d00, d01, d11, d20, d21 = v0 @ v0, v0 @ v1, v1 @ v1, v2 @ v0, v2 @ v1
        den = max(d00 * d11 - d01 * d01, 1e-18)
        bv = (d11 * d20 - d01 * d21) / den
        bw = (d00 * d21 - d01 * d20) / den
        bary = np.clip([1.0 - bv - bw, bv, bw], 0.0, 1.0)
        bary /= max(bary.sum(), 1e-12)
        skin[i] = bary @ sw[ids]
        near[i] = abs(off)
    f = core.fall(near, hug_reach)
    # The skin the cloth is nearest to is not always the skin it belongs to (a leg warmer's inside faces the other
    # leg). Where its bones and the cloth's own have nothing in common, the cloth's own weights stay.
    trust = np.clip(np.minimum(own, skin).sum(axis=1) / 0.4, 0.0, 1.0)
    f = f * np.where(have, trust, 1.0)
    mix = np.where(have[:, None], f[:, None] * skin + (1.0 - f[:, None]) * own, skin)
    a, b = garment_edges(ob)
    mix = core.smooth_weights(mix, a, b, n, f > 0, passes=1, strength=0.35)
    mix = core.fix_outliers(mix, a, b, n)
    mix = core.limit_weights(mix, 4)

    pieces = np.full(n, -1)
    label = core.components(n, a, b)
    words = [w.strip().lower() for w in rigid_words.split(",") if w.strip()]
    label_names = " ".join([ob.name.lower()] + [s.material.name.lower() for s in ob.material_slots if s.material])
    named = any(w in label_names for w in words)
    rigid_count = 0
    sizes = np.bincount(label)
    if named:  # a tie is one thing: its knot and its cord must not move on their own, only bend along with the chest
        every = np.ones(n, dtype=bool)
        mix = core.limit_weights(core.smooth_weights(mix, a, b, n, every, passes=60, strength=0.5), 4)
        rigid_count = 1
    for p in range(len(sizes)):
        if not named and rigid_auto and len(sizes) > 1 and sizes[p] <= max(40, 0.03 * n):
            pieces[label == p] = p
            rigid_count += 1
    if rigid_count:
        mix = core.limit_weights(core.rigid_pieces(mix, pieces), 4)
    return mix, rigid_count


def write_weights(ob, body, w):
    for g in list(ob.vertex_groups):
        ob.vertex_groups.remove(g)
    groups = {}
    for col in np.nonzero(w.max(axis=0) > 1e-4)[0]:
        groups[col] = ob.vertex_groups.new(name=body.bones[col])
    for col, group in groups.items():
        rows = np.nonzero(w[:, col] > 1e-4)[0]
        for i in rows:
            group.add([int(i)], float(w[i, col]), "REPLACE")


def bind(ob, rig):
    for m in [m for m in ob.modifiers if m.type == "ARMATURE"]:
        ob.modifiers.remove(m)
    mod = ob.modifiers.new("Armature", "ARMATURE")
    mod.object = rig
    ob.parent = rig
    ob.matrix_parent_inverse = rig.matrix_world.inverted()


# ---- the whole thing --------------------------------------------------------------------------------------------

def is_prop(ob, words):
    """Whole words of the mesh's name (digits and punctuation split them), so Backpack is not a Pack."""
    have = {w.lower() for w in re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])", ob.name)}
    have |= {w[:-1] for w in have if w.endswith("s")}  # Helmets is a helmet
    return any(w.strip().lower() in have for w in words.split(",") if w.strip())


FINISH_STEPS = ("props", "list", "shapes", "on")


def finish_outfit(context, skins, garments, props_words=PROP_WORDS, log=print, steps=FINISH_STEPS, poses=None):
    """What you would do by hand after fitting, each step on its own: hide what is only carried ("props"), list the
    rest in layer order, inner first ("list"), make the fit shapes ("shapes") and switch on the ones for what is worn
    ("on"). Props are left out of the layers whatever the steps. Returns (the anti-clip result or None, hidden names)."""
    props = [ob for ob in garments if is_prop(ob, props_words)] if props_words.strip() else []
    if "props" in steps:
        for ob in props:
            ob.hide_viewport = ob.hide_render = True
    else:
        props = []
    worn = [ob for ob in garments if ob not in props]
    s = outfits.settings(context)
    listed = outfits.outfit_objects(context)
    layers = sorted(listed + [ob for ob in worn if ob not in listed],
                    key=lambda ob: ob["kat_layer"] if "kat_layer" in ob else 100 + outfits.mean_distance(skins, ob, context))
    if "list" in steps:
        s.items.clear()
        for ob in layers:
            s.items.add().obj = ob
        log("layers, inner first: " + ", ".join(ob.name for ob in layers))
    done = None
    if "shapes" in steps and layers:
        done = outfits.run_fit(context, skins, layers, s.gap / 1000.0, poses or s.quality, s.max_push / 1000.0, s.skin_mode)
    if "on" in steps:
        names = {ob.name for ob in layers}
        for ob in bpy.data.objects:
            for key in (face.key_blocks(ob) or []) if ob.type == "MESH" else []:
                if key.name.startswith(outfits.FIT) and key.name[len(outfits.FIT):] in names:
                    key.value = 1.0
    return done, [ob.name for ob in props]


def attempt(log, what, call, fallback=None):
    """The steps that only polish a fit (the cloth, the layers, a tie lifted over a shirt): if one of them fails on a mesh
    nobody thought of, the fit stands as it was, and the reason is in the log."""
    try:
        return call()
    except Exception as error:  # noqa: BLE001
        log(f"{what} skipped: {error}")
        return fallback


def refit_outfit(context, src_rig, skins, dst_rig, garments=None, gap=2.0, tighten=0.2, reach=25.0, rigid=True,
                 rigid_words=", ".join(RIGID_WORDS[:7]), solid_words=SOLID_WORDS, log=print, finish=False,
                 props_words=PROP_WORDS, cloth=True, stiffness=500.0, progress=None, keep_original=True, mode="BALANCED"):
    """Fit, weight and bind an outfit. `gap` and `reach` in millimetres. Returns a result with one row per garment."""
    garments = list(garments or [o for o in bpy.data.objects if o.type == "MESH" and o.find_armature() == src_rig])
    if not garments:
        raise ValueError(f"No meshes are rigged to {src_rig.name}")
    skins = list(skins)
    if not skins:
        raise ValueError("Add the skin first (Outfits panel)")
    started = time.time()
    rows = []
    how = MODES.get(mode, MODES["BALANCED"])
    with basis_state(skins, [dst_rig]):
        log(f"skeleton: {src_rig.name} onto {dst_rig.name}")
        follows = fit_skeleton(garments, src_rig, dst_rig, [g for g in garments if is_solid(g, solid_words)])
        if keep_original or how["relax"]:  # the relaxing needs the shape the cloth was designed in
            for g in garments:
                attempt(log, f"{g.name}: keeping the original shape", lambda g=g: keep_original_shape(g))
        body = Body(context, skins, dst_rig)
        order = sorted(garments, key=lambda g: float(np.median([body.query(p)[3] for p in coords(g.data)[:: max(1, len(g.data.vertices) // 200)]])))
        for rank, ob in enumerate(order):
            ob["kat_layer"] = rank  # inner to outer, as fitted: the fit shapes later use the same order
        log("layers, inner first: " + ", ".join(o.name for o in order))
        shapes = {g.name: (coords(g.data), outfits.triangles_of(g)) for g in garments}

        def others_bvh(ob):
            """One BVH of everything else in the outfit, as it is after the skeleton fit and before any surface fit."""
            rest = [v for k, v in shapes.items() if k != ob.name]
            if not rest:
                return None
            offset = np.cumsum([0] + [len(c) for c, _ in rest])
            tris = np.concatenate([t + o for (_, t), o in zip(rest, offset)])
            return outfits.build_bvh(np.concatenate([c for c, _ in rest]).astype(np.float32), tris)

        solids = [g for g in order if is_solid(g, solid_words)]
        fitted = []  # garments fitted so far, inner first, as they are now
        for turn, ob in enumerate(order):
            if progress is not None:
                progress(turn / max(len(order), 1))
            rigid_named = rigid and is_solid(ob, rigid_words) and not is_solid(ob, solid_words)
            if is_solid(ob, solid_words) or rigid_named:  # a tie is as hard as a shoe: it keeps its shape, and moves as one
                report = fit_rigid(ob, body, gap / 1000.0)
                if rigid_named:
                    under = [g for g in fitted if not is_prop(g, props_words) and not is_solid(g, solid_words)]
                    if under:
                        def lift():
                            offset = np.cumsum([0] + [len(coords(g.data)) for g in under])
                            return lift_rigid(ob, body, outfits.build_bvh(
                                np.concatenate([coords(g.data) for g in under]).astype(np.float32),
                                np.concatenate([outfits.triangles_of(g) + o for g, o in zip(under, offset)])))
                        lifted = attempt(log, f"{ob.name}: lifting it over the clothes", lift, 0.0)
                        if lifted:
                            log(f"{ob.name}: lifted {lifted * 1000:.1f} mm over the garments under it")
                    fitted.append(ob)
                weights, nrigid = refit_weights(ob, body, follows, max(reach / 1000.0, 0.015), rigid_words, rigid)
                write_weights(ob, body, weights)
                bind(ob, dst_rig)
                rows.append(SimpleNamespace(name=ob.name, report=report, rigid=nrigid))
                log(f"{ob.name}: kept its shape, moved {report.moved * 1000:.0f} mm, inside after {report.inside_after}")
                continue
            # Where one garment lies over another, it sits a little further out, or the two end up in the same place
            # and show through each other. Which is over which is read from where they were before the fit.
            over = others_bvh(ob)
            tucks = attempt(log, f"{ob.name}: finding what is tucked in", lambda: tucked_under(ob, {
                g.name: outfits.build_bvh(shapes[g.name][0].astype(np.float32), shapes[g.name][1])
                for g in order[:order.index(ob)] if g.name in shapes}), {})
            if how["prefit"] and not rigid_named:
                under = {g.name: outfits.build_bvh(envelope(g).astype(np.float32), outfits.triangles_of(g))
                         for g in fitted if not is_prop(g, props_words) and not is_solid(g, solid_words)}
                left = attempt(log, f"{ob.name}: laying it over the body in one movement", lambda: smooth_prefit(
                    ob, body, under, tuck_masks(tucks, coords(ob.data)), gap / 1000.0, how["prefit"], sigma=how["sigma"]), None)
                if left is not None:
                    log(f"{ob.name}: smooth pre-fit, {left} vertices still asked to move")
            fine, cuts, cap, hidden = how["densify"]
            densify(ob, body, gap / 1000.0, fine, cuts, cap, hidden)
            report = fit_to_body(ob, body, gap / 1000.0, reach / 1000.0, tighten, how["iterations"], over=over, bend=how["bend"])
            for _ in range(how["refits"]):  # pushing stretches the cloth: cut the triangles that got long, and fit again
                if not densify(ob, body, gap / 1000.0, fine, cuts, cap, hidden):
                    break
                again = fit_to_body(ob, body, gap / 1000.0, reach / 1000.0, 0.0, how["iterations"], over=over, bend=how["bend"])  # tightening once: every fit does it again
                report.inside_after, report.vertices, report.room = again.inside_after, again.vertices, again.room
                report.moved = max(report.moved, again.moved)
            for _ in range(how["verify"]):  # what still sits inside the body is pushed out again, until nothing does
                if report.inside_after <= max(2, 0.0005 * report.vertices):
                    break
                again = fit_to_body(ob, body, gap / 1000.0, reach / 1000.0, 0.0, how["iterations"], over=over, bend=how["bend"])
                report.inside_after, report.room = again.inside_after, again.room
            if how["relax"] and ob.data.shape_keys is not None and ORIGINAL in ob.data.shape_keys.key_blocks:
                designed = coords(ob.data, ob.data.shape_keys.key_blocks[ORIGINAL])
                gone = attempt(log, f"{ob.name}: relaxing the cloth", lambda: relax_garment(
                    ob, body, report.room, designed, how["relax"]))
                if gone:
                    log(f"{ob.name}: relaxed toward its designed shape, stretch {gone[0][0] * 100:.1f}% -> {gone[1][0] * 100:.1f}%, folds {gone[0][1] * 100:.1f}% -> {gone[1][1] * 100:.1f}%")
            if how["cloth"] and cloth and 50 <= len(ob.data.vertices) <= CLOTH_MAX and not is_solid(ob, rigid_words) and not is_prop(ob, props_words):
                layer = [g for g in order if g not in solids].index(ob)
                done = attempt(log, f"{ob.name}: the cloth step", lambda: cloth_polish(
                    context, ob, body, gap / 1000.0 + LAYER_STEP / 1000.0 * layer, stiffness, frames=how["frames"], quality=how["cloth_quality"]))
                if done is not None:
                    log(f"{ob.name}: " + (f"settled with cloth, moved up to {done.far * 1000:.1f} mm ({done.jitter * 100:.2f}% shards, {done.flips * 100:.2f}% folded)" if done.used else
                                          f"cloth left alone ({done.jitter * 100:.1f}% shards, {done.flips * 100:.1f}% folded)"))
            union = [g for g in fitted if not is_prop(g, props_words)]
            if union:
                def hold_over():
                    offset = np.cumsum([0] + [len(coords(g.data)) for g in union])
                    inner = outfits.build_bvh(np.concatenate([envelope(g) for g in union]).astype(np.float32),
                                              np.concatenate([outfits.triangles_of(g) + o for g, o in zip(union, offset)]))
                    return keep_over(ob, report.room, gap / 1000.0, inner, body=body)
                lifted = attempt(log, f"{ob.name}: holding it over the clothes under it", hold_over, 0)
                if lifted:
                    log(f"{ob.name}: {lifted} vertices held over the garments under it")
            fitted.append(ob)
            before = [g for g in fitted[:-1] if not is_prop(g, props_words) and not is_solid(g, solid_words)]
            if before:
                put = attempt(log, f"{ob.name}: keeping it out of the clothes under it", lambda: keep_apart(ob, tucks, rounds=how["apart"], body=body, floor=gap / 2000.0, outer={
                    g.name: outfits.build_bvh(envelope(g).astype(np.float32), outfits.triangles_of(g)) for g in before}), 0)
                if put:
                    log(f"{ob.name}: {put} vertices moved out of the garments under it")
            for _ in range(2 if how["verify"] else 1):
                off = attempt(log, f"{ob.name}: keeping it off the skin", lambda: keep_off_skin(ob, body, gap / 2000.0), 0)
                if not off:
                    break
                log(f"{ob.name}: {off} vertices moved off the skin")
            if how["membrane"] and ob.data.shape_keys is not None and ORIGINAL in ob.data.shape_keys.key_blocks and not is_solid(ob, rigid_words) and not is_solid(ob, solid_words) and not is_prop(ob, props_words):
                under = [g for g in fitted[:-1] if not is_prop(g, props_words) and not is_solid(g, solid_words)]

                def lay():
                    return membrane_fit(
                        ob, body, report.room, coords(ob.data, ob.data.shape_keys.key_blocks[ORIGINAL]),
                        {g.name: outfits.build_bvh(envelope(g).astype(np.float32), outfits.triangles_of(g)) for g in under},
                        tuck_masks(tucks, coords(ob.data)), how["membrane"], floor=gap / 2000.0)
                held = attempt(log, f"{ob.name}: laying it over the body", lay, None)
                if held is not None:
                    log(f"{ob.name}: laid over the body, {held} vertices on a wall")
            if how["rim"] and ob.data.shape_keys is not None and ORIGINAL in ob.data.shape_keys.key_blocks:
                # last, because the layers and the skin each pushed single rim vertices; then the skin gets the final word
                attempt(log, f"{ob.name}: straightening its edges", lambda: straighten_rim(
                    ob, coords(ob.data, ob.data.shape_keys.key_blocks[ORIGINAL]), how["rim"]))
                attempt(log, f"{ob.name}: keeping it off the skin", lambda: keep_off_skin(ob, body, gap / 2000.0), 0)
            if attempt(log, f"{ob.name}: refreshing its shading", lambda: fresh_normals(context, ob), False):
                log(f"{ob.name}: shading follows the fitted shape")
            weights, nrigid = refit_weights(ob, body, follows, max(reach / 1000.0, 0.015), rigid_words, rigid)
            write_weights(ob, body, weights)
            bind(ob, dst_rig)
            rows.append(SimpleNamespace(name=ob.name, report=report, rigid=nrigid))
            log(f"{ob.name}: inside {report.inside_before} -> {report.inside_after}, moved up to {report.moved * 1000:.0f} mm, "
                f"{nrigid} rigid piece(s)")
    if not keep_original:
        for g in garments:  # the original was only kept for the relaxing
            if g.data.shape_keys is not None and ORIGINAL in g.data.shape_keys.key_blocks:
                g.shape_key_remove(g.data.shape_keys.key_blocks[ORIGINAL])
                if len(g.data.shape_keys.key_blocks) == 1:
                    g.shape_key_clear()
    if src_rig.name in bpy.data.objects and not [o for o in bpy.data.objects if o.parent == src_rig]:
        bpy.data.objects.remove(src_rig)
    try:
        textures.auto_find(garments)
    except Exception:  # a convenience, never a reason for the fit to fail
        pass
    clip, hidden = None, []
    steps = FINISH_STEPS if finish is True else tuple(finish or ())
    if steps:
        clip, hidden = finish_outfit(context, skins, garments, props_words, log, steps, how["poses"])
    return SimpleNamespace(rows=rows, seconds=time.time() - started, clip=clip, hidden=hidden)


# ---- UI -------------------------------------------------------------------------------------------------------------

class KAT_OT_refit(bpy.types.Operator):
    bl_idname = "kat.refit"
    bl_label = "Refit clothes"
    bl_description = ("Fit the picked outfit to your body: skeleton, surface, weights, then bind it to your rig. The skin "
                      "is the one in the Outfits panel. Ctrl+Z undoes it")
    bl_options = {"REGISTER", "UNDO"}

    @operator_guard
    def execute(self, context):
        s = settings(context)
        if s.rig is None:
            raise ValueError("Pick the outfit's armature first")
        skins = outfits.skin_objects(context)
        if not skins:  # nobody listed it: look for it by name among your meshes, not the outfit's
            pool = [o for o in context.scene.objects if o.type == "MESH" and o.find_armature() != s.rig]
            found = [o for o in pool if outfits.is_skin_name(o.name)]
            for ob in found:
                outfits.settings(context).skins.add().obj = ob
            skins = outfits.skin_objects(context)
        if not skins:
            raise ValueError("No mesh is called Skin, Body, Head or Face. Select the body meshes and press Add selected "
                             "under Skin in the Outfits panel")
        dst = outfits.find_rig(context, skins[0])
        if dst is None or dst == s.rig:
            raise ValueError("The skin has no rig of its own to fit onto")
        manager = context.window_manager
        manager.progress_begin(0, 100)
        try:
            done = refit_outfit(context, s.rig, skins, dst, None, s.gap, s.tighten, s.reach, s.rigid, s.rigid_words,
                                s.solid_words, finish=tuple(k for k, on in zip(FINISH_STEPS, (
                                    s.hide_props, s.list_layers, s.make_shapes, s.shapes_on)) if on), props_words=s.props_words,
                                cloth=s.cloth, stiffness=s.stiffness, progress=lambda f: manager.progress_update(int(90 * f)),
                                keep_original=s.keep_original, mode=s.mode)
        finally:
            manager.progress_end()
        last["text"] = f"Fitted {len(done.rows)} meshes in {done.seconds:.0f} s"
        if done.clip is not None:
            last["text"] += f"; {len(done.clip.rows)} fit shapes made"
        if done.hidden:
            last["text"] += "; hidden as props: " + ", ".join(done.hidden)
        self.report({"INFO"}, last["text"])
        s.rig = None
        return {"FINISHED"}


class KAT_PT_refit(bpy.types.Panel):
    bl_label = "Refit clothes"
    bl_idname = "KAT_PT_refit"
    bl_parent_id = "KAT_PT_outfits"
    bl_order = 1036
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "KAT"
    bl_options = {"DEFAULT_CLOSED"}

    def draw_header(self, context):
        self.layout.label(icon="MOD_CLOTH")

    def draw(self, context):
        s = settings(context)
        layout = self.layout
        uiutil.paragraph(layout, context, "Import an outfit made for another avatar. Pick its armature here and press "
                                          "Refit. Your skin is found by name (Skin, Body, Head), or use the list in the "
                                          "Outfits panel.", "INFO")
        layout.prop(s, "rig", text="Outfit rig")
        layout.row().prop(s, "mode", expand=True)
        col = layout.column(align=True)
        col.prop(s, "gap")
        col.prop(s, "tighten", slider=True)
        col.prop(s, "reach")
        layout.prop(s, "cloth")
        if s.cloth:
            layout.prop(s, "stiffness", slider=True)
        layout.prop(s, "rigid")
        layout.prop(s, "solid_words", text="Hard parts")
        if s.rigid:
            layout.prop(s, "rigid_words", text="Also")
        box = layout.box()
        box.label(text="Afterwards")
        box.prop(s, "hide_props")
        if s.hide_props:
            box.prop(s, "props_words", text="Props")
        box.prop(s, "keep_original")
        box.prop(s, "list_layers")
        box.prop(s, "make_shapes")
        sub = box.row()
        sub.enabled = s.make_shapes
        sub.prop(s, "shapes_on")
        row = layout.row()
        row.scale_y = 1.3
        row.enabled = s.rig is not None
        row.operator("kat.refit", icon="MOD_CLOTH")
        if last["text"]:
            uiutil.paragraph(layout, context, last["text"])
        if refitted_objects():
            row = layout.row(align=True)
            row.operator("kat.refit_view", text="View original", icon="HIDE_OFF").original = True
            row.operator("kat.refit_view", text="View fit", icon="MOD_CLOTH").original = False


def refitted_objects():
    """The meshes that kept their original shape."""
    return [o for o in bpy.data.objects if o.type == "MESH" and o.data.shape_keys and ORIGINAL in o.data.shape_keys.key_blocks]


class KAT_OT_refit_view(bpy.types.Operator):
    bl_idname = "kat.refit_view"
    bl_label = "View original"
    bl_description = "Show the refitted clothes as they were before the fit, or as fitted"
    bl_options = {"REGISTER", "UNDO"}

    original: BoolProperty(name="Original", default=True)

    @operator_guard
    def execute(self, context):
        found = refitted_objects()
        if not found:
            raise ValueError("Nothing here kept an original shape. Refit with Keep the original ticked")
        for ob in found:
            ob.data.shape_keys.key_blocks[ORIGINAL].value = 1.0 if self.original else 0.0
            ob.data.update()
        self.report({"INFO"}, ("Original" if self.original else "Fit") + f" shown on {len(found)} meshes")
        return {"FINISHED"}


classes = (KAT_RefitSettings, KAT_OT_refit, KAT_OT_refit_view, KAT_PT_refit)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.kat_refit = PointerProperty(type=KAT_RefitSettings)


def unregister():
    del bpy.types.Scene.kat_refit
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
