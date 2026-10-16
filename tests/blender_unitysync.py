# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Krislyz
"""Texture sync with Unity. Run: blender -b --factory-startup --python tests/blender_unitysync.py"""
import json
import os
import sys
import tempfile

import bpy
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..")))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import strict  # noqa: E402,F401

from kat.modules import export, textures, unitysync  # noqa: E402

bpy.ops.wm.read_factory_settings(use_empty=True)
project = tempfile.mkdtemp(prefix="kat_unity_")
assets = os.path.join(project, "Assets", "Avatar")
os.makedirs(os.path.join(assets, "Textures"))


def write_png(path, rgba):
    img = bpy.data.images.new(os.path.basename(path), 4, 4, alpha=True)
    img.pixels.foreach_set(np.tile(np.array(rgba, dtype=np.float32), 16))
    img.filepath_raw = path
    img.file_format = "PNG"
    img.save()
    bpy.data.images.remove(img)


for name in ("Skin.png", "SkinNormal.png", "Glow.png", "Cloth.png"):
    write_png(os.path.join(assets, "Textures", name), (0.5, 0.5, 1, 1))

ob = bpy.data.objects.new("Body", bpy.data.meshes.new("Body"))
bpy.context.scene.collection.objects.link(ob)
skin, cloth = bpy.data.materials.new("Skin"), bpy.data.materials.new("Cloth_FBX_name")
ob.data.materials.append(skin)
ob.data.materials.append(cloth)
model = os.path.join(assets, "Avatar.fbx")

data = {
    "kat": 1,
    "project": project,
    "renderers": [
        {"name": "Body", "slots": [
            {"material": "Skin", "textures": [
                {"role": "baseColor", "property": "_MainTex", "path": "Assets/Avatar/Textures/Skin.png"},
                {"role": "normal", "property": "_BumpMap", "path": "Assets/Avatar/Textures/SkinNormal.png"},
                {"role": "emission", "property": "_EmissionMap", "path": "Assets/Avatar/Textures/Glow.png"},
                {"role": "other", "property": "_Main2ndTex", "path": "Assets/Avatar/Textures/Skin.png"},
            ]},
            {"material": "UnityNamedItDifferently", "textures": [
                {"role": "baseColor", "property": "_MainTex", "path": "Assets/Avatar/Textures/Cloth.png"},
                {"role": "baseColor", "property": "_MainTex", "path": "Assets/Avatar/Textures/Gone.png"},
            ]},
        ]},
        {"name": "NoSuchMesh", "slots": [{"material": "x", "textures": []}]},
    ],
}

result = unitysync.apply([ob], data, model)
assert result.applied == 4 and result.missing == ["Assets/Avatar/Textures/Gone.png"], result
node = textures.principled(skin)
tree = skin.node_tree
base = textures.upstream_image(node.inputs["Base Color"])
assert os.path.basename(base.filepath) == "Skin.png" and base.colorspace_settings.name == "sRGB"
normal = textures.upstream_image(node.inputs["Normal"])
assert os.path.basename(normal.filepath) == "SkinNormal.png" and normal.colorspace_settings.name == "Non-Color"
assert node.inputs["Normal"].links[0].from_node.type == "NORMAL_MAP", "a normal map needs a Normal Map node"
glow = textures.upstream_image(node.inputs["Emission Color"])
assert os.path.basename(glow.filepath) == "Glow.png" and node.inputs["Emission Strength"].default_value == 1.0
assert os.path.basename(textures.upstream_image(textures.principled(cloth).inputs["Base Color"]).filepath) == "Cloth.png", \
    "matched by slot number, not by name"
assert tree.nodes.active.type == "TEX_IMAGE", "the Texture view needs an active image node"

# the texture's alpha counts only where Unity's material uses it
assert base.alpha_mode == "STRAIGHT", "no mode in the file: the image is left alone"
data_alpha = dict(data, renderers=[{"name": "Body", "slots": [
    {"material": "Skin", "mode": "opaque", "textures": data["renderers"][0]["slots"][0]["textures"][:1]},
    {"material": "Cloth", "mode": "blend", "textures": [data["renderers"][0]["slots"][1]["textures"][0]]}]}])
unitysync.apply([ob], data_alpha, model)
assert base.alpha_mode == "NONE", "opaque material: the alpha channel is ignored"
cloth_node = textures.principled(cloth)
assert cloth_node.inputs["Alpha"].is_linked, "a transparent material shows the texture's alpha"

# slot numbers that no longer line up (meshes joined, materials atlased) are not guessed at
before = os.path.basename(textures.upstream_image(textures.principled(cloth).inputs["Base Color"]).filepath)
three = dict(data, renderers=[{"name": "Body", "slots": data["renderers"][0]["slots"] + [{"material": "Extra", "textures": [
    {"role": "baseColor", "property": "_MainTex", "path": "Assets/Avatar/Textures/Skin.png"}]}]}])
