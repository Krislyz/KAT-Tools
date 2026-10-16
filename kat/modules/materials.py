# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Krislyz
"""Material clean-up: fold Blender's .001 duplicates back into the original, drop empty slots."""
import re

import bpy

from ..core.safe import operator_guard
from . import armature

COPY = re.compile(r"^(.*)\.\d{3}$")


def merge_duplicates(objs):
    """Point slots using 'Name.001' at 'Name' when that exists. Returns slots changed."""
    changed = 0
    for ob in objs:
        for slot in ob.material_slots:
            mat = slot.material
            m = COPY.match(mat.name) if mat else None
            base = bpy.data.materials.get(m.group(1)) if m else None
            if base is not None and base is not mat and mat.library is None:
                slot.material = base
                changed += 1
    return changed


def remove_empty_slots(objs):
    removed = 0
    for ob in objs:
        used = {p.material_index for p in ob.data.polygons}
        for i in reversed(range(len(ob.material_slots))):
            if i not in used:
                ob.active_material_index = i
                with bpy.context.temp_override(object=ob, active_object=ob):
                    bpy.ops.object.material_slot_remove()
                removed += 1
    return removed


def purge_orphans():
    doomed = [m for m in bpy.data.materials if m.users == 0 and m.library is None]
    for m in doomed:
        bpy.data.materials.remove(m)
    return len(doomed)


class KAT_OT_materials_clean(bpy.types.Operator):
    bl_idname = "kat.materials_clean"
    bl_label = "Clean materials"
    bl_description = "Merge .001 duplicate materials, remove unused slots, delete orphaned materials"
    bl_options = {"REGISTER", "UNDO"}

    @operator_guard
    def execute(self, context):
        rig = armature.rig_of(context)
        objs = armature.meshes_of(rig) if rig else [o for o in context.selected_objects if o.type == "MESH"]
        if not objs:
            raise ValueError("Select a rigged mesh or an armature")
        merged = merge_duplicates(objs)
        slots = remove_empty_slots(objs)
        orphans = purge_orphans()
        self.report({"INFO"}, f"Merged {merged} slots, removed {slots} empty slots, deleted {orphans} orphans")
        return {"FINISHED"}


classes = (KAT_OT_materials_clean,)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
