# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Krislyz
"""Reduce triangles: one slider per mesh, from "a single triangle" to untouched.

Auto-fill suggests the sliders: hair, the face and the hands stay untouched, and the rest share the cut needed to
reach the chosen VRChat rank. Reducing keeps what the mesh carries: shape keys, vertex weights, UVs and materials.
"""
import bpy
import numpy as np
from bpy.props import BoolProperty, CollectionProperty, EnumProperty, FloatProperty, IntProperty, PointerProperty, StringProperty
from mathutils import Vector
from mathutils.bvhtree import BVHTree
from mathutils.interpolate import poly_3d_calc

from ..core import vrc
from ..core.safe import operator_guard
from . import armature, face, uiutil

HAIR_WORDS = {"hair", "bang", "bangs", "ahoge", "fringe", "ponytail", "twintail", "braid"}
HAND_WORDS = {"glove", "gloves", "hand", "hands", "finger", "fingers", "nail", "nails"}
FINGER_TOKENS = ("Hand", "Thumb", "Index", "Middle", "Ring", "Little")
BUDGETS = [
    ("EXCELLENT", "PC Excellent (32,000)", "Cut until the whole avatar is at most 32,000 triangles"),
    ("GOOD", "PC Good (70,000)", "Cut until the whole avatar is at most 70,000 triangles"),
    ("QUEST_GOOD", "Quest Good (10,000)", "Cut until the whole avatar is at most 10,000 triangles"),
    ("QUEST_MEDIUM", "Quest Medium (15,000)", "Cut until the whole avatar is at most 15,000 triangles"),
]
BUDGET_TRIS = {"EXCELLENT": vrc.LIMITS["triangles"][0], "GOOD": vrc.LIMITS["triangles"][1],
               "QUEST_GOOD": vrc.MOBILE_LIMITS["triangles"][1], "QUEST_MEDIUM": vrc.MOBILE_LIMITS["triangles"][2]}
KEEP_GROUP = "KAT_keep"
PREVIEW = "KAT_preview"
MIN_TRIS = 1  # a slider at the far left leaves one triangle: it is what the user asked for

summary = {"text": ""}


def tris_of(ob):
    me = ob.data
    me.calc_loop_triangles()
    return len(me.loop_triangles)


def words_of(name):
    import re
    return {w.lower() for w in re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])", name)}


def protection(ob, face_mesh):
    """Why a mesh should stay as it is ('' when it may be reduced). A custom property kat_protect overrides the guess:
    True protects, False allows."""
    manual = ob.get("kat_protect")
    if manual is True:
        return "protected by you"
    if manual is False:
        return ""
    if ob is face_mesh:
        return "face"
    if words_of(ob.name) & HAIR_WORDS or any("hair" in (s.material.name.lower() if s.material else "") for s in ob.material_slots):
        return "hair"
    if words_of(ob.name) & HAND_WORDS:
        return "hands"
    return ""


class KAT_ReduceItem(bpy.types.PropertyGroup):
    obj: PointerProperty(type=bpy.types.Object, name="Mesh")
    amount: FloatProperty(name="Keep", default=1.0, min=0.0, max=1.0, subtype="FACTOR", precision=2,
                          description="How much of the triangles to keep. 1 leaves the mesh alone, 0 leaves a single triangle",
                          update=lambda self, context: preview(self))
    tris: IntProperty()
    note: StringProperty()


class KAT_ReduceSettings(bpy.types.PropertyGroup):
    items: CollectionProperty(type=KAT_ReduceItem)
    index: IntProperty()
    budget: EnumProperty(name="Aim for", items=BUDGETS, default="GOOD")
    live: BoolProperty(name="Real-time preview", default=False, update=lambda self, context: refresh_previews(context),
                       description="Show the reduced mesh in the viewport while a slider moves. Nothing is changed "
                                   "until Reduce is pressed")


def settings(context):
    return context.scene.kat_reduce


