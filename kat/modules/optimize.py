# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Krislyz
"""Optimization: a checklist of safe clean-ups with what each would do, mesh joining that leaves the outfits
alone (so clothes can still toggle in Unity), bone merging, and the material tools."""
import os
import re

import bpy
from bpy.props import FloatProperty
from mathutils import Vector

from ..core.safe import operator_guard
from ..core import compat, vrc
from . import armature, atlas, face, materials, mesh, options, outfits, reduce, texopt, uiutil, validate

found = {"rig": None, "counts": {}, "joinable": []}  # the last scan, shown beside the checkboxes

STEPS = (  # (option, label, key in the scan, icon)
    ("opt_keys", "Empty shape keys", "keys", "SHAPEKEY_DATA"),
    ("opt_groups", "Unused vertex groups", "groups", "GROUP_VERTEX"),
    ("opt_materials", "Duplicate materials", "materials", "MATERIAL"),
    ("opt_weights", "Limit weights to 4", "weights", "MOD_VERTEX_WEIGHT"),
    ("opt_triangles", "Reduce triangles", "triangles", "MOD_DECIM"),
    ("opt_textures", "Shrink textures", "textures", "TEXTURE_DATA"),
    ("opt_atlas", "Combine materials", "atlas", "TEXTURE"),
    ("opt_bones", "Unused bones", "bones", "BONE_DATA"),
    ("opt_join", "Join meshes", "join", "AUTOMERGE_ON"),
)
SAFE = ("opt_keys", "opt_groups", "opt_materials")  # only remove what nothing uses


def outfit_set(context):
    return set(outfits.outfit_objects(context))


def joinable(context, rig):
    """Meshes that can go into one: everything except the outfits, which have to stay separate to toggle."""
    keep = outfit_set(context)
    return [ob for ob in armature.meshes_of(rig) if ob not in keep]


def duplicate_slots(objs):
    count = 0
    for ob in objs:
        for slot in ob.material_slots:
            mat = slot.material
            m = materials.COPY.match(mat.name) if mat else None
            base = bpy.data.materials.get(m.group(1)) if m else None
            if base is not None and base is not mat and mat.library is None:
                count += 1
        used = {p.material_index for p in ob.data.polygons}
        count += sum(1 for i in range(len(ob.material_slots)) if i not in used)
    return count


def atlas_count(meshes, loose):
    try:
        return sum(len(g.materials) for g in atlas.plan(meshes, loose=loose)[0])
    except ValueError:  # no UV maps at all: nothing to combine
        return 0


def scan(context, rig):
    meshes = armature.meshes_of(rig)
    keep = joinable(context, rig)
    counts = {
        "keys": sum(len(face.empty_keys(ob)) for ob in meshes),
        "groups": sum(len(mesh.unused_groups(ob, rig)) for ob in meshes),
        "materials": duplicate_slots(meshes),
        "weights": sum(mesh.heavy_vertices(ob, rig) for ob in meshes),
        "bones": len(armature.prunable_bones(rig, meshes)),
        "join": max(0, len(keep) - 1),
        "triangles": sum(reduce.tris_of(ob) for ob in meshes),
        "textures": sum(1 for i in texopt.used_images(meshes) if max(i.size) > int(context.scene.kat.tex_limit)),
        "atlas": atlas_count(meshes, context.scene.kat.atlas_loose),
    }
    found.update(rig=rig.name, counts=counts, joinable=[ob.name for ob in keep])
    return counts


def join_keeping_outfits(context, rig):
    """Join every mesh that is not an outfit into the one with the most shape keys. Returns (target, how many joined)."""
    group = joinable(context, rig)
    if len(group) < 2:
        return None, 0
    target = max(group, key=lambda ob: (len(face.key_blocks(ob) or ()), len(ob.data.vertices)))
    with context.temp_override(active_object=target, object=target, selected_objects=group,
                               selected_editable_objects=group):
        bpy.ops.object.join()
    return target, len(group)


