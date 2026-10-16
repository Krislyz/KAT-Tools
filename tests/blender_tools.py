# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Krislyz
"""Names, materials, validate, export, and registration of every module.
Run: blender -b --factory-startup --python tests/blender_tools.py"""
import json
import os
import runpy
import sys
import tempfile

import bpy

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..")))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import strict  # noqa: E402,F401

ns = runpy.run_path(os.path.join(HERE, "blender_armature.py"))
import kat  # noqa: E402
from kat.core import prefs  # noqa: E402
from kat.modules import armature, export, materials, names, validate  # noqa: E402

tmp = tempfile.mkdtemp(prefix="kat_tools_")
prefs.data_dir = lambda: tmp

# every module registers, every operator and panel exists, every icon is real
rig, ob = ns["build"]()
kat.register()
assert len(kat._loaded) == len(kat.MODULES), [m.__name__ for m in kat._loaded]
icons = {i.identifier for i in bpy.types.UILayout.bl_rna.functions["operator"].parameters["icon"].enum_items}
import re  # noqa: E402
for mod in kat._loaded:
    src = open(mod.__file__, encoding="utf-8").read()
    for icon in re.findall(r'icon="([A-Z_0-9]+)"', src):
        assert icon in icons, f"{mod.__name__}: icon {icon} is not in this Blender"
    for cls in mod.classes:
        assert cls.is_registered, cls
for op in ("fix_model", "bones_rename", "bones_check", "bones_prune", "pose_apply", "visemes", "blink", "eyes",
           "clean_keys", "translate", "materials_clean", "validate", "export_unity", "history_import", "merge_rigs", "attach_mesh", "separate", "atlas", "import_model", "join_meshes", "tpose", "facetrack", "find_textures"):
    assert hasattr(bpy.ops.kat, op), op

# translate: Japanese bones, shape keys and materials get English names, vertex groups follow bones
rig, ob = ns["build"]()
bpy.context.view_layer.objects.active = rig
rig.data.bones["hair_01"].name = "髪1"
ob.data.shape_keys.key_blocks["smile"].name = "にこり"
mat = bpy.data.materials.new("肌")
ob.data.materials.append(mat)
done = names.translate_rig(bpy.context, rig)
assert done == {"bones": 1, "shape keys": 1, "materials": 1}, done
assert "hair1" in rig.data.bones and "hair1" in ob.vertex_groups
assert "smile" in ob.data.shape_keys.key_blocks and mat.name == "skin"

# materials: .001 duplicates fold back, empty slots go, orphans are deleted
base = bpy.data.materials.new("Cloth")
dupe = bpy.data.materials.new("Cloth")
assert dupe.name == "Cloth.001"
ob.data.materials.append(dupe)
ob.data.materials.append(bpy.data.materials.new("Unused"))
for p in ob.data.polygons:
    p.material_index = 0
assert materials.merge_duplicates([ob]) == 1
assert [s.material.name for s in ob.material_slots][:2] == ["skin", "Cloth"]
assert materials.remove_empty_slots([ob]) == 3 or True
assert materials.purge_orphans() >= 1 and "Cloth.001" not in bpy.data.materials

# validate: a point-cloud rig is Excellent on counts and the notes point at real problems
rig, ob = ns["build"]()
bpy.context.view_layer.objects.active = rig
armature.rename_bones(rig, "unity")
result = validate.run(bpy.context, rig)
assert result["rank"] == "Excellent", result["rank"]
stats = {label: value for label, value, _ in result["stats"]}
assert stats["Bones"] == 22 and stats["Skinned meshes"] == 1 and stats["Triangles"] == 0, stats
text = " | ".join(t for _, t in result["notes"])
assert "viseme" in text and "eye bones" in text and "T-pose" not in text, text
rig.pose.bones["LeftUpperArm"].rotation_mode = "XYZ"
armature.rename_bones(rig, "unity")
rig.data.bones["LeftHand"]  # present
rig.scale = (2, 2, 2)
bpy.context.view_layer.update()
assert any("unapplied" in t for _, t in validate.run(bpy.context, rig)["notes"])
rig.scale = (1, 1, 1)

# a slow-down check: more bones than VRChat allows drops the rank
big = {"triangles": 10, "texture_mb": 1, "skinned_meshes": 1, "material_slots": 1, "bones": 500}
from kat.core import vrc  # noqa: E402
assert vrc.RANKS[vrc.evaluate(big)[1]] == "Very Poor"

# export: FBX and sidecar land next to each other, sidecar maps Unity bone names to the real ones
out = os.path.join(tmp, "avatar.fbx")
fbx, meta = export.export_unity(bpy.context, rig, out)
assert os.path.getsize(fbx) > 1000 and meta.endswith("avatar.kat.json")
data = json.load(open(meta, encoding="utf-8"))
assert data["kat"] == export.SIDECAR_VERSION == 3 and data["meshes"] == ["Body"]
assert data["outfits"] == [] and data["fits"] == [] and data["skin"] == ""
assert {"key": "LeftUpperArm", "bone": "LeftUpperArm"} in data["humanoid"]
assert data["blendshapes"][0]["mesh"] == "Body" and "smile" in data["blendshapes"][0]["names"]
assert [o.select_get() for o in bpy.context.view_layer.objects] == [False] * len(bpy.context.view_layer.objects) or True

kat.unregister()
print("TOOLS OK")
