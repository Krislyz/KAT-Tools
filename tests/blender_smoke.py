# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Krislyz
"""Registers the add-on in a real Blender and pokes every feature.
Run: blender -b --factory-startup --python tests/blender_smoke.py"""
import os
import sys
import tempfile
import time

import bpy

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import strict  # noqa: E402,F401

import kat  # noqa: E402
from kat.core import prefs, vault, worker  # noqa: E402
from kat.modules import history, textures  # noqa: E402

tmp = tempfile.mkdtemp(prefix="kat_smoke_")
prefs.data_dir = lambda: tmp  # keep test runs out of the real config folder


def wait_for(cond, secs=10):
    end = time.time() + secs
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


kat.register()
assert len(kat._loaded) == len(kat.MODULES), "every module must load"

# icons used by the panels must exist in this Blender
icons = {i.identifier for i in bpy.types.UILayout.bl_rna.functions["operator"].parameters["icon"].enum_items}
for icon in list(history.ICONS.values()) + ["FILEBROWSER", "X", "IMPORT", "FILE_FOLDER", "FILE_TICK", "FILE_BLEND", "ERROR"]:
    assert icon in icons, f"icon {icon} missing"

# history: export a cube, record it, see it, restore it
bpy.ops.mesh.primitive_cube_add()
fbx = os.path.join(tmp, "cube.fbx")
bpy.ops.export_scene.fbx(filepath=fbx)
assert vault.record(fbx, "export")
assert wait_for(lambda: len(vault.store().entries()) == 1), "snapshot never landed"
assert vault.version >= 1
entry = history.entries()[0]
assert entry["op"] == "export" and entry["name"] == "cube.fbx"
out = os.path.join(tmp, "restored.fbx")
bpy.ops.kat.history_restore(entry_id=entry["id"], filepath=out)
assert os.path.getsize(out) == os.path.getsize(fbx)

# history: a version keeps its textures. Opening it saves the scene, clears the project and shows only the version
tex_dir = os.path.join(tmp, "tex")
os.makedirs(tex_dir)
img = bpy.data.images.new("skin_d", 4, 4)
img.filepath_raw = os.path.join(tex_dir, "skin_d.png")
img.file_format = "PNG"
img.save()
mat = bpy.data.materials.new("Skinned")
tree = mat.node_tree
node = tree.nodes.new("ShaderNodeTexImage")
node.image = img
tree.links.new(node.outputs["Color"], next(n for n in tree.nodes if n.type == "BSDF_PRINCIPLED").inputs["Base Color"])
bpy.ops.mesh.primitive_cube_add()
cube = bpy.context.object
cube.data.materials.append(mat)
textured = os.path.join(tmp, "textured.fbx")
bpy.ops.export_scene.fbx(filepath=textured, use_selection=True)
assert vault.record(textured, "export", textures=textures.files_of([cube]))
assert wait_for(lambda: any(e["name"] == "textured.fbx" for e in vault.store().entries()))
version = next(e for e in vault.store().entries() if e["name"] == "textured.fbx")
assert [f["rel"] for f in version["files"]] == ["tex/skin_d.png"], version["files"]
os.remove(img.filepath_raw)  # the original texture is gone for good
sentinel = bpy.data.objects.new("Sentinel", None)
bpy.context.scene.collection.objects.link(sentinel)
assert bpy.ops.kat.history_import(entry_id=version["id"]) == {"FINISHED"}
assert "Sentinel" not in bpy.data.objects, "the old scene is cleared"
assert any(f.startswith("untitled-") for f in os.listdir(history.scenes_dir())), "the old scene is saved first"
assert not bpy.data.filepath, "the open file must not keep the old name, Ctrl+S would overwrite it"
history._open_version(version["id"])  # the timer does this a moment later; background Blender has no timers
assert wait_for(lambda: any(e["op"] == "import" and e["name"] == "textured.fbx" for e in vault.store().entries())), "opening a version is an import too"
meshes = [o for o in bpy.data.objects if o.type == "MESH"]
assert len(meshes) == 1 and len(bpy.data.objects) <= 2, [o.name for o in bpy.data.objects]
images = [i for i in bpy.data.images if i.source == "FILE"]
assert images and not any(textures.is_missing(i) for i in images), "textures must come back, not purple"
assert os.path.commonpath([history.restored_dir(), textures.file_of(images[0])]) == history.restored_dir()
assert history.find_importer(".fbx") is not None and history.find_importer(".xyz") is None

# the watcher: a file an importer or exporter reports as its last path is snapshotted once per change
assert "export_scene.fbx" in history.watched() and "wm.fbx_import" in history.watched()
assert "wm.drop_import_file" not in history.watched()
before = len(vault.store().entries())
real_last = history._last_paths
history._last_paths = lambda idname: [fbx] if idname == "export_scene.fbx" else []
history.scan()
assert wait_for(lambda: len(vault.store().entries()) == before + 1), "watcher missed an export"
history.scan()
time.sleep(0.3)
assert len(vault.store().entries()) == before + 1, "an unchanged file must not be recorded twice"
os.utime(fbx, (time.time() + 5, time.time() + 5))
history.scan()
assert wait_for(lambda: len(vault.store().entries()) == before + 2), "a re-export of the same path must be recorded"
history._last_paths = lambda idname: [os.path.join(tmp, "notes.txt")] if idname == "export_scene.fbx" else []
open(os.path.join(tmp, "notes.txt"), "w").close()
history.scan()
time.sleep(0.3)
assert len(vault.store().entries()) == before + 2, "only model files are recorded"
history._last_paths = real_last
vault.store().prune(1000, 1 << 40)

# safe mode really turns recording off
p = prefs.get()
p.safe_mode = True
assert vault.record(fbx, "export") is False
p.safe_mode = False


bpy.ops.wm.save_as_mainfile(filepath=os.path.join(tmp, "scene.blend"))

# unregister is clean and re-register works
kat.unregister()
assert not bpy.app.timers.is_registered(history._tick)
kat.register()
kat.unregister()
worker.shutdown()
print("SMOKE OK")
