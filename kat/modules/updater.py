# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Krislyz
"""Looks for a newer KAT release each time Blender starts, and installs it when asked."""
import logging
import os

import bpy

from ..core import prefs, update, worker
from . import ui

log = logging.getLogger("kat")
state = dict(status="", info=None, error="")  # status: "", checking, current, available, installing, installed, offline, error


def _redraw():
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            area.tag_redraw()
    return None


def _set(**kw):
    state.update(kw)
    bpy.app.timers.register(_redraw, first_interval=0.1)  # the job runs on a thread: redraw from the main one


def _check_job(current):
    try:
        info = update.check(current)
    except Exception as err:  # offline, rate limited, GitHub down: not worth more than a line in the panel
        log.info("kat: update check failed: %r", err)
        _set(status="error", error="Could not reach GitHub")
        return
    _set(status="available" if info else "current", info=info)


def check():
    if state["status"] in ("checking", "installing"):
        return
    if not bpy.app.online_access:
        _set(status="offline")
        return
    _set(status="checking", error="")
    worker.submit(_check_job, ui.version())


def _install_job(info, folder):
    try:
        data = update.download(info)
        path = os.path.join(folder, info["name"])
        with open(path, "wb") as f:
            f.write(data)
    except Exception as err:
        log.info("kat: update download failed: %r", err)
        _set(status="error", error="Download failed")
        return
    bpy.app.timers.register(lambda: _install(path), first_interval=0.1)  # bpy only from the main thread


def _install(path):
    try:
        bpy.ops.extensions.package_install_files(filepath=path, repo="user_default", enable_on_install=True, overwrite=True)
        _set(status="installed")
    except Exception as err:
        log.info("kat: update install failed: %r", err)
        _set(status="error", error="Install failed. Install the zip from the releases page instead.")
    return None


def _startup():
    if prefs.get().check_updates and not bpy.app.background:
        check()
    return None


class KAT_OT_check_update(bpy.types.Operator):
    bl_idname = "kat.check_update"
    bl_label = "Check for updates"
    bl_description = "Ask GitHub whether a newer KAT is out"

    def execute(self, context):
        check()
        return {"FINISHED"}


class KAT_OT_install_update(bpy.types.Operator):
    bl_idname = "kat.install_update"
    bl_label = "Update now"
    bl_description = "Download the new version from GitHub and install it. Restart Blender afterwards"

    @classmethod
    def poll(cls, context):
        return state["status"] == "available" and state["info"] is not None

    def execute(self, context):
        folder = os.path.join(prefs.data_dir(), "update")
        os.makedirs(folder, exist_ok=True)
        _set(status="installing")
        worker.submit(_install_job, state["info"], folder)
        return {"FINISHED"}


class KAT_PT_updates(bpy.types.Panel):
    bl_label = "Updates"
    bl_idname = "KAT_PT_updates"
    bl_order = 1001
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "KAT"  # open by default, like Quick access and Credits; every other panel starts closed

    def draw_header(self, context):
        self.layout.label(icon="URL")

    def draw(self, context):
        draw(self.layout)


def draw(box):
    s, info = state["status"], state["info"]
    if s == "available":
        box.label(text=f"KAT {info['version']} is out (you have {ui.version()})", icon="INFO")
        row = box.row(align=True)
        row.operator("kat.install_update", icon="IMPORT")
        row.operator("wm.url_open", text="What's new", icon="HELP").url = info["page"]
    elif s == "installed":
        box.label(text="Installed. Restart Blender to use it.", icon="CHECKMARK")
    else:
        text = dict(checking="Checking...", installing="Downloading...", current="You have the latest version",
                    offline="Turn on Allow Online Access in Preferences > System").get(s, state["error"])
        if text:
            box.label(text=text, icon="ERROR" if s in ("error", "offline") else "INFO")
        box.operator("kat.check_update", icon="FILE_REFRESH")
    p = prefs.get()
    if isinstance(p, bpy.types.AddonPreferences):
        box.prop(p, "check_updates")


def draw_notice(layout):
    """One line in the main panel when there is something to install."""
    if state["status"] == "available":
        layout.operator("kat.install_update", text=f"Update KAT to {state['info']['version']}", icon="IMPORT")


classes = (KAT_OT_check_update, KAT_OT_install_update, KAT_PT_updates)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.app.timers.register(_startup, first_interval=5.0)


def unregister():
    if bpy.app.timers.is_registered(_startup):
        bpy.app.timers.unregister(_startup)
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
