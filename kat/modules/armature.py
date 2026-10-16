# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Krislyz
"""Armature fixing: humanoid naming, required-bone check, pruning, apply pose as rest."""
import contextlib
import re

import bpy
from bpy.props import EnumProperty
from mathutils import Matrix, Vector

from ..core import humanoid
from ..core.safe import operator_guard

STYLES = [("unity", "Unity (LeftUpperArm)", ""), ("blender", "Blender (UpperArm.L)", "")]


def rig_of(context):
    """The armature the user means: the active one, or the one the active mesh is rigged to."""
    ob = context.active_object
    if ob is None:
        return None
    if ob.type == "ARMATURE":
        return ob
    for mod in getattr(ob, "modifiers", ()):
        if mod.type == "ARMATURE" and mod.object:
            return mod.object
    return ob.parent if ob.parent and ob.parent.type == "ARMATURE" else None


def rigged_meshes(rig):
    # this scene only: objects of other scenes (version snapshots) or in no scene are not part of the model
    return [o for o in bpy.context.scene.objects
            if o.type == "MESH" and any(m.type == "ARMATURE" and m.object == rig for m in o.modifiers)]


def meshes_of(rig, scene=None):
    """Meshes rigged to `rig` that operators can actually work on: in the view layer and visible.
    (Meshes in excluded collections are not in the depsgraph and cannot be selected.)"""
    layer = bpy.context.view_layer
    return [o for o in rigged_meshes(rig) if o.name in layer.objects and o.visible_get()]


def unreachable_meshes(rig):
    """Meshes rigged to `rig` that meshes_of leaves out, so operators can say so."""
    usable = set(meshes_of(rig))
    return [o for o in rigged_meshes(rig) if o not in usable]


def check_ready(context, rig):
    """Raise ValueError before anything changes if `rig` can't be worked on."""
    if rig.name not in context.view_layer.objects:
        raise ValueError(f"{rig.name} is not in the current view layer")
    if not rig.visible_get():
        raise ValueError(f"{rig.name} is hidden. Show it first")


def map_bones(rig):
    """{canonical key: bone name} for the best candidate of each key, plus the leftovers.
    Deform bones win over control bones, then the shallowest one."""
    bones = rig.data.bones
    depth = {b.name: len(b.parent_recursive) for b in bones}
    deform = {b.name: b.use_deform for b in bones}
    resolved = humanoid.resolve_all([b.name for b in bones])
    best = {}
    for name, key in resolved.items():
        if key is None:
            continue
        rank = (not deform[name], depth[name])
        if key not in best or rank < (not deform[best[key]], depth[best[key]]):
            best[key] = name
    mapped = set(best.values())
    return best, [b.name for b in bones if b.name not in mapped]


def missing_required(best):
    return [k for k in humanoid.REQUIRED if k not in best]


_map_cache = {"key": None, "value": None}


def cached_map(rig):
    """map_bones for panels. They redraw all the time and name matching is not free."""
    key = (rig.name, tuple(b.name for b in rig.data.bones))
    if _map_cache["key"] != key:
        _map_cache.update(key=key, value=map_bones(rig))
    return _map_cache["value"]


def rename_bones(rig, style):
    """Rename recognised bones to the chosen style. Returns (renamed count, mapping used)."""
    best, _ = map_bones(rig)
    plan = {name: humanoid.style_name(key, style) for key, name in best.items()}
    bones = rig.data.bones
    targets = set(plan.values())
    for b in bones:  # someone else already owns a target name: move them aside
        if b.name not in plan and b.name in targets:
            b.name = b.name + "_old"
    pending = [(n, final) for n, final in plan.items() if n != final]
    for i, (name, _) in enumerate(pending):  # two phases so swaps and chains never collide
        bones[name].name = f"__kat{i}"
    for i, (_, final) in enumerate(pending):
        bones[f"__kat{i}"].name = final
    return len(pending), plan


def weighted_groups(meshes):
    used = set()
    for ob in meshes:
        names = [g.name for g in ob.vertex_groups]
        for v in ob.data.vertices:
            for g in v.groups:
                if g.weight > 0:
                    used.add(names[g.group])
    return used


