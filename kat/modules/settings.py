# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Krislyz
"""The add-on's options, right in the sidebar so nobody has to dig through Preferences."""
import bpy

from ..core import prefs


class KAT_PT_settings(bpy.types.Panel):
    bl_label = "Settings"
    bl_idname = "KAT_PT_settings"
    bl_order = 1095
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "KAT"
    bl_options = {"DEFAULT_CLOSED"}

    def draw_header(self, context):
        self.layout.label(icon="PREFERENCES")

    def draw(self, context):
        p = prefs.get()
        if not isinstance(p, bpy.types.AddonPreferences):
            self.layout.label(text="Preferences are not available yet")
            return
        col = self.layout.column()
        col.prop(p, "safe_mode")
        box = col.box()
        box.label(text="History")
        box.prop(p, "history_enabled")
        box.prop(p, "history_keep")
        box.prop(p, "history_max_gb")
        box = col.box()
        box.label(text="Import model")
        box.prop(p, "import_flat_view")
        box.prop(p, "import_find_textures")
        box.prop(p, "import_unity_sync")


classes = (KAT_PT_settings,)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
