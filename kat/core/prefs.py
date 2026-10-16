# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Krislyz
import os
from types import SimpleNamespace

import bpy
from bpy.props import BoolProperty, FloatProperty, IntProperty

ROOT = __package__.rpartition(".")[0]

D = SimpleNamespace(
    safe_mode=False,
    history_enabled=True,
    history_keep=50,
    history_max_gb=5.0,
    history_max_file_mb=1024,
    import_flat_view=True,
    import_find_textures=True,
    import_unity_sync=True,
)


class KatPreferences(bpy.types.AddonPreferences):
    bl_idname = ROOT

    safe_mode: BoolProperty(name="Safe mode", default=D.safe_mode,
                            description="Turn off every background feature (version history and its watcher)")
    history_enabled: BoolProperty(name="Version history", default=D.history_enabled)
    history_keep: IntProperty(name="Versions per file", default=D.history_keep, min=1, max=1000)
    history_max_gb: FloatProperty(name="Size limit (GB)", default=D.history_max_gb, min=0.1, max=500.0)
    history_max_file_mb: IntProperty(name="Skip files above (MB)", default=D.history_max_file_mb, min=1)
    import_flat_view: BoolProperty(name="Switch to flat texture view", default=D.import_flat_view,
                                   description="After Import model, frame the model and set the viewport to Solid, Flat lighting, Texture colour")
    import_unity_sync: BoolProperty(name="Use Unity's textures", default=D.import_unity_sync,
                                    description="After Import model, if the Unity package left a .kat-unity.json next to the model, use the textures Unity uses")
    import_find_textures: BoolProperty(name="Search for missing textures", default=D.import_find_textures,
                                       description="After Import model, look near the file for textures the model could not find (max 8 seconds)")

    def draw(self, context):
        col = self.layout.column()
        col.prop(self, "safe_mode")
        box = col.box()
        box.label(text="Version history")
        for name in ("history_enabled", "history_keep", "history_max_gb", "history_max_file_mb"):
            box.prop(self, name)
        box = col.box()
        box.label(text="Import model")
        for name in ("import_flat_view", "import_find_textures", "import_unity_sync"):
            box.prop(self, name)


def get():
    """Live prefs, or the defaults when the add-on isn't registered (tests, headless)."""
    try:
        return bpy.context.preferences.addons[ROOT].preferences
    except (KeyError, AttributeError):
        return D


def data_dir():
    try:
        return bpy.utils.extension_path_user(ROOT, create=True)
    except (ValueError, RuntimeError):
        path = os.path.join(bpy.utils.user_resource("CONFIG"), "kat")
        os.makedirs(path, exist_ok=True)
        return path


def register():
    bpy.utils.register_class(KatPreferences)


def unregister():
    bpy.utils.unregister_class(KatPreferences)
