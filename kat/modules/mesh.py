# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Krislyz
"""Mesh clean-up for rigged avatars, and the one-click Fix model."""
import bmesh
import bpy
from bpy.props import BoolProperty, EnumProperty, FloatProperty

from ..core.safe import operator_guard
from . import armature

MAX_INFLUENCES = 4  # VRChat skins with four bone weights per vertex


def join_meshes(context, rig):
    meshes = armature.meshes_of(rig)
    if len(meshes) < 2:
        return meshes[0] if meshes else None
    target = meshes[0]
    with context.temp_override(active_object=target, object=target, selected_objects=meshes,
                               selected_editable_objects=meshes):
        bpy.ops.object.join()
    return target


def unused_groups(ob, rig, drop_non_bone=False):
    """Vertex groups with no weight at all; optionally also those that match no bone."""
    bones = {b.name for b in rig.data.bones}
    used = set()
    names = [g.name for g in ob.vertex_groups]
    for v in ob.data.vertices:
        for g in v.groups:
            if g.weight > 0:
                used.add(names[g.group])
    return [g.name for g in ob.vertex_groups if g.name not in used or (drop_non_bone and g.name not in bones)]


def clean_groups(ob, rig, drop_non_bone=False):
    """Remove vertex groups with no weight at all; optionally those that match no bone. Returns count."""
    doomed = unused_groups(ob, rig, drop_non_bone)
    for name in doomed:
        ob.vertex_groups.remove(ob.vertex_groups[name])
    return len(doomed)


def heavy_vertices(ob, rig, limit=MAX_INFLUENCES):
    """How many vertices have more than `limit` bone weights."""
    deform = {g.index for g in ob.vertex_groups if g.name in {b.name for b in rig.data.bones}}
    return sum(1 for v in ob.data.vertices if sum(1 for g in v.groups if g.group in deform and g.weight > 0) > limit)


def limit_and_normalize(ob, rig, limit=MAX_INFLUENCES):
    """Keep the strongest `limit` bone weights per vertex, scale them to sum to 1. Returns vertices changed."""
    bones = {b.name for b in rig.data.bones}
    deform = {g.index for g in ob.vertex_groups if g.name in bones}
    changed = 0
    for v in ob.data.vertices:
        ws = sorted(((g.weight, g.group) for g in v.groups if g.group in deform and g.weight > 0), reverse=True)
        if not ws:
            continue
        for _, idx in ws[limit:]:
            ob.vertex_groups[idx].remove([v.index])
        keep = ws[:limit]
        total = sum(w for w, _ in keep)
        if len(ws) > limit or abs(total - 1.0) > 1e-5:
            for w, idx in keep:
                ob.vertex_groups[idx].add([v.index], w / total, "REPLACE")
            changed += 1
    return changed


def weld(ob, distance):
    """Merge vertices closer than `distance`. Refuses meshes with shape keys: welding would corrupt them."""
    if ob.data.shape_keys:
        raise ValueError(f"{ob.name} has shape keys, welding it would break them")
    bm = bmesh.new()
    try:
        bm.from_mesh(ob.data)
        before = len(bm.verts)
        bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=distance)
        bm.to_mesh(ob.data)
    finally:
        bm.free()
    ob.data.update()
    return before - len(ob.data.vertices)


def apply_transforms(context, rig):
    """Apply rotation and scale on the armature and its meshes together so nothing moves in the world."""
    objs = [rig] + armature.meshes_of(rig)
    for ob in objs:
        if ob.data.users > 1:
            ob.data = ob.data.copy()
    with context.temp_override(active_object=rig, object=rig, selected_objects=objs, selected_editable_objects=objs):
        bpy.ops.object.transform_apply(location=False, rotation=True, scale=True)
    return len(objs)