def fill_list(context, rig):
    """Make the list match the rig's meshes. Sliders the user set are kept."""
    s = settings(context)
    meshes = armature.meshes_of(rig)
    have = {i.obj: i.amount for i in s.items if i.obj is not None}  # plain numbers: clearing the list frees the items
    s.items.clear()
    face_mesh = face.guess_face_mesh(meshes)
    for ob in sorted(meshes, key=lambda o: -len(o.data.polygons)):
        item = s.items.add()
        item.obj = ob
        item.tris = tris_of(ob)
        item.note = protection(ob, face_mesh)
        item.amount = have.get(ob, 1.0)
    return s


MAX_TOLERANCE = 5.0  # mm: the most the auto-fill will let a mesh drift from the original
CANDIDATES = (0.85, 0.7, 0.55, 0.42, 0.3, 0.2, 0.12, 0.07)


def trial_curve(context, ob, rig):
    """Try a few ratios on a throwaway copy of `ob`. Returns [(share of triangles kept, p99 distance in mm of the
    reduced surface from the original)], most triangles first, the error never going down as the share does."""
    before = tris_of(ob)
    me = ob.data
    co = np.empty(len(me.vertices) * 3, dtype=np.float32)
    me.vertices.foreach_get("co", co)
    co = co.reshape(-1, 3)
    me.calc_loop_triangles()
    tris = np.empty(len(me.loop_triangles) * 3, dtype=np.int32)
    me.loop_triangles.foreach_get("vertices", tris)
    bvh = BVHTree.FromPolygons(co.tolist(), tris.reshape(-1, 3).tolist(), all_triangles=True)
    scale = ob.matrix_world.median_scale
    curve, worst = [], 0.0
    for ratio in CANDIDATES:
        copy = ob.copy()
        copy.data = ob.data.copy()
        copy.name = "KAT_trial"
        context.scene.collection.objects.link(copy)
        try:
            reduce_mesh(context, copy, ratio, rig, keep_keys=False)
            pts = np.empty(len(copy.data.vertices) * 3, dtype=np.float32)
            copy.data.vertices.foreach_get("co", pts)
            dists = [bvh.find_nearest(Vector(p.tolist()))[3] or 0.0 for p in pts.reshape(-1, 3)]
            worst = max(worst, float(np.percentile(dists, 99)) * scale * 1000.0)
            curve.append((tris_of(copy) / before, worst))
        finally:
            data = copy.data
            bpy.data.objects.remove(copy)
            bpy.data.meshes.remove(data)
    return curve


def pick_ratio(curve, tolerance):
    """The fewest triangles whose error stays within `tolerance` mm; 1.0 when no trial does."""
    ok = [share for share, err in curve if err <= tolerance]
    return min(ok) if ok else 1.0


def suggest(context, rig):
    """Set every slider. Hair, face and hands stay at 1. For the rest, each mesh is tried at several reductions and
    gets the deepest cut that stays close to the original; the tolerance is as tight as it can be while the whole
    avatar still fits the chosen rank."""
    s = fill_list(context, rig)
    budget = BUDGET_TRIS[s.budget]
    fixed = sum(i.tris for i in s.items if i.note)
    free = [i for i in s.items if not i.note and i.tris > 12]
    total_free = sum(i.tris for i in free)
    if fixed + total_free <= budget:
        for item in s.items:
            item.amount = 1.0
        summary["text"] = "Already within the budget"
        return s
    curves = {i.obj.name: trial_curve(context, i.obj, rig) for i in free}

    def size(tol):
        return fixed + sum(i.tris * pick_ratio(curves[i.obj.name], tol) for i in free) + sum(
            i.tris for i in s.items if not i.note and i.tris <= 12)

    lo, hi = 0.05, MAX_TOLERANCE  # mm
    reached = size(hi) <= budget
    if not reached:
        tol = hi  # more than this would start to show, so stop here and say how far it got
    else:
        for _ in range(14):
            mid = (lo * hi) ** 0.5
            lo, hi = (mid, hi) if size(mid) > budget else (lo, mid)
        tol = hi
    for item in s.items:
        item.amount = 1.0 if item.note or item not in free else pick_ratio(curves[item.obj.name], tol)
    after = int(size(tol))
    over = " (hair, face and hands alone are over it)" if fixed > budget else ""
    if not reached:
        over = over or f" (the budget needs a deeper cut than {MAX_TOLERANCE:.0f} mm allows)"
    summary["text"] = f"{fixed + total_free:,} to about {after:,} triangles, within about {tol:.1f} mm{over}"
    return s


