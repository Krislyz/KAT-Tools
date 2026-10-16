# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Krislyz
"""Texture optimizer. Run: blender -b --factory-startup --python tests/blender_texopt.py"""
import os
import sys
import tempfile

import bpy
import numpy as np

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import strict  # noqa: E402,F401

import kat  # noqa: E402
from kat.modules import texopt  # noqa: E402

kat.register()
scene = bpy.context.scene
assert texopt.power_of_two(1000) == 1024 and texopt.power_of_two(700) == 512 and texopt.power_of_two(1) == 1
assert texopt.target_size(4096, 2048, 1024, True) == (1024, 512)
assert texopt.target_size(1000, 600, 4096, True) == (1024, 512), "odd sizes round to powers of two"
assert texopt.target_size(1000, 600, 4096, False) == (1000, 600)
assert texopt.target_size(300, 300, 1024, True) == (256, 256)

folder = tempfile.mkdtemp(prefix="kat_tex_")
mat = bpy.data.materials.new("M")
tree = mat.node_tree


def add(name, w, h, rgba, saved=True):
    img = bpy.data.images.new(name, w, h, alpha=True)
    img.pixels.foreach_set(np.tile(np.array(rgba, dtype=np.float32), w * h))
    if saved:
        img.filepath_raw = os.path.join(folder, name + ".png")
        img.file_format = "PNG"
        img.save()
    node = tree.nodes.new("ShaderNodeTexImage")
    node.image = img
    return img


big = add("big", 2048, 2048, (1, 0, 0, 1))
odd = add("odd", 1000, 600, (0, 1, 0, 1))
small = add("small", 128, 128, (0, 0, 1, 1))
made = bpy.data.objects.new("Thing", bpy.data.meshes.new("Thing"))
scene.collection.objects.link(made)
made.data.materials.append(mat)
made.select_set(True)

images = texopt.used_images([made])
assert [i.name for i in images] == ["big", "odd", "small"], "biggest first"
before = texopt.megabytes(images)

done = texopt.shrink(images, 512, True)
assert {d[0] for d in done} == {"big", "odd"}, done
sizes = {d[0]: d[3] for d in done}
assert sizes["big"] == (512, 512) and sizes["odd"] == (512, 256), sizes
assert os.path.isfile(os.path.join(folder, "big_512.png")) and os.path.isfile(os.path.join(folder, "big.png")), "original stays"
used = {n.image.name: n.image for n in tree.nodes if n.type == "TEX_IMAGE"}
assert set(used) == {"big_512", "odd_512", "small"}, set(used)
px = np.empty(512 * 512 * 4, dtype=np.float32)
used["big_512"].pixels.foreach_get(px)
assert abs(px[0] - 1.0) < 0.02 and abs(px[1]) < 0.02, "the colours survive"
assert texopt.megabytes(texopt.used_images([made])) < before / 5

# nothing left to shrink; a packed image with no file is shrunk and packed
assert texopt.shrink(texopt.used_images([made]), 512, True) == []
mem = add("mem", 1024, 1024, (1, 1, 0, 1), saved=False)
done = texopt.shrink([mem], 256, True)
assert done and bpy.data.images["mem_256"].packed_file is not None

# the operator and the unused-image cleaner
for o in list(scene.objects):
    o.select_set(False)
made.select_set(True)
scene.kat.tex_limit = "256"
assert bpy.ops.kat.textures_shrink() in ({"FINISHED"}, {"CANCELLED"})
orphan = bpy.data.images.new("orphan", 4, 4)
assert bpy.ops.kat.textures_unused() == {"FINISHED"} and "orphan" not in bpy.data.images

kat.unregister()
print("TEXOPT OK")
