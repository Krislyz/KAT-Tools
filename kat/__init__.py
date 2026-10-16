# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Krislyz
import atexit
import importlib
import logging

import bpy
from bpy.app.handlers import persistent

from .core import prefs, worker
from .core.safe import guarded

log = logging.getLogger("kat")

# Each module exposes register()/unregister(); one failing module never blocks the rest.
# Panel order in the sidebar follows this list.
MODULES = ["options", "ui", "importer", "textures", "unitysync", "mesh", "armature", "optimize", "texopt", "reduce", "world", "outfits", "refit", "face", "custom",
           "names", "materials", "atlas", "validate", "export", "history", "settings", "updater", "credits"]
_loaded = []


@guarded
def _load(name):
    mod = importlib.import_module(f".modules.{name}", __package__)
    mod.register()
    _loaded.append(mod)


@persistent
def _on_exit(*_):
    worker.shutdown()  # queued snapshot jobs are dropped, so quitting never waits on them


def _exit_handlers():
    return getattr(bpy.app.handlers, "exit_pre", None)  # not in every Blender 5 release


def register():
    prefs.register()
    handlers = _exit_handlers()
    if handlers is not None and _on_exit not in handlers:
        handlers.append(_on_exit)
    elif handlers is None:
        atexit.register(worker.shutdown)
    for name in MODULES:
        _load(name)


def unregister():
    handlers = _exit_handlers()
    if handlers is not None and _on_exit in handlers:
        handlers.remove(_on_exit)
    atexit.unregister(worker.shutdown)
    for mod in reversed(_loaded):
        guarded(mod.unregister)()
    _loaded.clear()
    worker.shutdown()
    prefs.unregister()