def predicted(s):
    return sum(max(MIN_TRIS, int(i.tris * i.amount)) if i.amount < 1.0 else i.tris for i in s.items if i.obj is not None)


# ---- reducing one mesh ------------------------------------------------------------------------------------------

def protect_weights(ob, rig, keep_hands=True):
    """Per-vertex weight of what reducing must spare (borders, hands), or None."""
    weights = border_weights(ob)
    if rig is not None and keep_hands:
        hands = hand_weights(ob, rig)
        if hands is not None:
            weights = np.maximum(weights, hands)
    return weights if weights.max() > 0.0 else None


def keep_group(ob, weights):
    group = ob.vertex_groups.new(name=KEEP_GROUP)
    for i in np.nonzero(weights > 0.001)[0]:
        group.add([int(i)], float(weights[i]), "REPLACE")
    return group


def clear_preview(ob):
    mod = ob.modifiers.get(PREVIEW)
    if mod is not None:
        ob.modifiers.remove(mod)
    if KEEP_GROUP in ob.vertex_groups:
        ob.vertex_groups.remove(ob.vertex_groups[KEEP_GROUP])


def preview(item):
    """Show the slider's result as a Decimate modifier: live and undoable by moving the slider back."""
    ob = item.obj
    if ob is None or ob.type != "MESH":
        return
    try:
        scene = item.id_data
        if item.amount >= 1.0 or not scene.kat_reduce.live or item.tris <= MIN_TRIS:
            clear_preview(ob)
            return
        mod = ob.modifiers.get(PREVIEW)
        if mod is None:
            mod = ob.modifiers.new(PREVIEW, "DECIMATE")
            mod.decimate_type = "COLLAPSE"
            mod.use_collapse_triangulate = True
            mod.show_expanded = False
            ob.modifiers.move(len(ob.modifiers) - 1, 0)  # before the armature, so it cuts the rest shape
            weights = protect_weights(ob, ob.find_armature())
            if weights is not None:
                mod.vertex_group = keep_group(ob, weights).name
                mod.vertex_group_factor = 1.0
                mod.invert_vertex_group = True
        mod.ratio = max(item.amount, MIN_TRIS / item.tris)
    except (RuntimeError, ReferenceError):
        pass  # a preview that cannot be made must not break the slider


def refresh_previews(context):
    for item in settings(context).items:
        preview(item)


def hand_weights(ob, rig):
    """Per-vertex weight (0..1) of the hand and finger bones, or None when the mesh has no hand skinning."""
    best, _ = armature.cached_map(rig)
    names = {v for k, v in best.items() if any(t in k for t in FINGER_TOKENS)}
    index = {g.index for g in ob.vertex_groups if g.name in names}
    if not index:
        return None
    w = np.zeros(len(ob.data.vertices))
    for v in ob.data.vertices:
        w[v.index] = min(1.0, sum(g.weight for g in v.groups if g.group in index))
    return w if w.max() > 0.2 else None


def border_weights(ob):
    """1 on every vertex that sits on an open border or a non-manifold edge, else 0. Collapsing those pulls hems,
    collars and cuffs inwards, so they are spared."""
    me = ob.data
    loops = np.empty(len(me.loops), dtype=np.int64)
    me.loops.foreach_get("edge_index", loops)
    uses = np.bincount(loops, minlength=len(me.edges))
    ends = np.empty(len(me.edges) * 2, dtype=np.int64)
    me.edges.foreach_get("vertices", ends)
    ends = ends.reshape(-1, 2)
    w = np.zeros(len(me.vertices))
    w[ends[uses != 2].ravel()] = 1.0
    return w


