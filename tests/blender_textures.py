# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Krislyz
"""Texture search. Run: blender -b --factory-startup --python tests/blender_textures.py"""
import os
import sys
import tempfile
import time

import bpy
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..")))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import strict  # noqa: E402,F401

from kat.modules import textures  # noqa: E402

bpy.ops.wm.read_factory_settings(use_empty=True)
root = tempfile.mkdtemp(prefix="kat_tex_")
models, texdir = os.path.join(root, "Models"), os.path.join(root, "Textures")
os.makedirs(models)
os.makedirs(texdir)


def write_png(path, rgba):
    img = bpy.data.images.new(os.path.basename(path), 4, 4, alpha=True)
    img.pixels.foreach_set(np.tile(np.array(rgba, dtype=np.float32), 16))
    img.filepath_raw = path
    img.file_format = "PNG"
    img.save()
    bpy.data.images.remove(img)


write_png(os.path.join(texdir, "Body_D.png"), (1, 0, 0, 1))
write_png(os.path.join(texdir, "face.png"), (0, 1, 0, 1))
write_png(os.path.join(texdir, "cloth_n.png"), (0.5, 0.5, 1, 1))   # a normal map: never a base colour
write_png(os.path.join(texdir, "cloth_d.png"), (0, 0, 1, 1))


def material(name, ref=None):
    mat = bpy.data.materials.new(name)
    if ref:
        node = mat.node_tree.nodes.new("ShaderNodeTexImage")
        img = bpy.data.images.new(os.path.basename(ref), 4, 4)
        img.source = "FILE"
        img.filepath = ref  # a path from somebody else's computer: the file is not there
        node.image = img
        pr = next(n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
        mat.node_tree.links.new(node.outputs["Color"], pr.inputs["Base Color"])
    return mat


ob = bpy.data.objects.new("Model", bpy.data.meshes.new("Model"))
bpy.context.scene.collection.objects.link(ob)
mats = [
    material("Body", "C:\\Users\\artist\\Desktop\\tex\\body_d.png"),   # same name, other case
    material("Face", "D:/proj/face.tga"),                              # same name, other extension
    material("Gone", "C:\\nowhere\\missing_thing.png"),                # exists nowhere
    material("cloth"),                                                 # no texture at all: matched by name
    material("Metal"),                                                 # no texture and no match: left alone
]
for m in mats:
    ob.data.materials.append(m)

assert all(textures.is_missing(n.image) for m in mats[:3] for n in textures.image_nodes(m))
assert [m.name for m in textures.bare_materials(mats)] == ["cloth", "Metal"]

start = time.monotonic()
res = textures.find_textures([ob], textures.search_roots(os.path.join(models, "Model.fbx")), seconds=8.0)
assert time.monotonic() - start < 8.0
assert res.fixed == 2 and res.created == 1 and res.unresolved == ["missing_thing.png"], res
body_img = textures.image_nodes(mats[0])[0].image
assert os.path.normcase(body_img.filepath) == os.path.normcase(os.path.join(texdir, "Body_D.png")) and body_img.size[0] == 4
assert textures.image_nodes(mats[1])[0].image.size[0] == 4
cloth = textures.image_nodes(mats[3])
assert len(cloth) == 1 and os.path.basename(cloth[0].image.filepath) == "cloth_d.png", "colour map, not the normal map"
assert [m.name for m in textures.bare_materials(mats)] == ["Metal"]
assert "2 relinked" in textures.summary(res) and "missing_thing.png" in textures.summary(res)

# the Texture view shows each material's active image node, so the base colour image must be the active one
mat = bpy.data.materials.new("TwoImages")
tree = mat.node_tree
extra = tree.nodes.new("ShaderNodeTexImage")
extra.image = bpy.data.images.new("rough.png", 4, 4)
colour = tree.nodes.new("ShaderNodeTexImage")
colour.image = bpy.data.images.new("colour.png", 4, 4)
mix = tree.nodes.new("ShaderNodeMix")
mix.data_type = "RGBA"
tree.links.new(colour.outputs["Color"], mix.inputs[6])
tree.links.new(mix.outputs[2], next(n for n in tree.nodes if n.type == "BSDF_PRINCIPLED").inputs["Base Color"])
assert tree.nodes.active.name != colour.name
assert textures.activate_image_nodes([mat]) == 1 and tree.nodes.active.name == colour.name, "found through the colour mix"
assert textures.activate_image_nodes([mat]) == 0, "already set"
plain = bpy.data.materials.new("NoImages")
assert textures.activate_image_nodes([plain]) == 0

# nothing missing means nothing is scanned at all
again = textures.find_textures([ob], textures.search_roots(os.path.join(models, "Model.fbx")))
assert again.scanned == 0 or again.fixed == 0
fresh = bpy.data.objects.new("Fresh", bpy.data.meshes.new("Fresh"))
bpy.context.scene.collection.objects.link(fresh)
assert textures.find_textures([fresh], [root]) is textures.NOTHING

# automatic: a model with a missing texture finds it next to where it was imported from, with nobody asking
auto = bpy.data.objects.new("Auto", bpy.data.meshes.new("Auto"))
bpy.context.scene.collection.objects.link(auto)
auto.data.materials.append(material("AutoMat", "C:\gone\Cloth_D.PNG"))
auto["kat_source"] = os.path.join(models, "Model.fbx")
assert textures.missing_images([auto])
found = textures.auto_find([auto])
assert found.fixed == 1 and not textures.missing_images([auto]), found
assert textures.auto_find([auto]) is textures.NOTHING, "nothing missing, nothing scanned"
print("TEXTURES OK")