def fix_model(context, rig, *, style="unity", rename=False, transforms=False, clean=False, limit=False,
              drop_non_bone=False, join=False, weld_distance=0.0, prune=False):
    """Run only the steps asked for, in a safe order. Returns a list of human-readable result lines."""
    armature.check_ready(context, rig)
    log = []
    left_out = armature.unreachable_meshes(rig)
    if left_out:
        log.append("Skipped hidden or excluded meshes: " + ", ".join(o.name for o in left_out))
    if transforms:
        log.append(f"Applied rotation/scale to {apply_transforms(context, rig)} objects")
    if rename:
        count, _ = armature.rename_bones(rig, style)
        log.append(f"Renamed {count} humanoid bones")
    if join:
        main = join_meshes(context, rig)
        log.append(f"Joined meshes into {main.name}" if main else "No meshes to join")
    for ob in armature.meshes_of(rig):
        if clean or drop_non_bone:
            log.append(f"{ob.name}: removed {clean_groups(ob, rig, drop_non_bone)} vertex groups")
        if limit:
            log.append(f"{ob.name}: limited weights on {limit_and_normalize(ob, rig)} vertices")
        if weld_distance > 0:
            try:
                log.append(f"{ob.name}: welded {weld(ob, weld_distance)} vertices")
            except ValueError as err:
                log.append(f"Skipped weld: {err}")
    if prune:
        names = armature.prunable_bones(rig, armature.meshes_of(rig))
        armature.delete_bones(context, rig, names)
        log.append(f"Removed {len(names)} unused bones")
    if not log or log == [f"Skipped hidden or excluded meshes: " + ", ".join(o.name for o in left_out)]:
        log.append("No fix options are ticked (Fix options panel)")
    best, _ = armature.map_bones(rig)
    missing = armature.missing_required(best)
    log.append("Missing required bones: " + ", ".join(missing) if missing else "All required humanoid bones present")
    return log


class KAT_OT_fix_model(bpy.types.Operator):
    bl_idname = "kat.fix_model"
    bl_label = "Fix model"
    bl_description = "Run the steps ticked in the Fix options panel. Only naming the humanoid bones is ticked at first"
    bl_options = {"REGISTER", "UNDO"}

    @operator_guard
    def execute(self, context):
        rig = armature.rig_of(context)
        if rig is None:
            raise ValueError("Select an armature, or a mesh rigged to one")
        o = context.scene.kat
        for line in fix_model(context, rig, style=o.fix_style, rename=o.fix_rename, transforms=o.fix_transforms,
                              clean=o.fix_clean_groups, limit=o.fix_limit_weights, drop_non_bone=o.fix_drop_non_bone):
            self.report({"WARNING" if line.startswith(("Missing", "Skipped", "No fix")) else "INFO"}, line)
        return {"FINISHED"}


class KAT_PT_fix(bpy.types.Panel):
    bl_label = "Fix & clean"
    bl_idname = "KAT_PT_fix"
    bl_order = 1010
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "KAT"
    bl_options = {"DEFAULT_CLOSED"}

    def draw_header(self, context):
        self.layout.label(icon="MODIFIER")

    def draw(self, context):
        o = context.scene.kat
        layout = self.layout
        box = layout.box()
        box.label(text="Safe", icon="CHECKMARK")
        row = box.row()
        row.prop(o, "fix_rename")
        if o.fix_rename:
            box.prop(o, "fix_style", text="")
        box = layout.box()
        box.label(text="Changes your data", icon="ERROR")
        col = box.column(align=True)
        for prop in ("fix_transforms", "fix_clean_groups", "fix_limit_weights", "fix_drop_non_bone"):
            col.prop(o, prop)
        row = layout.row()
        row.scale_y = 1.3
        row.enabled = armature.rig_of(context) is not None
        row.operator("kat.fix_model", icon="MODIFIER")
        layout.label(text="Everything undoes with Ctrl+Z", icon="LOOP_BACK")
        layout.separator()
        col = layout.column(align=True)
        col.enabled = armature.rig_of(context) is not None
        col.operator("kat.translate", icon="WORDWRAP_ON")


class KAT_OT_join_meshes(bpy.types.Operator):
    bl_idname = "kat.join_meshes"
    bl_label = "Join meshes"
    bl_description = "Join every mesh rigged to the armature into one"
    bl_options = {"REGISTER", "UNDO"}

    @operator_guard
    def execute(self, context):
        rig = armature.rig_of(context)
        if rig is None:
            raise ValueError("Select an armature, or a mesh rigged to one")
        armature.check_ready(context, rig)
        before = len(armature.meshes_of(rig))
        if join_meshes(context, rig) is None:
            raise ValueError("No meshes are rigged to this armature")
        self.report({"INFO"}, f"Joined {before} meshes into one" if before > 1 else "Only one mesh, nothing to join")
        return {"FINISHED"}


classes = (KAT_OT_fix_model, KAT_OT_join_meshes, KAT_PT_fix)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