def save_keys(ob):
    blocks = face.key_blocks(ob)
    if blocks is None:
        return None
    keys = []
    for b in blocks:
        keys.append(dict(name=b.name, co=face._coords(b).reshape(-1, 3), relative=b.relative_key.name, value=b.value,
                         min=b.slider_min, max=b.slider_max, mute=b.mute, group=b.vertex_group,
                         interpolation=b.interpolation))
    return keys


def transfer_map(old_co, old_tris, new_co):
    """For each new vertex: the three old vertices of the nearest old triangle and the weights between them."""
    bvh = BVHTree.FromPolygons(old_co.tolist(), old_tris.tolist(), all_triangles=True)
    verts = np.zeros((len(new_co), 3), dtype=np.int64)
    weights = np.zeros((len(new_co), 3))
    for i, p in enumerate(new_co):
        loc, _, tri, _ = bvh.find_nearest(Vector(p.tolist()))
        a, b, c = old_tris[tri]
        w = poly_3d_calc([Vector(old_co[a].tolist()), Vector(old_co[b].tolist()), Vector(old_co[c].tolist())], loc)
        verts[i] = (a, b, c)
        weights[i] = (w[0], w[1], w[2])
    return verts, weights


def reduce_mesh(context, ob, amount, rig=None, keep_hands=True, keep_keys=True):
    """Reduce `ob` to about `amount` of its triangles (0 to 1). Returns (before, after) triangle counts.
    Hand and finger skinning is protected, and shape keys, vertex weights, UVs and materials come along."""
    clear_preview(ob)  # the live preview is only a stand-in for this
    before = tris_of(ob)
    if amount >= 1.0 or before <= MIN_TRIS:
        return before, before
    if ob.data.shape_keys and ob.data.shape_keys.animation_data and ob.data.shape_keys.animation_data.drivers:
        raise ValueError(f"{ob.name} has drivers on its shape keys, which reducing would break")
    if ob.data.users > 1:
        ob.data = ob.data.copy()
    old = ob.data
    old_co = np.empty(len(old.vertices) * 3, dtype=np.float32)
    old.vertices.foreach_get("co", old_co)
    old_co = old_co.reshape(-1, 3)
    old.calc_loop_triangles()
    old_tris = np.empty(len(old.loop_triangles) * 3, dtype=np.int32)
    old.loop_triangles.foreach_get("vertices", old_tris)
    old_tris = old_tris.reshape(-1, 3)
    keys = save_keys(ob)
    old_vn = None
    if keep_keys and old.has_custom_normals:  # imported models carry their own normals, and collapsing drops them
        cn = np.empty(len(old.loops) * 3, dtype=np.float32)
        old.corner_normals.foreach_get("vector", cn)
        corner_vertex = np.empty(len(old.loops), dtype=np.int64)
        old.loops.foreach_get("vertex_index", corner_vertex)
        acc = np.zeros((len(old.vertices), 3))
        np.add.at(acc, corner_vertex, cn.reshape(-1, 3))
        old_vn = acc / np.maximum(np.linalg.norm(acc, axis=1, keepdims=True), 1e-9)
    weights = protect_weights(ob, rig, keep_hands)
    layer = context.view_layer
    held = [(m, m.show_viewport) for m in ob.modifiers]
    group = None
    try:
        for m, _ in held:
            m.show_viewport = False
        if keys:  # last to first: taking the basis out first would make the next key the mesh's shape
            blocks = ob.data.shape_keys.key_blocks
            for k in reversed(list(blocks)):
                ob.shape_key_remove(k)
        mod = ob.modifiers.new("KAT_reduce", "DECIMATE")
        mod.decimate_type = "COLLAPSE"
        mod.ratio = max(amount, MIN_TRIS / before)
        mod.use_collapse_triangulate = True
        if weights is not None:
            group = keep_group(ob, weights)
            mod.vertex_group = group.name
            mod.vertex_group_factor = 1.0
            mod.invert_vertex_group = True  # in the group means "reduce more"; inverted, the hands are spared
        layer.update()
        evaluated = ob.evaluated_get(context.evaluated_depsgraph_get())
        new = bpy.data.meshes.new_from_object(evaluated)
        ob.modifiers.remove(mod)
    finally:
        for m, was in held:
            m.show_viewport = was
    ob.data = new  # vertex group names live on the mesh, so the temporary group travels with it and goes now
    if KEEP_GROUP in ob.vertex_groups:
        ob.vertex_groups.remove(ob.vertex_groups[KEEP_GROUP])
    new.name = old.name
    if keep_keys and (keys or old_vn is not None):
        new_co = np.empty(len(new.vertices) * 3, dtype=np.float32)
        new.vertices.foreach_get("co", new_co)
        new_co = new_co.reshape(-1, 3)
        verts, w = transfer_map(old_co, old_tris, new_co)
        if old_vn is not None:
            nn = (old_vn[verts] * w[:, :, None]).sum(axis=1)
            nn /= np.maximum(np.linalg.norm(nn, axis=1, keepdims=True), 1e-9)
            new.polygons.foreach_set("use_smooth", [True] * len(new.polygons))
            new.normals_split_custom_set_from_vertices(nn.tolist())
    if keys and keep_keys:
        base_old = keys[0]["co"]
        ob.shape_key_add(name=keys[0]["name"], from_mix=False)
        blocks = ob.data.shape_keys.key_blocks
        blocks[0].data.foreach_set("co", new_co.reshape(-1))
        for k in keys[1:]:
            delta = k["co"] - base_old
            moved = new_co + (delta[verts] * w[:, :, None]).sum(axis=1)
            b = ob.shape_key_add(name=k["name"], from_mix=False)
            b.data.foreach_set("co", moved.astype(np.float32).reshape(-1))
        for k in keys:
            b = blocks[k["name"]]
            b.relative_key = blocks.get(k["relative"]) or blocks[0]
            b.value, b.slider_min, b.slider_max, b.mute = k["value"], k["min"], k["max"], k["mute"]
            b.vertex_group, b.interpolation = k["group"], k["interpolation"]
    bpy.data.meshes.remove(old)
    new.update()
    return before, tris_of(ob)


