# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Krislyz
"""Import model. Run: blender -b --factory-startup --python tests/blender_importer.py"""
import os
import sys
import tempfile
import time

import bpy

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..")))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import strict  # noqa: E402,F401

from kat.core import formats, prefs, vault  # noqa: E402
from kat.modules import importer  # noqa: E402

tmp = tempfile.mkdtemp(prefix="kat_import_")
prefs.data_dir = lambda: tmp
ctx = bpy.context

# 1. make one small model file in every format the task names
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.mesh.primitive_uv_sphere_add(segments=8, ring_count=4)
bpy.context.object.name = "Ball"
files = {
    "fbx": os.path.join(tmp, "ball.fbx"),
    "glb": os.path.join(tmp, "ball.glb"),
    "obj": os.path.join(tmp, "ball.obj"),
    "usd": os.path.join(tmp, "ball.usd"),
}
bpy.ops.export_scene.fbx(filepath=files["fbx"])
bpy.ops.export_scene.gltf(filepath=files["glb"])
bpy.ops.wm.obj_export(filepath=files["obj"])
bpy.ops.wm.usd_export(filepath=files["usd"])
assert all(os.path.getsize(p) > 100 for p in files.values()), files

assert {".fbx", ".glb", ".gltf", ".obj", ".usd"} <= set(formats.importable_extensions())


def default_scene():
    bpy.ops.wm.read_factory_settings(use_empty=False)
    assert sorted(o.name for o in ctx.scene.objects) == ["Camera", "Cube", "Light"], [o.name for o in ctx.scene.objects]


# 2. every format loads, and the untouched startup scene is cleared away with its data
for kind, path in files.items():
    default_scene()
    assert len(importer.default_scene_objects(ctx.scene)) == 3
    res = importer.import_file(ctx, path)
    names = sorted(o.name for o in ctx.scene.objects)
    new, cleared = res.new, res.cleared
    assert cleared == 3 and new, (kind, names)
    assert not {"Cube", "Camera", "Light"} & set(names), (kind, names)
    assert "Cube" not in bpy.data.meshes and "Camera" not in bpy.data.cameras and "Light" not in bpy.data.lights, kind
    assert ctx.view_layer.objects.active in new or ctx.view_layer.objects.active is not None, kind

# after an import exactly one thing is selected and active: the armature if there is one, so the textures show
selected = [o for o in ctx.scene.objects if o.select_get()]
assert len(selected) == 1 and ctx.view_layer.objects.active == selected[0], selected

# 3. a second model on top of an already loaded one deletes nothing
count = len(ctx.scene.objects)
first = set(ctx.scene.objects)
res = importer.import_file(ctx, files["obj"])
new, cleared = res.new, res.cleared
assert cleared == 0 and first <= set(ctx.scene.objects) and len(ctx.scene.objects) == count + len(new)

# 4. a scene with the default objects plus anything of the user's own is left alone
default_scene()
bpy.data.objects.new("MyModel", bpy.data.meshes.new("MyModel"))
ctx.scene.collection.objects.link(bpy.data.objects["MyModel"])
assert importer.default_scene_objects(ctx.scene) == []
res = importer.import_file(ctx, files["fbx"])
cleared = res.cleared
assert cleared == 0 and {"Cube", "Camera", "Light", "MyModel"} <= {o.name for o in ctx.scene.objects}

# 5. a renamed or edited default cube does not count as untouched either
default_scene()
new, cleared = res.new, res.cleared
ctx.scene.objects["Cube"].name = "Torso"
assert importer.default_scene_objects(ctx.scene) == []
default_scene()
bpy.ops.object.mode_set(mode="EDIT")
bpy.ops.mesh.subdivide()
bpy.ops.object.mode_set(mode="OBJECT")
assert importer.default_scene_objects(ctx.scene) == [], "a subdivided cube is somebody's work"

# 6. an import that fails must not cost the user their scene
default_scene()
real = formats.find_importer
formats.find_importer = lambda ext: (lambda **kw: {"CANCELLED"})
try:
    importer.import_file(ctx, files["fbx"])
    raise AssertionError("an importer that adds nothing must be reported")
except ValueError as err:
    assert "added nothing" in str(err)
formats.find_importer = real
assert sorted(o.name for o in ctx.scene.objects) == ["Camera", "Cube", "Light"], "scene must be untouched after a failed import"

# 7. bad input is refused with a clear message
for bad, needle in ((os.path.join(tmp, "nope.fbx"), "No such file"), (os.path.join(tmp, "notes.txt"), "cannot import")):
    open(os.path.join(tmp, "notes.txt"), "w").close()
    try:
        importer.import_file(ctx, bad)
        raise AssertionError(bad)
    except ValueError as err:
        assert needle in str(err), err

# 8. the import lands in the version history
default_scene()
importer.import_file(ctx, files["glb"])
end = time.time() + 10
while time.time() < end and not [e for e in vault.store().entries() if e["name"] == "ball.glb" and e["op"] == "import"]:
    time.sleep(0.05)
assert [e for e in vault.store().entries() if e["name"] == "ball.glb" and e["op"] == "import"], "import was not recorded"
print("IMPORTER OK")
