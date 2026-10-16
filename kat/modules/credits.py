# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Krislyz
"""The Credits panel. Its own module so it registers last and sits at the bottom of the tab."""
import bpy

from . import ui, uiutil


class KAT_PT_credits(bpy.types.Panel):
    bl_label = "Credits"
    bl_idname = "KAT_PT_credits"
    bl_order = 1099
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "KAT"

    def draw_header(self, context):
        self.layout.label(icon="FUND")

    def draw(self, context):
        layout = self.layout
        box = layout.box()
        box.label(text=f"KAT Blender Plugin {ui.version()}".strip(), icon="OUTLINER_OB_ARMATURE")
        col = box.column(align=True)
        col.label(text="& Unity Utilities")
        col.label(text="Made by Krislyz")
        uiutil.paragraph(box, context, "GPL-3.0-only. Free to use, change and share, and what you share stays open.")
        uiutil.paragraph(box, context, "Made to make your avatar workflow easier.")
        col = layout.column(align=True)
        col.operator("wm.url_open", text="Website", icon="WORLD").url = ui.SITE
        col.operator("wm.url_open", text="Source code", icon="URL").url = ui.REPO
        col.operator("wm.url_open", text="Report a problem", icon="QUESTION").url = ui.REPO + "/issues"
        col.operator("wm.url_open", text="Releases and Unity package", icon="PACKAGE").url = ui.REPO + "/releases"


classes = (KAT_PT_credits,)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
