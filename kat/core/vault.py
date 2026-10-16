# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Krislyz
"""The snapshot store version history uses."""
import os

import bpy

from . import prefs, worker
from .store import Store

version = 0  # bumped from the worker thread whenever the index changes; panels compare against it
_store = None


def store():
    global _store
    if _store is None:
        _store = Store(os.path.join(prefs.data_dir(), "history"))
    return _store


def _job(vault, path, op, files, meta, keep, max_bytes):
    global version
    vault.add(path, op, files=files, **meta)
    vault.prune(keep, max_bytes)
    version += 1


def record(path, op, textures=(), **meta):
    """Queue a snapshot of `path` and the texture files it uses. Returns False when skipped. Cheap enough to call
    from a timer: the copying happens on the worker thread."""
    p = prefs.get()
    if p.safe_mode or not p.history_enabled:
        return False
    try:
        if not os.path.isfile(path) or os.path.getsize(path) > p.history_max_file_mb << 20:
            return False
    except OSError:
        return False
    meta.setdefault("blend", os.path.basename(bpy.data.filepath))
    meta.setdefault("blender", bpy.app.version_string)
    limit = p.history_max_file_mb << 20
    files = []
    for file in textures:  # existence is checked here, on the main thread, where Blender's state is safe to read
        try:
            if os.path.isfile(file) and os.path.getsize(file) <= limit:
                files.append(file)
        except OSError:
            continue
    worker.submit(_job, store(), path, op, files, meta, p.history_keep, int(p.history_max_gb * (1 << 30)))
    return True