def optimize(context, rig, o):
    """Run the ticked steps. Returns a list of result lines."""
    armature.check_ready(context, rig)
    meshes = armature.meshes_of(rig)
    before = validate.collect(context, rig)
    log = []
    if o.opt_keys:
        log.append(f"Removed {sum(face.remove_empty_keys(ob) for ob in meshes)} empty shape keys")
    if o.opt_groups:
        log.append(f"Removed {sum(mesh.clean_groups(ob, rig) for ob in meshes)} unused vertex groups")
    if o.opt_materials:
        merged = materials.merge_duplicates(meshes)
        slots = materials.remove_empty_slots(meshes)
        log.append(f"Merged {merged} duplicate material slots, removed {slots} empty ones, "
                   f"deleted {materials.purge_orphans()} orphaned materials")
    if o.opt_weights:
        log.append(f"Limited weights on {sum(mesh.limit_and_normalize(ob, rig) for ob in meshes)} vertices")
    if o.opt_triangles:  # before joining: the face and hair meshes are spared by name
        reduce.suggest(context, rig)
        done = reduce.reduce_all(context, rig)
        log.append(reduce.summary["text"] if not done else f"Reduced {len(done)} meshes: {reduce.summary['text']}")
    if o.opt_textures:
        done = texopt.shrink(texopt.used_images(armature.meshes_of(rig)), int(o.tex_limit), o.tex_pot)
        log.append(f"Shrunk {len(done)} textures to {o.tex_limit} px" if done else "No texture above the limit")
    if o.opt_atlas:
        folder = bpy.path.abspath("//") if bpy.data.filepath else ""
        try:
            built, skipped = atlas.build(armature.meshes_of(rig), int(o.atlas_size), f"{rig.name}_atlas", folder, o.atlas_loose)
        except ValueError as err:
            built, skipped = [], []
            log.append(f"Atlas skipped: {err}")
        log.append(f"Combined {sum(len(g.materials) for g in built)} materials into {len(built)} atlas(es)" if built
                   else "Fewer than two materials can be combined")
    if o.opt_join:
        target, count = join_keeping_outfits(context, rig)
        log.append(f"Joined {count} meshes into {target.name}" if target else "Nothing to join")
    if o.opt_bones:
        names = armature.prunable_bones(rig, armature.meshes_of(rig))
        armature.delete_bones(context, rig, names)
        log.append(f"Removed {len(names)} unused bones")
    if not log:
        log.append("No steps are ticked")
    after = validate.collect(context, rig)
    platform = "QUEST" if o.opt_triangles and reduce.settings(context).budget.startswith("QUEST") else o.platform
    for metric, label in (("triangles", "triangles"), ("texture_mb", "texture MB"), ("skinned_meshes", "skinned meshes"),
                          ("material_slots", "material slots"), ("bones", "bones")):
        if before[metric] != after[metric]:
            log.append(f"{label}: {before[metric]:,} to {after[metric]:,}")
    was, now = vrc.evaluate(before, platform)[1], vrc.evaluate(after, platform)[1]
    log.append(f"Rank: {vrc.RANKS[was]} to {vrc.RANKS[now]}" if was != now else f"Rank stays {vrc.RANKS[now]}")
    found["counts"] = {}
    return log


# ---- bone merging -----------------------------------------------------------------------------------------------

def loose_chains(rig):
    """{top bone: [bones]}: the loose chains (hair, tails, skirts, ears). Humanoid bones and bones that constraints,
    drivers or other bones refer to are never part of one."""
    best, _ = armature.cached_map(rig)
    protected = set(best.values()) | armature.referenced_bones(rig)
    loose = {b.name for b in rig.data.bones if b.use_deform and b.name not in protected and b.parent is not None}
    chains = {}
    for b in rig.data.bones:
        if b.name not in loose:
            continue
        top = b
        while top.parent is not None and top.parent.name in loose:
            top = top.parent
        chains.setdefault(top.name, []).append(b)
    return chains


SEPARATORS = re.compile(r"[_.\-\s]+")