def referenced_bones(rig):
    """Bones something other than skin weights depends on: bone parents, constraints, drivers."""
    used = set()
    for ob in bpy.data.objects:
        if ob.parent == rig and ob.parent_type == "BONE" and ob.parent_bone:
            used.add(ob.parent_bone)
        for con in ob.constraints:
            if getattr(con, "target", None) == rig and getattr(con, "subtarget", ""):
                used.add(con.subtarget)
    for pb in rig.pose.bones:
        for con in pb.constraints:
            for attr in ("subtarget", "pole_subtarget"):
                if getattr(con, attr, ""):
                    used.add(getattr(con, attr))
    for holder in (*bpy.data.objects, rig.data):
        anim = getattr(holder, "animation_data", None)
        for fc in (anim.drivers if anim else ()):
            if holder in (rig, rig.data):  # drivers on the rig itself name bones in their data path
                used.update(re.findall(r'bones\["([^"]+)"\]', fc.data_path))
            for var in fc.driver.variables:
                for t in var.targets:
                    if t.id == rig and t.bone_target:
                        used.add(t.bone_target)
    return used


def prunable_bones(rig, meshes):
    """Bones nothing depends on: no weights, not humanoid, not referenced, nothing kept below them."""
    best, _ = map_bones(rig)
    keep = weighted_groups(meshes) | set(best.values()) | referenced_bones(rig)
    bones = rig.data.bones
    for name in list(keep):
        b = bones.get(name)
        while b is not None and b.parent is not None:
            keep.add(b.parent.name)
            b = b.parent
    return [b.name for b in bones if b.name not in keep]


@contextlib.contextmanager
def in_mode(context, rig, mode):
    """Make `rig` active in `mode`. Always puts back the active object and the mode it was in."""
    layer = context.view_layer
    previous = layer.objects.active
    previous_mode = previous.mode if previous is not None else "OBJECT"
    layer.objects.active = rig
    try:
        with context.temp_override(active_object=rig, object=rig):
            bpy.ops.object.mode_set(mode=mode)
            try:
                yield
            finally:
                if rig.mode != "OBJECT":
                    bpy.ops.object.mode_set(mode="OBJECT")
    finally:
        layer.objects.active = previous
        if previous is not None and previous_mode != "OBJECT" and previous.mode != previous_mode:
            with contextlib.suppress(RuntimeError):
                with context.temp_override(active_object=previous, object=previous):
                    bpy.ops.object.mode_set(mode=previous_mode)


def edit_bones(context, rig, names, action):
    with in_mode(context, rig, "EDIT"):
        action(rig.data.edit_bones, names)


def delete_bones(context, rig, names):
    def action(edit, names):
        for n in names:
            if n in edit:
                edit.remove(edit[n])
    edit_bones(context, rig, names, action)


def _evaluated_coords(ob, depsgraph):
    mesh = ob.evaluated_get(depsgraph).to_mesh()
    try:
        coords = [0.0] * (len(mesh.vertices) * 3)
        mesh.vertices.foreach_get("co", coords)
        return coords
    finally:
        ob.evaluated_get(depsgraph).to_mesh_clear()


def apply_pose_as_rest(context, rig):
    """Bake the current pose into the rest pose, keeping every shape key correct.
    Returns the number of meshes fixed, or raises ValueError with a reason."""
    check_ready(context, rig)
    meshes = meshes_of(rig)
    left_out = unreachable_meshes(rig)
    if left_out:
        raise ValueError("Meshes rigged to this armature are hidden or in excluded collections: "
                         + ", ".join(o.name for o in left_out))
    baked = {}
    restore = []  # callables that undo each temporary change, run in reverse
    try:
        for ob in meshes:
            for m in [m for m in ob.modifiers if m.type != "ARMATURE" and m.show_viewport]:
                m.show_viewport = False
                restore.append(lambda m=m: setattr(m, "show_viewport", True))
            keys = ob.data.shape_keys.key_blocks if ob.data.shape_keys else []
            restore.append(lambda ob=ob, was=(ob.show_only_shape_key, ob.active_shape_key_index): (
                setattr(ob, "show_only_shape_key", was[0]), setattr(ob, "active_shape_key_index", was[1])))
            for kb in keys:  # a muted key evaluates as its reference key, which would overwrite it
                if kb.mute:
                    kb.mute = False
                    restore.append(lambda kb=kb: setattr(kb, "mute", True))
        context.view_layer.update()
        for ob in meshes:
            n = len(ob.data.vertices)
            keys = ob.data.shape_keys.key_blocks if ob.data.shape_keys else []
            coords = []
            if keys:
                ob.show_only_shape_key = True
                for i in range(len(keys)):
                    ob.active_shape_key_index = i
                    context.view_layer.update()
                    coords.append(_evaluated_coords(ob, context.evaluated_depsgraph_get()))
            else:
                coords.append(_evaluated_coords(ob, context.evaluated_depsgraph_get()))
            if any(len(c) != n * 3 for c in coords):
                raise ValueError(f"{ob.name}: modifiers change the vertex count, apply them first")
            baked[ob] = coords
        with in_mode(context, rig, "POSE"):
            bpy.ops.pose.armature_apply(selected=False)
    finally:
        for undo in reversed(restore):
            undo()
    for ob, coords in baked.items():
        keys = ob.data.shape_keys.key_blocks if ob.data.shape_keys else []
        if keys:
            for kb, co in zip(keys, coords):
                kb.data.foreach_set("co", co)
        ob.data.vertices.foreach_set("co", coords[0])
        ob.data.update()
    return len(baked)


