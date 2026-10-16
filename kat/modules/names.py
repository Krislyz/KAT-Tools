# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Krislyz
"""Translate Japanese bone, shape key and material names to English, offline."""
import bpy
from bpy.props import BoolProperty

from ..core import jp
from ..core.safe import operator_guard
from . import armature


def translate_collection(items):
    """Rename every item that has Japanese in its name. Returns how many changed."""
    count = 0
    for item in list(items):
        if jp.has_japanese(item.name):
            new = jp.translate(item.name)
            if new != item.name:
                item.name = new
                count += 1
    return count


def translate_rig(context, rig, bones=True, shape_keys=True, materials=True):
    done = {"bones": 0, "shape keys": 0, "materials": 0}
    if bones:
        done["bones"] = translate_collection(rig.data.bones)  # vertex groups follow their bones
    for ob in armature.meshes_of(rig):
        if shape_keys and ob.data.shape_keys:
            done["shape keys"] += translate_collection(ob.data.shape_keys.key_blocks)
        if materials:
            for slot in ob.material_slots:
                if slot.material:
                    done["materials"] += translate_collection([slot.material])
    return done


class KAT_OT_translate(bpy.types.Operator):
    bl_idname = "kat.translate"
    bl_label = "Translate names"
    bl_description = "Turn Japanese bone, shape key and material names into English (offline dictionary)"
    bl_options = {"REGISTER", "UNDO"}
    bones: BoolProperty(name="Bones", default=True)
    shape_keys: BoolProperty(name="Shape keys", default=True)
    materials: BoolProperty(name="Materials", default=True)

    @operator_guard
    def execute(self, context):
        rig = armature.rig_of(context)
        if rig is None:
            raise ValueError("Select an armature, or a mesh rigged to one")
        done = translate_rig(context, rig, self.bones, self.shape_keys, self.materials)
        self.report({"INFO"}, ", ".join(f"{n} {what}" for what, n in done.items()) + " renamed")
        return {"FINISHED"}


classes = (KAT_OT_translate,)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