def category_name(names):
    """['skirt_222_2201', 'skirt_222_2202', ...] -> 'skirt_root_222_22': what they share, with root after the first word."""
    parts = SEPARATORS.split(os.path.commonprefix(names).rstrip("_.- "), 1)
    first = parts[0] or SEPARATORS.split(names[0], 1)[0]
    return f"{first}_root" + (f"_{parts[1]}" if len(parts) > 1 and parts[1] else "")


def categories(rig):
    """[(root name, parent name, [top bones])]: loose chains that start on the same bone and share the first word of their
    name (skirt_*, hair_*) get one root between them and that bone. Chains that already hang off such a root are left."""
    groups = {}
    for top in loose_chains(rig):
        word = re.match(r"[A-Za-z]+", top.lstrip("_.- "))
        groups.setdefault((rig.data.bones[top].parent.name, word.group().lower() if word else ""), []).append(top)
    taken = {b.name for b in rig.data.bones}
    out = []
    for (parent, _), tops in groups.items():
        if len(tops) < 2:
            continue
        name = category_name(sorted(tops))
        if "_root" in parent and parent.split("_root")[0] == name.split("_root")[0]:
            continue
        base, n = name, 1
        while name in taken:
            name = f"{base}.{n:03d}"
            n += 1
        taken.add(name)
        out.append((name, parent, sorted(tops)))
    return sorted(out)


def make_categories(context, rig, groups):
    """Add each root (at the middle of its chains' first bones, standing up) and hang the chains on it. Weights stay."""
    armature.check_ready(context, rig)

    def action(edit, _):
        for name, parent, tops in groups:
            heads = [edit[t].head.copy() for t in tops]
            length = max(sum(edit[t].length for t in tops) / len(tops) * 0.5, 0.01)
            root = edit.new(name)
            root.head = sum(heads, Vector()) / len(heads)
            root.tail = root.head + Vector((0, 0, length))
            root.parent = edit[parent]
            root.use_deform = False
            for t in tops:
                edit[t].parent = root
                edit[t].use_connect = False
    armature.edit_bones(context, rig, [], action)
    return len(groups)


def merge_bones(context, rig, names, into="PARENT", keep=False, visible=False):
    """Move each bone's weights onto its parent (or onto the active bone), hand its children on, delete it. Deepest first.
    `keep` leaves the bones in place, `visible` leaves the weights of hidden meshes alone."""
    armature.check_ready(context, rig)
    meshes = [m for m in armature.meshes_of(rig) if not visible or m.visible_get()]
    bones = rig.data.bones
    active = bones.active.name if bones.active is not None and into == "ACTIVE" else ""
    if into == "ACTIVE" and not active:
        raise ValueError("Select the bone the weights should go to (it becomes the active bone)")
    doomed = sorted((n for n in names if n in bones and n != active), key=lambda n: -len(bones[n].parent_recursive))
    parent_of = {n: bones[n].parent.name for n in doomed if bones[n].parent}
    moved = {}
    for n in doomed:  # a bone whose parent is also going passes its weights on to the parent's parent, and so on
        if into == "ACTIVE":
            moved[n] = active
            continue
        p = parent_of.get(n)
        while p in parent_of and p in doomed:
            p = parent_of[p]
        moved[n] = p
    for ob in meshes:
        index = {g.index: g.name for g in ob.vertex_groups}
        adds = []
        for v in ob.data.vertices:
            for g in v.groups:
                name = index.get(g.group)
                if name in moved and moved[name] and g.weight > 0:
                    adds.append((v.index, name, g.weight))
        for vi, name, w in adds:
            target = ob.vertex_groups.get(moved[name]) or ob.vertex_groups.new(name=moved[name])
            target.add([vi], w, "ADD")
        for name in {n for _, n, _ in adds}:
            ob.vertex_groups.remove(ob.vertex_groups[name])
    if keep:
        return len(doomed)

    def action(edit, _):
        for n in doomed:
            if n in edit:
                eb = edit[n]
                for child in list(eb.children):
                    child.parent = edit[active] if into == "ACTIVE" else eb.parent
                    child.use_connect = False
                edit.remove(eb)

    armature.edit_bones(context, rig, doomed, action)
    return len(doomed)