def make_tpose(context, rig):
    """Pose the arms straight out sideways and the legs straight down, joint by joint from the shoulder and hip
    outwards. Only the pose changes (undoable); apply it as the rest pose to keep it.
    Returns (bones moved, limbs that could not be found)."""
    check_ready(context, rig)
    best, _ = map_bones(rig)
    centre = rig.data.bones[best["Hips"]].head_local.x if "Hips" in best else 0.0
    moved, lacking = [], []
    for side in humanoid.SIDES:
        top = best.get(side + "UpperArm")
        sign = 1.0 if top is None or rig.data.bones[top].head_local.x >= centre else -1.0
        for (first, second), target in (
            (("UpperArm", "LowerArm"), Vector((sign, 0.0, 0.0))),
            (("LowerArm", "Hand"), Vector((sign, 0.0, 0.0))),
            (("UpperLeg", "LowerLeg"), Vector((0.0, 0.0, -1.0))),
            (("LowerLeg", "Foot"), Vector((0.0, 0.0, -1.0))),
        ):
            a, b = best.get(side + first), best.get(side + second)
            if a is None or b is None:
                lacking.append(f"{side}{first}")
                continue
            context.view_layer.update()  # the parent's new pose has to be in place before we read this bone
            start, end = rig.pose.bones[a], rig.pose.bones[b]
            along = end.head - start.head
            if along.length < 1e-6:
                continue
            turn = along.normalized().rotation_difference(target)
            if turn.angle < 1e-4:
                continue
            pivot = Matrix.Translation(start.head)
            start.matrix = pivot @ turn.to_matrix().to_4x4() @ pivot.inverted() @ start.matrix
            moved.append(a)
    context.view_layer.update()
    return moved, sorted(set(lacking))


class KatRigOp:
    """Shared by operators that work on the rig the selection points at."""
    bl_options = {"REGISTER", "UNDO"}

    def rig(self, context):
        rig = rig_of(context)
        if rig is None:
            raise ValueError("Select an armature, or a mesh rigged to one")
        return rig

    def warn_unreachable(self, rig):
        left_out = unreachable_meshes(rig)
        if left_out:
            self.report({"WARNING"}, "Skipped hidden or excluded meshes: " + ", ".join(o.name for o in left_out))


class KAT_OT_bones_rename(KatRigOp, bpy.types.Operator):
    bl_idname = "kat.bones_rename"
    bl_label = "Name humanoid bones"
    bl_description = "Rename recognised humanoid bones (Mixamo, MMD, Unreal, VRM, Blender names) to one naming style"
    style: EnumProperty(name="Style", items=STYLES, default="unity")

    @operator_guard
    def execute(self, context):
        rig = self.rig(context)
        count, plan = rename_bones(rig, self.style)
        best, rest = map_bones(rig)
        missing = missing_required(best)
        self.report({"INFO"}, f"Renamed {count} bones, {len(rest)} left alone")
        if missing:
            self.report({"WARNING"}, "Missing required bones: " + ", ".join(missing))
        return {"FINISHED"}


class KAT_OT_bones_check(KatRigOp, bpy.types.Operator):
    bl_idname = "kat.bones_check"
    bl_label = "Check humanoid bones"
    bl_description = "List which required humanoid bones the rig has"

    @operator_guard
    def execute(self, context):
        best, rest = map_bones(self.rig(context))
        missing = missing_required(best)
        if missing:
            self.report({"WARNING"}, "Missing required bones: " + ", ".join(missing))
        else:
            self.report({"INFO"}, f"All {len(humanoid.REQUIRED)} required bones found ({len(best)} humanoid in total)")
        return {"FINISHED"}


