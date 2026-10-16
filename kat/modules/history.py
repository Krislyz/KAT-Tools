# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Krislyz
"""Version history for every model file imported or exported while Blender is open."""
import contextlib
import functools
import logging
import os
import shutil
import time

import bpy
from bpy.props import StringProperty

from ..core import prefs, vault
from ..core.formats import find_importer
from ..core.safe import guarded, operator_guard
from . import importer, textures

log = logging.getLogger("kat")
RESTORED_DAYS = 14  # restored versions keep their files on disk (the model's textures point there), for this long

EXTS = {".fbx", ".glb", ".gltf", ".obj", ".vrm", ".pmx", ".pmd", ".dae", ".usd", ".usda", ".usdc",
        ".usdz", ".abc", ".ply", ".stl", ".vrma"}

ICONS = {"import": "IMPORT", "export": "EXPORT", "backup": "FILE_BACKUP"}
WATCH_EVERY = 2.0

_seen = set()
_objects = set()
_cache = (-1, [])
_drawn_version = 0
status = {"text": ""}  # the last thing a version import did, shown in the panel


def human_size(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def ago(ts):
    s = max(0, int(time.time() - ts))
    if s < 90:
        return f"{s}s ago"
    if s < 5400:
        return f"{s // 60}m ago"
    if s < 172800:
        return f"{s // 3600}h ago"
    return f"{s // 86400}d ago"


def entries():
    global _cache
    if _cache[0] != vault.version:
        _cache = (vault.version, sorted(vault.store().entries(), key=lambda e: e["ts"], reverse=True))
    return _cache[1]


# Operator namespaces that hold importers and exporters. Blender's own list of finished operators
# (window_manager.operators) skips file operators, but it does remember the last file each one used.
NAMESPACES = ("wm", "import_scene", "export_scene", "import_mesh", "export_mesh", "import_anim", "export_anim",
              "mmd_tools")
IGNORED = {"drop_import_file", "collection_export_all", "collection_exporter_add", "collection_exporter_remove"}
_known = {"at": 0.0, "ids": []}


def _kind(name):
    name = name.lower()
    if "export" in name:
        return "export"
    return "import" if "import" in name else None


def watched():
    """Importer and exporter operator ids this Blender has right now. Add-ons can add more, so refresh now and then."""
    if time.monotonic() - _known["at"] > 30:
        ids = []
        for space in NAMESPACES:
            ns = getattr(bpy.ops, space, None)
            if ns is not None:
                ids += [i for i in (f"{space}.{n}" for n in dir(ns)) if _kind(i) and i.split(".")[1] not in IGNORED]
        _known.update(at=time.monotonic(), ids=ids)
    return _known["ids"]


def _last_paths(idname):
    """Files the operator last worked on, as Blender remembers them."""
    try:
        props = bpy.context.window_manager.operator_properties_last(idname)
    except (KeyError, TypeError, AttributeError, RuntimeError):
        return []
    if props is None:
        return []
    path = getattr(props, "filepath", "")
    if path:
        return [path]
    folder = getattr(props, "directory", "")
    return [os.path.join(folder, f.name) for f in getattr(props, "files", ())] if folder else []


def _textures(kind, fresh):
    """The texture files a model from File > Import/Export used: those of the objects that just arrived (import),
    or of every mesh (export, where Blender does not say what was selected)."""
    objs = [bpy.data.objects[n] for n in fresh if n in bpy.data.objects] if kind == "import" else list(bpy.data.objects)
    return textures.files_of([o for o in objs if o.type == "MESH"])


@guarded
def scan(seed_only=False):
    """Snapshot model files that import/export operators finished with since the last scan."""
    global _objects
    try:
        now = set(bpy.data.objects.keys())
    except AttributeError:  # while the add-on registers, Blender may not let us read its data yet
        now = set()
    fresh, _objects = now - _objects, now
    for idname in watched():
        kind = _kind(idname)
        for path in _last_paths(idname):
            try:
                key = (idname, path, os.path.getmtime(path))
            except OSError:
                continue
            if key in _seen:
                continue
            _seen.add(key)
            if not seed_only and os.path.splitext(path)[1].lower() in EXTS and not path.startswith(scratch_dir()):
                vault.record(path, kind, textures=_textures(kind, fresh))


def scratch_dir():
    return os.path.join(prefs.data_dir(), "scratch")


@guarded
def _refresh():
    global _drawn_version
    scan()
    if _drawn_version != vault.version:
        _drawn_version = vault.version
        for win in bpy.context.window_manager.windows:
            if win.screen is None:  # windows are half-built while a file loads
                continue
            for area in win.screen.areas:
                if area.type == "VIEW_3D":
                    area.tag_redraw()


def _tick():
    _refresh()  # a timer that raises is unregistered for the session, so nothing may escape from here
    return WATCH_EVERY


class KAT_OT_history_restore(bpy.types.Operator):
    bl_idname = "kat.history_restore"
    bl_label = "Save a copy of this version"
    bl_description = "Write this snapshot back out as a file"
    entry_id: StringProperty(options={"HIDDEN"})
    filepath: StringProperty(subtype="FILE_PATH")

    def invoke(self, context, event):
        entry = vault.store().get(self.entry_id)
        if entry is None:
            self.report({"WARNING"}, "That snapshot is gone")
            return {"CANCELLED"}
        self.filepath = entry["name"]
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    @operator_guard
    def execute(self, context):
        vault.store().restore(self.entry_id, self.filepath)
        self.report({"INFO"}, f"Saved {os.path.basename(self.filepath)}")
        return {"FINISHED"}


def restored_dir():
    return os.path.join(prefs.data_dir(), "restored")


def scenes_dir():
    return os.path.join(prefs.data_dir(), "scenes")


def stash_scene():
    """Save what is open before it is cleared. Returns where it went, or "" when there was nothing worth saving."""
    if bpy.data.filepath:
        if bpy.data.is_dirty:
            bpy.ops.wm.save_mainfile()
        return bpy.data.filepath
    scene = bpy.context.scene
    if not scene.objects or importer.default_scene_objects(scene):
        return ""
    os.makedirs(scenes_dir(), exist_ok=True)
    path = os.path.join(scenes_dir(), time.strftime("untitled-%Y%m%d-%H%M%S.blend"))
    bpy.ops.wm.save_as_mainfile(filepath=path, copy=True)  # a copy: the open file does not get renamed
    return path


def _open_version(entry_id):
    """Runs a moment after the scene was cleared, when Blender's data is settled again."""
    try:
        model = vault.store().restore_all(entry_id, os.path.join(restored_dir(), entry_id))
        result = importer.import_file(bpy.context, model)
        found = textures.summary(result.textures) if result.textures else ""
        status["text"] = f"Opened {os.path.basename(model)}" + (f", {found}" if found else "")
    except Exception as err:  # a timer that raises is gone for the session
        log.exception("kat: could not open the version")
        status["text"] = f"Could not open the version: {err}"
    return None


def clean_restored():
    """Throw away restored versions nobody has opened for a while."""
    root = restored_dir()
    if not os.path.isdir(root):
        return
    limit = time.time() - RESTORED_DAYS * 86400
    for name in os.listdir(root):
        folder = os.path.join(root, name)
        with contextlib.suppress(OSError):
            if os.path.getmtime(folder) < limit:
                shutil.rmtree(folder, ignore_errors=True)


class KAT_OT_history_import(bpy.types.Operator):
    bl_idname = "kat.history_import"
    bl_label = "Open this version"
    bl_description = ("Save the open scene, clear the project, and open only this version, with its textures, as a "
                      "new scene")
    entry_id: StringProperty(options={"HIDDEN"})

    @operator_guard
    def execute(self, context):
        entry = vault.store().get(self.entry_id)
        if entry is None:
            raise ValueError("That snapshot is gone")
        ext = os.path.splitext(entry["name"])[1].lower()
        if find_importer(ext) is None:
            raise ValueError(f"This Blender has no importer for {ext} files, use Save a copy instead")
        saved = stash_scene()
        bpy.ops.wm.read_homefile(use_empty=True, load_ui=False)  # also forgets the file name, so Ctrl+S cannot overwrite it
        bpy.app.timers.register(functools.partial(_open_version, self.entry_id), first_interval=0.2)
        status["text"] = "Opening..." + (f" The previous scene is saved: {saved}" if saved else "")
        return {"FINISHED"}


class KAT_OT_history_forget(bpy.types.Operator):
    bl_idname = "kat.history_forget"
    bl_label = "Delete this snapshot"
    entry_id: StringProperty(options={"HIDDEN"})

    def execute(self, context):
        vault.store().forget(self.entry_id)
        vault.version += 1
        return {"FINISHED"}


class KAT_OT_history_open_folder(bpy.types.Operator):
    bl_idname = "kat.history_open_folder"
    bl_label = "Open history folder"

    def execute(self, context):
        bpy.ops.wm.path_open(filepath=vault.store().root)
        return {"FINISHED"}


class KAT_PT_history(bpy.types.Panel):
    bl_label = "Version history"
    bl_idname = "KAT_PT_history"
    bl_order = 1090
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "KAT"
    bl_options = {"DEFAULT_CLOSED"}

    def draw_header(self, context):
        self.layout.label(icon="RECOVER_LAST")

    def draw(self, context):
        col = self.layout.column(align=True)
        p = prefs.get()
        if p.safe_mode or not p.history_enabled:
            col.label(text="Off (Preferences > Add-ons > KAT)")
            return
        if status["text"]:
            col.label(text=status["text"], icon="INFO")
        rows = entries()[:12]
        if not rows:
            col.label(text="Nothing yet. Import or export a model.")
        for e in rows:
            box = col.box()
            box.label(text=e["name"], icon=ICONS.get(e["op"], "FILE"))
            row = box.row(align=True)
            size = e["size"] + sum(f["size"] for f in e.get("files", ()))
            info = row.row()
            info.enabled = False
            info.label(text=f"{e['op']}, {ago(e['ts'])}, {human_size(size)}")
            for idname, icon in (("kat.history_import", "IMPORT"), ("kat.history_restore", "FILEBROWSER"),
                                 ("kat.history_forget", "X")):
                row.operator(idname, text="", icon=icon).entry_id = e["id"]
        col.separator()
        col.operator("kat.history_open_folder", icon="FILE_FOLDER")


classes = (KAT_OT_history_restore, KAT_OT_history_import, KAT_OT_history_forget,
           KAT_OT_history_open_folder, KAT_PT_history)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    scan(seed_only=True)  # whatever ran before we loaded is not news
    guarded(clean_restored)()
    if not bpy.app.timers.is_registered(_tick):
        bpy.app.timers.register(_tick, first_interval=WATCH_EVERY, persistent=True)


def unregister():
    if bpy.app.timers.is_registered(_tick):
        bpy.app.timers.unregister(_tick)
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    _seen.clear()