class KAT_OT_optimize_scan(bpy.types.Operator):
    bl_idname = "kat.optimize_scan"
    bl_label = "Scan"
    bl_description = "Count what each step would change. Nothing is changed"

    @operator_guard
    def execute(self, context):
        rig = armature.rig_of(context)
        if rig is None:
            raise ValueError("Select an armature, or a mesh rigged to one")
        counts = scan(context, rig)
        listed = ", ".join(f"{v} {k}" for k, v in counts.items() if v)
        self.report({"INFO"}, f"Found: {listed}" if listed else "Nothing to do")
        return {"FINISHED"}


class KAT_OT_optimize_preset(bpy.types.Operator):
    bl_idname = "kat.optimize_preset"
    bl_label = "Tick steps"
    bl_description = "Tick the steps of a preset: only the ones that remove what nothing uses, or everything"
    kind: bpy.props.EnumProperty(items=[("SAFE", "Safe", "Only remove what nothing uses"), ("ALL", "Everything", "All steps"),
                                        ("PC", "PC Good", "Everything, aiming for PC rank Good: 70,000 triangles, textures up to 2048"),
                                        ("QUEST", "Quest", "Everything, aiming for Quest rank Good: 10,000 triangles, textures up to 512")],
                                 default="SAFE")

    def execute(self, context):
        o = context.scene.kat
        for prop, _, _, _ in STEPS:
            setattr(o, prop, prop in SAFE or self.kind != "SAFE")
        if self.kind == "PC":
            reduce.settings(context).budget = "GOOD"
            o.tex_limit, o.atlas_size, o.platform = "2048", "2048", "PC"
        elif self.kind == "QUEST":
            reduce.settings(context).budget = "QUEST_GOOD"
            o.tex_limit, o.atlas_size, o.platform = "512", "1024", "QUEST"
        return {"FINISHED"}


class KAT_OT_optimize(bpy.types.Operator):
    bl_idname = "kat.optimize"
    bl_label = "Optimize"
    bl_description = "Run the ticked steps. Outfit meshes are never joined, so clothes can still switch. Ctrl+Z undoes it"
    bl_options = {"REGISTER", "UNDO"}

    @operator_guard
    def execute(self, context):
        rig = armature.rig_of(context)
        if rig is None:
            raise ValueError("Select an armature, or a mesh rigged to one")
        for line in optimize(context, rig, context.scene.kat):
            self.report({"INFO"}, line)
        return {"FINISHED"}


class KAT_OT_join_selected(bpy.types.Operator):
    bl_idname = "kat.join_selected"
    bl_label = "Join selected"
    bl_description = "Join the selected meshes into the active one"
    bl_options = {"REGISTER", "UNDO"}

    @operator_guard
    def execute(self, context):
        chosen = [o for o in context.selected_objects if o.type == "MESH"]
        target = context.active_object
        if target not in chosen or len(chosen) < 2:
            raise ValueError("Select at least two meshes, with the one to keep active")
        with context.temp_override(active_object=target, object=target, selected_objects=chosen,
                                   selected_editable_objects=chosen):
            bpy.ops.object.join()
        self.report({"INFO"}, f"Joined {len(chosen)} meshes into {target.name}")
        return {"FINISHED"}


class KAT_OT_weld(bpy.types.Operator):
    bl_idname = "kat.weld"
    bl_label = "Remove doubles"
    bl_description = "Merge vertices closer than the distance. Meshes with shape keys are skipped, it would break them"
    bl_options = {"REGISTER", "UNDO"}
    distance: FloatProperty(name="Distance", default=0.00002, min=0.0, max=0.1, precision=5, unit="LENGTH")

    @operator_guard
    def execute(self, context):
        rig = armature.rig_of(context)
        objs = armature.meshes_of(rig) if rig else [o for o in context.selected_objects if o.type == "MESH"]
        removed, skipped = 0, []
        for ob in objs:
            try:
                removed += mesh.weld(ob, self.distance)
            except ValueError:
                skipped.append(ob.name)
        self.report({"INFO"}, f"Merged {removed} vertices")
        if skipped:
            self.report({"WARNING"}, "Skipped (shape keys): " + ", ".join(skipped))
        return {"FINISHED"}


