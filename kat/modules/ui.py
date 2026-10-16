# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Krislyz
"""The top of the KAT sidebar tab (import, export, what state the rig is in, the three main steps) and the credits."""
import os

import bpy

from ..core import humanoid
from . import armature, uiutil

REPO = "https://github.com/Krislyz/KAT-Tools"
SITE = "https://krislyz.com/kat/"
_cache = {"key": None, "value": None}
_version = []


def has_rig(context):
    return armature.rig_of(context) is not None


def summary(rig):
    """What the status block shows. Cached: panels redraw constantly and name matching is not free."""
    key = (rig.name, tuple(b.name for b in rig.data.bones))
    if _cache["key"] != key:
        best, rest = armature.cached_map(rig)
        missing = armature.missing_required(best)
        _cache.update(key=key, value=dict(
            humanoid=len(best), required=len(humanoid.REQUIRED), missing=missing, other=len(rest)))
    return _cache["value"]


def version():
    """The add-on's version, read from its manifest."""
    if not _version:
        try:
            import tomllib
            path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "blender_manifest.toml")
            with open(path, "rb") as f:
                _version.append(tomllib.load(f)["version"])
        except Exception:  # a missing version must never break a panel
            _version.append("")
    return _version[0]


class KAT_PT_main(bpy.types.Panel):
    bl_label = "Quick access"
    bl_idname = "KAT_PT_main"
    bl_order = 1000
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "KAT"
    # No HIDE_HEADER: a header-less panel makes Blender move the whole KAT tab to the front of the tab list.

    def draw_header(self, context):
        self.layout.label(icon="OUTLINER_OB_ARMATURE")

    def draw(self, context):
        layout = self.layout
        rig = armature.rig_of(context)
        row = layout.row(align=True)
        row.scale_y = 1.5
        row.operator("kat.import_model", text="Import", icon="IMPORT")
        sub = row.row(align=True)
        sub.enabled = rig is not None
        sub.operator("kat.export_unity", text="Export", icon="EXPORT")
        if rig is None:
            uiutil.paragraph(layout, context, "Select a rigged model to see its state here.", "INFO")
            return
        self.draw_status(layout, rig)

    @staticmethod
    def draw_status(layout, rig):
        info = summary(rig)
        box = layout.box()
        box.label(text=rig.name, icon="ARMATURE_DATA")
        grid = box.grid_flow(row_major=True, columns=2, even_columns=True, even_rows=True, align=True)
        have = info["required"] - len(info["missing"])
        cell = grid.row()
        cell.alert = bool(info["missing"])
        cell.label(text=f"{have}/{info['required']} bones", icon="BONE_DATA")
        grid.label(text=f"{len(armature.meshes_of(rig))} meshes", icon="MESH_DATA")
        if info["missing"]:
            cell = box.row()
            cell.alert = True
            cell.label(text="Missing: " + ", ".join(info["missing"][:3]) + ("..." if len(info["missing"]) > 3 else ""),
                       icon="ERROR")
        left_out = armature.unreachable_meshes(rig)
        if left_out:
            box.label(text=f"{len(left_out)} hidden mesh(es) skipped", icon="HIDE_ON")


classes = (KAT_PT_main,)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
