# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Krislyz
"""Which Blender operator imports which kind of model file."""
import bpy

# Importer operators per extension, newest first. Blender renames these now and then, so we look them up
# at call time. Add-on importers (VRM, MMD, Source) only count while their add-on is enabled.
EXTENSIONS = {
    ".fbx": [("wm", "fbx_import"), ("import_scene", "fbx")],
    ".glb": [("import_scene", "gltf")],
    ".gltf": [("import_scene", "gltf")],
    ".obj": [("wm", "obj_import")],
    ".usd": [("wm", "usd_import")],
    ".usda": [("wm", "usd_import")],
    ".usdc": [("wm", "usd_import")],
    ".usdz": [("wm", "usd_import")],
    ".abc": [("wm", "alembic_import")],
    ".ply": [("wm", "ply_import")],
    ".stl": [("wm", "stl_import")],
    ".vrm": [("import_scene", "vrm")],
    ".pmx": [("mmd_tools", "import_model")],
    ".pmd": [("mmd_tools", "import_model")],
    ".smd": [("import_scene", "smd")],
}


def find_importer(ext):
    """The operator that imports `ext` files in this Blender, or None."""
    for module, name in EXTENSIONS.get(ext.lower(), ()):
        space = getattr(bpy.ops, module, None)
        if space is not None and hasattr(space, name):
            return getattr(space, name)
    return None


def importable_extensions():
    return [ext for ext in EXTENSIONS if find_importer(ext) is not None]