class KAT_OT_merge_bones(bpy.types.Operator):
    bl_idname = "kat.merge_bones"
    bl_label = "Merge to parent"
    bl_description = "Move the weights of the selected bones (Edit or Pose mode) to their parents and delete them"
    bl_options = {"REGISTER", "UNDO"}
    stays_in_edit = True  # it reads the Edit mode selection, then returns to Edit mode

    @operator_guard
    def execute(self, context):
        rig = armature.rig_of(context)
        if rig is None:
            raise ValueError("Select an armature, or a mesh rigged to one")
        editing = rig.mode == "EDIT"
        names = compat.selected_bones(rig)
        if editing:  # the bones' parents and the weights are read in Object mode
            bpy.ops.object.mode_set(mode="OBJECT")
        try:
            bones = rig.data.bones
            todo = [n for n in names if n in bones and bones[n].parent is not None]
            if not todo:
                raise ValueError("Select the bones to merge in Edit or Pose mode. A bone without a parent can't be merged")
            done = merge_bones(context, rig, todo)
        finally:
            if editing:
                bpy.ops.object.mode_set(mode="EDIT")
        self.report({"INFO"}, f"Merged {done} bones into their parents")
        return {"FINISHED"}


class KAT_OT_group_bones(bpy.types.Operator):
    bl_idname = "kat.group_bones"
    bl_label = "Create roots"
    bl_description = ("Give loose bones that start on the same bone and are named alike (a skirt, hair) one root bone of their "
                      "own, named after them: skirt_222_2201, skirt_222_2202 get skirt_root_222_22. Weights stay as they are")
    bl_options = {"REGISTER", "UNDO"}

    @operator_guard
    def execute(self, context):
        rig = armature.rig_of(context)
        if rig is None:
            raise ValueError("Select an armature, or a mesh rigged to one")
        groups = categories(rig)
        if not groups:
            raise ValueError("No group of loose bones with the same parent and a similar name")
        make_categories(context, rig, groups)
        self.report({"INFO"}, "Created " + ", ".join(g[0] for g in groups[:6]) + (" ..." if len(groups) > 6 else ""))
        return {"FINISHED"}


class KAT_OT_bones_clean(bpy.types.Operator):
    bl_idname = "kat.bones_clean"
    bl_label = "Delete"
    bl_description = "Delete what is ticked: bones nothing uses, constraints on the bones, vertex groups without weights"
    bl_options = {"REGISTER", "UNDO"}

    @operator_guard
    def execute(self, context):
        rig = armature.rig_of(context)
        if rig is None:
            raise ValueError("Select an armature, or a mesh rigged to one")
        o = context.scene.kat
        armature.check_ready(context, rig)
        meshes = armature.meshes_of(rig)
        lines = []
        if o.opt_del_constraints:
            count = 0
            for pb in rig.pose.bones:
                for con in list(pb.constraints):
                    pb.constraints.remove(con)
                    count += 1
            lines.append(f"{count} constraints")
        if o.opt_del_groups:
            lines.append(f"{sum(mesh.clean_groups(ob, rig) for ob in meshes)} vertex groups")
        if o.opt_del_bones:
            names = [n for n in armature.prunable_bones(rig, meshes)
                     if not (o.opt_keep_twist and "twist" in n.lower()) and not (o.opt_skip_hidden and rig.data.bones[n].hide)]
            armature.delete_bones(context, rig, names)
            lines.append(f"{len(names)} bones")
        self.report({"INFO"}, "Deleted " + ", ".join(lines) if lines else "Nothing is ticked")
        return {"FINISHED"}