class KAT_OT_bones_prune(KatRigOp, bpy.types.Operator):
    bl_idname = "kat.bones_prune"
    bl_label = "Remove unused bones"
    bl_description = "Delete bones nothing uses: no weights, constraints, drivers or parented objects (humanoid bones stay)"

    @operator_guard
    def execute(self, context):
        rig = self.rig(context)
        check_ready(context, rig)
        self.warn_unreachable(rig)
        names = prunable_bones(rig, meshes_of(rig))
        delete_bones(context, rig, names)
        self.report({"INFO"}, f"Removed {len(names)} unused bones")
        return {"FINISHED"}


class KAT_OT_tpose(KatRigOp, bpy.types.Operator):
    bl_idname = "kat.tpose"
    bl_label = "Make T-pose"
    bl_description = "Pose the arms straight out and the legs straight down. Then use Apply pose as rest pose to keep it"

    @operator_guard
    def execute(self, context):
        rig = self.rig(context)
        moved, lacking = make_tpose(context, rig)
        if not moved and lacking:
            raise ValueError("Could not find the arm and leg bones. Name the humanoid bones first")
        self.report({"INFO"}, f"Moved {len(moved)} bones. Apply pose as rest pose to keep it")
        if lacking:
            self.report({"WARNING"}, "Missing: " + ", ".join(lacking))
        return {"FINISHED"}


class KAT_OT_pose_start(KatRigOp, bpy.types.Operator):
    bl_idname = "kat.pose_start"
    bl_label = "Start pose mode"
    bl_description = "Go into pose mode on the armature without touching the pose, to test how the model moves"

    @operator_guard
    def execute(self, context):
        rig = self.rig(context)
        context.view_layer.objects.active = rig
        rig.select_set(True)
        rig.data.pose_position = "POSE"
        bpy.ops.object.mode_set(mode="POSE")
        self.report({"INFO"}, "Pose mode. Move bones to test the model, then Stop pose mode or Save as rest pose")
        return {"FINISHED"}


class KAT_OT_pose_stop(KatRigOp, bpy.types.Operator):
    bl_idname = "kat.pose_stop"
    bl_label = "Stop pose mode"
    bl_description = "Back to object mode. The pose stays as it is; Reset pose puts it back"

    @operator_guard
    def execute(self, context):
        self.rig(context)  # already out of pose mode: operator_guard left it before getting here
        self.report({"INFO"}, "Object mode. The pose is kept")
        return {"FINISHED"}


class KAT_OT_pose_reset(KatRigOp, bpy.types.Operator):
    bl_idname = "kat.pose_reset"
    bl_label = "Reset pose"
    bl_description = "Put every bone back to its rest position"

    @operator_guard
    def execute(self, context):
        rig = self.rig(context)
        for pb in rig.pose.bones:
            pb.matrix_basis = pb.matrix_basis.__class__()
        context.view_layer.update()
        self.report({"INFO"}, "Pose reset")
        return {"FINISHED"}


class KAT_OT_pose_apply(KatRigOp, bpy.types.Operator):
    bl_idname = "kat.pose_apply"
    bl_label = "Save as rest pose"
    bl_description = "Make the current pose the new rest pose. Meshes and shape keys follow, so the model keeps its shape"

    @operator_guard
    def execute(self, context):
        n = apply_pose_as_rest(context, self.rig(context))
        self.report({"INFO"}, f"Applied pose, updated {n} meshes")
        return {"FINISHED"}


class KAT_PT_armature(bpy.types.Panel):
    bl_label = "Pose"
    bl_idname = "KAT_PT_armature"
    bl_order = 1020
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "KAT"
    bl_options = {"DEFAULT_CLOSED"}

    def draw_header(self, context):
        self.layout.label(icon="ARMATURE_DATA")

    def draw(self, context):
        layout = self.layout
        layout.enabled = rig_of(context) is not None
        col = layout.column(align=True)
        col.scale_y = 1.15
        row = col.row(align=True)
        if context.mode == "POSE":
            row.operator("kat.pose_stop", icon="OBJECT_DATAMODE")
        else:
            row.operator("kat.pose_start", icon="POSE_HLT")
        row.operator("kat.pose_reset", text="", icon="LOOP_BACK")
        col.operator("kat.tpose", icon="ARMATURE_DATA")
        col.operator("kat.pose_apply", icon="CHECKBOX_HLT")


classes = (KAT_OT_bones_rename, KAT_OT_bones_check, KAT_OT_bones_prune, KAT_OT_tpose, KAT_OT_pose_apply,
           KAT_OT_pose_start, KAT_OT_pose_stop, KAT_OT_pose_reset, KAT_PT_armature)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