def reduce_all(context, rig):
    s = settings(context)
    log = []
    for item in list(s.items):
        ob = item.obj
        if ob is None or item.amount >= 1.0:
            continue
        if not ob.visible_get():
            log.append(f"{ob.name}: hidden, skipped")  # a hidden mesh is not evaluated, so there is nothing to reduce
            continue
        before, after = reduce_mesh(context, ob, item.amount, rig)
        item.tris = after
        item.amount = 1.0
        log.append(f"{ob.name}: {before:,} to {after:,}")
    return log


# ---- operators and panel ----------------------------------------------------------------------------------------

class KAT_UL_reduce(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        ob = item.obj
        if ob is None:
            layout.label(text="(missing)", icon="ERROR")
            return
        col = layout.column(align=True)
        row = col.row(align=True)
        row.label(text=ob.name, icon="LOCKED" if item.note else "MESH_DATA")
        sub = row.row()
        sub.alignment = "RIGHT"
        sub.enabled = False
        now = f"{item.tris:,}" if item.amount >= 1.0 else f"{max(MIN_TRIS, int(item.tris * item.amount)):,}"
        sub.label(text=now if item.amount >= 1.0 else f"{item.tris:,} > {now}")
        col.prop(item, "amount", text=item.note.capitalize() if item.note else "Keep", slider=True)


class KAT_OT_reduce_suggest(bpy.types.Operator):
    bl_idname = "kat.reduce_suggest"
    bl_label = "Auto-fill sliders"
    bl_description = ("Set the sliders for you. Hair, the face and the hands stay at 1; the other meshes share the cut "
                      "needed to reach the chosen rank")
    bl_options = {"REGISTER", "UNDO"}

    @operator_guard
    def execute(self, context):
        rig = armature.rig_of(context)
        if rig is None:
            raise ValueError("Select an armature, or a mesh rigged to one")
        suggest(context, rig)
        self.report({"INFO"}, summary["text"])
        return {"FINISHED"}


class KAT_OT_reduce(bpy.types.Operator):
    bl_idname = "kat.reduce"
    bl_label = "Reduce"
    bl_description = "Reduce every mesh whose slider is below 1. Shape keys, weights, UVs and materials are kept. Ctrl+Z undoes it"
    bl_options = {"REGISTER", "UNDO"}

    @operator_guard
    def execute(self, context):
        rig = armature.rig_of(context)
        if rig is None:
            raise ValueError("Select an armature, or a mesh rigged to one")
        armature.check_ready(context, rig)
        if not settings(context).items:
            fill_list(context, rig)
        log = reduce_all(context, rig)
        if not log:
            raise ValueError("Every slider is at 1. Auto-fill them, or pull some down")
        summary["text"] = f"Reduced {len(log)} meshes"
        self.report({"INFO"}, "; ".join(log))
        return {"FINISHED"}


class KAT_OT_protect_toggle(bpy.types.Operator):
    bl_idname = "kat.protect_toggle"
    bl_label = "Protect selected"
    bl_description = ("Mark the mesh picked in the list, and any meshes selected in the viewport, as never to be "
                      "reduced by Auto-fill. Click again to allow")
    bl_options = {"REGISTER", "UNDO"}

    @operator_guard
    def execute(self, context):
        s = settings(context)
        chosen = [o for o in context.selected_objects if o.type == "MESH"]
        if 0 <= s.index < len(s.items) and s.items[s.index].obj is not None and s.items[s.index].obj not in chosen:
            chosen.append(s.items[s.index].obj)
        if not chosen:
            raise ValueError("Pick a mesh in the list, or select meshes in the viewport")
        protect = not all(o.get("kat_protect") is True for o in chosen)
        for ob in chosen:
            ob["kat_protect"] = protect
        rig = armature.rig_of(context)
        if rig is not None:
            fill_list(context, rig)
            for item in settings(context).items:
                if item.obj in chosen and protect:
                    item.amount = 1.0
        self.report({"INFO"}, ("Protected " if protect else "Allowed ") + ", ".join(o.name for o in chosen))
        return {"FINISHED"}


class KAT_PT_reduce(bpy.types.Panel):
    bl_label = "Reduce triangles"
    bl_idname = "KAT_PT_reduce"
    bl_parent_id = "KAT_PT_optimize_auto"
    bl_order = 1034
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "KAT"

    def draw_header(self, context):
        self.layout.label(icon="MOD_DECIM")

    def draw(self, context):
        s = settings(context)
        layout = self.layout
        rig = armature.rig_of(context)
        if rig is None:
            uiutil.paragraph(layout, context, "Select a rigged model.", "INFO")
            return
        row = layout.row(align=True)
        row.prop(s, "budget", text="")
        row.operator("kat.reduce_suggest", icon="SHADERFX")
        if not s.items:
            uiutil.paragraph(layout, context, "Auto-fill makes a slider for every mesh. 1 leaves it alone, 0 leaves a "
                                              "single triangle. Hair, face and hands start untouched.", "INFO")
            return
        layout.prop(s, "live", icon="HIDE_OFF")
        layout.template_list("KAT_UL_reduce", "", s, "items", s, "index", rows=6)
        total = sum(i.tris for i in s.items if i.obj is not None)
        after = predicted(s)
        platform = "QUEST" if s.budget.startswith("QUEST") else "PC"
        rank = vrc.RANKS[vrc.rank_of("triangles", after, platform)]
        row = layout.row()
        row.alert = vrc.rank_of("triangles", after, platform) >= 3
        row.label(text=f"{total:,} to {after:,} triangles, {rank}", icon="INFO")
        if summary["text"]:
            uiutil.paragraph(layout, context, summary["text"])
        row = layout.row()
        row.scale_y = 1.3
        row.operator("kat.reduce", icon="MOD_DECIM")
        layout.operator("kat.protect_toggle", text="Protect picked / selected", icon="LOCKED")


classes = (KAT_ReduceItem, KAT_ReduceSettings, KAT_UL_reduce, KAT_OT_reduce_suggest, KAT_OT_reduce,
           KAT_OT_protect_toggle, KAT_PT_reduce)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.kat_reduce = PointerProperty(type=KAT_ReduceSettings)


def unregister():
    del bpy.types.Scene.kat_reduce
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