class KAT_OT_bones_connect(bpy.types.Operator):
    bl_idname = "kat.bones_connect"
    bl_label = "Connect bones"
    bl_description = ("Bones with one child end where the child starts, and are connected to it. Rest shapes of the bones "
                      "change a little, the pose and the weights do not")
    bl_options = {"REGISTER", "UNDO"}

    @operator_guard
    def execute(self, context):
        rig = armature.rig_of(context)
        if rig is None:
            raise ValueError("Select an armature, or a mesh rigged to one")
        armature.check_ready(context, rig)
        done = []

        def action(edit, _):
            for eb in edit:
                kids = list(eb.children)
                if len(kids) == 1 and (kids[0].head - eb.head).length > 1e-5:
                    eb.tail = kids[0].head
                    kids[0].use_connect = True
                    done.append(eb.name)
        armature.edit_bones(context, rig, [], action)
        self.report({"INFO"}, f"Connected {len(done)} bones")
        return {"FINISHED"}


class OptimizePanel:
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "KAT"
    bl_parent_id = "KAT_PT_optimize_auto"


class KAT_PT_optimize_auto(OptimizePanel, bpy.types.Panel):
    bl_label = "Optimize"
    bl_idname = "KAT_PT_optimize_auto"
    bl_order = 1060
    bl_parent_id = ""
    bl_options = {"DEFAULT_CLOSED"}

    def draw_header(self, context):
        self.layout.label(icon="MOD_DECIM")

    def draw(self, context):
        o = context.scene.kat
        layout = self.layout
        rig = armature.rig_of(context)
        if rig is None:
            uiutil.paragraph(layout, context, "Select a rigged model.", "INFO")
            return
        meshes = armature.meshes_of(rig)
        grid = layout.grid_flow(row_major=True, columns=3, even_columns=True, align=True)
        grid.label(text=f"{len(meshes)} meshes")
        grid.label(text=f"{sum(len(o.material_slots) for o in meshes)} slots")
        grid.label(text=f"{len(rig.data.bones)} bones")
        counts = found["counts"] if found["rig"] == rig.name else {}
        box = layout.box()
        for prop, label, key, icon in STEPS:
            row = box.row(align=True)
            row.prop(o, prop, text=label, icon=icon)
            if key in counts:
                sub = row.row(align=True)
                sub.alignment = "RIGHT"
                sub.enabled = False
                sub.label(text=str(counts[key]))
        row = layout.row(align=True)
        row.operator("kat.optimize_preset", text="Safe").kind = "SAFE"
        row.operator("kat.optimize_preset", text="PC Good").kind = "PC"
        row.operator("kat.optimize_preset", text="Quest").kind = "QUEST"
        if o.opt_triangles:
            budget = reduce.settings(context).bl_rna.properties["budget"].enum_items[reduce.settings(context).budget]
            layout.label(text=f"Triangles: {budget.name} (set under Reduce triangles)", icon="MOD_DECIM")
        kept = len(outfits.outfit_objects(context))
        if o.opt_join:
            layout.label(text=f"{kept} outfit mesh(es) stay separate" if kept else "No outfits listed: every mesh joins",
                         icon="MATCLOTH" if kept else "ERROR")
        row = layout.row(align=True)
        row.operator("kat.optimize_scan", icon="VIEWZOOM")
        sub = row.row(align=True)
        sub.scale_x = 1.6
        sub.operator("kat.optimize", icon="CHECKMARK")
        layout.label(text="Ctrl+Z undoes it", icon="LOOP_BACK")


class KAT_PT_optimize_meshes(OptimizePanel, bpy.types.Panel):
    bl_label = "Join & weld"
    bl_idname = "KAT_PT_optimize_meshes"
    bl_order = 1031
    bl_options = {"DEFAULT_CLOSED"}

    def draw_header(self, context):
        self.layout.label(icon="MESH_DATA")

    def draw(self, context):
        layout = self.layout
        row = layout.row(align=True)
        row.operator("kat.join_meshes", text="Join all", icon="AUTOMERGE_ON")
        row.operator("kat.join_selected", text="Join selected")
        layout.operator("kat.weld", icon="SNAP_VERTEX")