assert unitysync.apply([ob], three, model).applied == 0, "three Unity slots, two Blender ones: nothing is applied"
cloth["kat_atlas"] = True
assert unitysync.apply([ob], data, model).applied == 3, "a KAT atlas material is left alone"
del cloth["kat_atlas"]

# a second sync reuses the image nodes instead of piling up new ones
count = len(textures.image_nodes(skin))
unitysync.apply([ob], data, model)
assert len(textures.image_nodes(skin)) == count == 3

# no "project" in the file: the Assets folder above the model gives it away
data2 = dict(data, project="")
data2["renderers"] = [data["renderers"][0]]
assert unitysync.project_root(data2, model) == project
assert unitysync.project_root(data2, os.path.join(project, "elsewhere", "m.fbx")) == ""

# the file next to the model is found and read; junk is refused
sidecar = unitysync.sync_file_for(model)
assert sidecar.endswith("Avatar.kat-unity.json")
assert unitysync.sync_for_import(model, [ob]) is unitysync.NOTHING, "no file, nothing happens"
with open(sidecar, "w", encoding="utf-8") as f:
    json.dump(data, f)
done = unitysync.sync_for_import(model, [ob])
assert done.applied == 4 and done.file == sidecar and "4 linked" in unitysync.summary(done)
open(sidecar, "w").write("not json")
assert unitysync.load(sidecar) is None

# export side: the sidecar lists each material's textures relative to the FBX, and skips what has no file
fbx_dir = os.path.join(assets)
mats = bpy.data.materials.new("Exported")
tree = mats.node_tree
pr = textures.principled(mats)
for role, socket, fname, nm in (("c", "Base Color", "Skin.png", False), ("n", "Normal", "SkinNormal.png", True)):
    tex = tree.nodes.new("ShaderNodeTexImage")
    tex.image = bpy.data.images.load(os.path.join(assets, "Textures", fname))
    if nm:
        node_map = tree.nodes.new("ShaderNodeNormalMap")
        tree.links.new(tex.outputs["Color"], node_map.inputs["Color"])
        tree.links.new(node_map.outputs["Normal"], pr.inputs[socket])
    else:
        tree.links.new(tex.outputs["Color"], pr.inputs[socket])
emit = tree.nodes.new("ShaderNodeTexImage")
emit.image = bpy.data.images.new("generated", 4, 4)  # lives only in memory: nothing for Unity to point at
tree.links.new(emit.outputs["Color"], pr.inputs["Emission Color"])
holder = bpy.data.objects.new("Holder", bpy.data.meshes.new("Holder"))
bpy.context.scene.collection.objects.link(holder)
holder.data.materials.append(mats)
listed = export.material_textures([holder], fbx_dir)
assert listed == [{"name": "Exported", "textures": [{"role": "baseColor", "path": "Textures/Skin.png"},
                                                    {"role": "normal", "path": "Textures/SkinNormal.png"}]}], listed

# an atlas packed into a .blend that was never saved still reaches Unity: written as a PNG next to the FBX
import tempfile  # noqa: E402
out_dir = tempfile.mkdtemp(prefix="kat_export_")
pack_mat = bpy.data.materials.new("Packed")
pack_img = bpy.data.images.new("Rig_atlas_base", 8, 8, alpha=True)
pack_img.pixels.foreach_set([0.5] * (8 * 8 * 4))
pack_img.pack()
pt = pack_mat.node_tree.nodes.new("ShaderNodeTexImage")
pt.image = pack_img
pack_mat.node_tree.links.new(pt.outputs["Color"], textures.principled(pack_mat).inputs["Base Color"])
packed_holder = bpy.data.objects.new("PackedHolder", bpy.data.meshes.new("PackedHolder"))
bpy.context.scene.collection.objects.link(packed_holder)
packed_holder.data.materials.append(pack_mat)
saved = export.write_unsaved([packed_holder], out_dir, "Rig")
png = os.path.join(out_dir, "Rig_textures", "Rig_atlas_base.png")
assert saved == {"Rig_atlas_base": png} and os.path.getsize(png) > 50, saved
assert pack_img.packed_file is not None, "the atlas stays packed in the .blend"
listed = export.material_textures([packed_holder], out_dir, saved)
assert listed == [{"name": "Packed", "textures": [{"role": "baseColor", "path": "Rig_textures/Rig_atlas_base.png"}]}], listed
assert export.material_textures([packed_holder], out_dir) == [{"name": "Packed", "textures": []}], "without it there is nothing to point at"
print("UNITYSYNC OK")