class KAT_PT_optimize_materials(OptimizePanel, bpy.types.Panel):
    bl_label = "Materials & atlas"
    bl_idname = "KAT_PT_optimize_materials"
    bl_order = 1032
    bl_options = {"DEFAULT_CLOSED"}

    def draw_header(self, context):
        self.layout.label(icon="MATERIAL")

    def draw(self, context):
        o = context.scene.kat
        layout = self.layout
        layout.operator("kat.materials_clean", icon="BRUSH_DATA")
        box = layout.box()
        box.label(text="Atlas", icon="TEXTURE")
        uiutil.paragraph(box, context, "Packs the materials of the selected meshes (or the armature's) into one texture "
                                       "and one slot. Normal maps, emission and cut-out transparency come along.")
        box.prop(o, "atlas_size", text="Size")
        box.prop(o, "atlas_loose", text="Ignore colour alpha")
        box.operator("kat.atlas_scan", icon="VIEWZOOM")
        op = box.operator("kat.atlas", icon="TEXTURE")
        op.size = o.atlas_size
        if atlas.last["text"]:
            box.label(text=atlas.last["text"], icon="INFO")
        for name, reason in atlas.last["skipped"][:8]:
            uiutil.paragraph(box, context, f"{name}: {reason}", "DOT").enabled = False


class KAT_PT_optimize_bones(OptimizePanel, bpy.types.Panel):
    bl_label = "Bone merging"
    bl_idname = "KAT_PT_optimize_bones"
    bl_order = 1036
    bl_options = {"DEFAULT_CLOSED"}

    def draw_header(self, context):
        self.layout.label(icon="BONE_DATA")

    def draw(self, context):
        o = context.scene.kat
        layout = self.layout
        rig = armature.rig_of(context)
        box = layout.box()
        box.label(text="Merge to parent", icon="AUTOMERGE_ON")
        uiutil.paragraph(box, context, "Select bones in Edit or Pose mode. Their weights go to the parent and the bones are deleted.", "INFO")
        picked = len(compat.selected_bones(rig)) if rig is not None else 0
        box.label(text=f"{picked} bones selected")
        row = box.row()
        row.scale_y = 1.2
        row.enabled = picked > 0
        row.operator("kat.merge_bones", icon="AUTOMERGE_ON")
        box = layout.box()
        box.label(text="Roots for loose bones", icon="GROUP_BONE")
        groups = categories(rig) if rig is not None and rig.mode != "EDIT" else []
        if groups:
            for name, _, tops in groups[:5]:
                box.label(text=f"{name} ({len(tops)} chains)", icon="BONE_DATA")
            if len(groups) > 5:
                box.label(text=f"and {len(groups) - 5} more")
        else:
            uiutil.paragraph(box, context, "Nothing to group (or the armature is in Edit mode).", "INFO")
        row = box.row()
        row.scale_y = 1.2
        row.enabled = bool(groups)
        row.operator("kat.group_bones", icon="GROUP_BONE")
        box = layout.box()
        box.label(text="Delete", icon="TRASH")
        col = box.column(align=True)
        for prop in ("opt_del_bones", "opt_del_constraints", "opt_del_groups"):
            col.prop(o, prop)
        sub = box.column(align=True)
        sub.enabled = o.opt_del_bones
        sub.prop(o, "opt_keep_twist")
        sub.prop(o, "opt_skip_hidden")
        row = box.row()
        row.enabled = rig is not None
        row.operator("kat.bones_clean", icon="TRASH")
        row = layout.row()
        row.enabled = rig is not None
        row.operator("kat.bones_connect", icon="CONSTRAINT_BONE")


classes = (KAT_OT_optimize_scan, KAT_OT_optimize_preset, KAT_OT_optimize, KAT_OT_join_selected, KAT_OT_weld, KAT_OT_merge_bones, KAT_OT_group_bones,
           KAT_OT_bones_clean, KAT_OT_bones_connect,
           KAT_PT_optimize_auto, KAT_PT_optimize_meshes, KAT_PT_optimize_materials,
           KAT_PT_optimize_bones)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
