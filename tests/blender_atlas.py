# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Krislyz
"""Atlas. Run: blender -b --factory-startup --python tests/blender_atlas.py"""
import os
import sys
import tempfile

import bpy
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..")))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import strict  # noqa: E402,F401
from kat.modules import atlas  # noqa: E402

bpy.ops.wm.read_factory_settings(use_empty=True)


def solid_image(name, rgba, size=64, space=None):
    img = bpy.data.images.new(name, size, size, alpha=True)
    if space:
        img.colorspace_settings.name = space  # before the pixels: changing it afterwards regenerates a generated image
    img.pixels.foreach_set(np.tile(np.array(rgba, dtype=np.float32), size * size))
    return img


def material(name, image=None, color=None, alpha=1.0, normal=None, cutout=False, emission=None, metallic=0.0,
             roughness=0.5, bad_normal=False):
    mat = bpy.data.materials.new(name)
    tree = mat.node_tree
    node = next(n for n in tree.nodes if n.type == "BSDF_PRINCIPLED")
    node.inputs["Metallic"].default_value = metallic
    node.inputs["Roughness"].default_value = roughness
    if image:
        tex = tree.nodes.new("ShaderNodeTexImage")
        tex.image = image
        tree.links.new(tex.outputs["Color"], node.inputs["Base Color"])
        if cutout:
            tree.links.new(tex.outputs["Alpha"], node.inputs["Alpha"])
    if color:
        node.inputs["Base Color"].default_value = color
    node.inputs["Alpha"].default_value = alpha if not cutout else 1.0
    if normal is not None or bad_normal:
        nm = tree.nodes.new("ShaderNodeNormalMap")
        tree.links.new(nm.outputs["Normal"], node.inputs["Normal"])
        if normal is not None:
            nt = tree.nodes.new("ShaderNodeTexImage")
            nt.image = normal
            tree.links.new(nt.outputs["Color"], nm.inputs["Color"])
    if emission is not None:
        et = tree.nodes.new("ShaderNodeTexImage")
        et.image = emission
        tree.links.new(et.outputs["Color"], node.inputs["Emission Color"])
        node.inputs["Emission Strength"].default_value = 1.0
    return mat


def quads(name, mats, offset=0):
    """One quad per material, each with full 0-1 UVs."""
    ob = bpy.data.objects.new(name, bpy.data.meshes.new(name))
    bpy.context.scene.collection.objects.link(ob)
    verts, faces = [], []
    for q in range(len(mats)):
        verts += [(offset + q * 2, 0, 0), (offset + q * 2 + 1, 0, 0), (offset + q * 2 + 1, 1, 0), (offset + q * 2, 1, 0)]
        faces.append((q * 4, q * 4 + 1, q * 4 + 2, q * 4 + 3))
    ob.data.from_pydata(verts, [], faces)
    ob.data.uv_layers.new(name="UVMap")
    for i, loop in enumerate(ob.data.uv_layers.active.data):
        loop.uv = [(0, 0), (1, 0), (1, 1), (0, 1)][i % 4]
    for m in mats:
        ob.data.materials.append(m)
    for i, p in enumerate(ob.data.polygons):
        p.material_index = i
    return ob


def pixels(image):
    px = np.empty(image.size[0] * image.size[1] * 4, dtype=np.float32)
    image.pixels.foreach_get(px)
    return px.reshape(image.size[1], image.size[0], 4)


def colour_at(ob, atlas_image, polygon):
    """The atlas colour under the middle of a polygon's UVs."""
    me = ob.data
    p = me.polygons[polygon]
    u, v = np.mean([me.uv_layers.active.data[l].uv for l in range(p.loop_start, p.loop_start + p.loop_total)], axis=0)
    assert 0 < u < 1 and 0 < v < 1, (u, v)
    h, w = atlas_image.size[1], atlas_image.size[0]
    return tuple(round(float(c), 2) for c in pixels(atlas_image)[int(v * h), int(u * w), :3])  # python floats: float32 == 0.2 is False on older numpy


# ---- one mesh: flat colours and images combine, the rest is left alone with a reason ----
mats = [
    material("Red", solid_image("red", (1, 0, 0, 1))),
    material("Green", solid_image("green", (0, 1, 0, 1))),
    material("Blue", color=(0, 0, 1, 1)),                       # flat colour, no image
    material("Glass", color=(1, 1, 1, 1), alpha=0.5),           # soft transparency: left alone
    material("BadNormal", solid_image("n", (0.5, 0.5, 1, 1)), bad_normal=True),  # normal map with no image: left alone
    material("Tiled", solid_image("t", (1, 1, 0, 1))),          # UVs past 1: left alone
]
ob = quads("Quads", mats)
uv = ob.data.uv_layers.active.data
tiled = ob.data.polygons[5]
for li in range(tiled.loop_start, tiled.loop_start + 4):
    uv[li].uv = uv[li].uv * 3
out = tempfile.mkdtemp(prefix="kat_atlas_")
built, skipped = atlas.build([ob], size=512, name="atlas", folder=out)
assert len(built) == 1 and built[0].materials == ["Red", "Green", "Blue"], built
assert dict(skipped) == {"Glass": "transparent", "BadNormal": "normal setup is not a plain tangent normal map",
                         "Tiled": "UVs outside the 0-1 square"}, skipped
names = [s.material.name for s in ob.material_slots]
assert sorted(names) == sorted(["atlas", "Glass", "BadNormal", "Tiled"]), names
image = built[0].images["base"]
assert os.path.getsize(os.path.join(out, "atlas_base.png")) > 100 and image.size[0] == 512
slot = names.index("atlas")
seen = [colour_at(ob, image, p.index) for p in ob.data.polygons if p.material_index == slot]
assert seen == [(1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)], seen
assert max(ob.data.uv_layers.active.data[l].uv.x for l in range(tiled.loop_start, tiled.loop_start + 4)) == 3.0

# ---- several meshes share one atlas, with normal maps and emission ----
bpy.ops.wm.read_factory_settings(use_empty=True)
norm_a = solid_image("na", (0.2, 0.8, 0.6, 1), space="Non-Color")  # normal maps are tagged Non-Color, as real files are
norm_b = solid_image("nb", (0.8, 0.2, 0.6, 1), space="Non-Color")
glow = solid_image("glow", (1, 0.5, 0, 1))
shirt_mats = [material("ShirtA", solid_image("sa", (1, 0, 0, 1)), normal=norm_a, emission=glow),
              material("ShirtB", solid_image("sb", (0, 1, 0, 1), 128), normal=norm_b, emission=glow)]
pants_mats = [material("PantsA", solid_image("pa", (0, 0, 1, 1)), normal=norm_a, emission=glow),
              shirt_mats[0]]  # shared between the two meshes: one tile
shirt = quads("Shirt", shirt_mats)
pants = quads("Pants", pants_mats, offset=10)
built, skipped = atlas.build([shirt, pants], size=1024, name="multi", folder="")
assert len(built) == 1 and sorted(built[0].materials) == ["PantsA", "ShirtA", "ShirtB"], built
imgs = built[0].images
assert set(imgs) == {"base", "normal", "emission"}
assert imgs["normal"].colorspace_settings.name == "Non-Color" and imgs["base"].colorspace_settings.name == "sRGB"
assert imgs["base"].packed_file is not None, "no folder: packed into the blend"
for ob_, slots in ((shirt, ["multi", "multi"]), (pants, ["multi", "multi"])):
    assert [s.material.name for s in ob_.material_slots] == ["multi"], [s.material.name for s in ob_.material_slots]
sa = colour_at(shirt, imgs["base"], 0)
pa = colour_at(pants, imgs["base"], 1)
assert sa == pa == (1.0, 0.0, 0.0), (sa, pa)        # the shared material has one tile, so both meshes point at it
assert colour_at(shirt, imgs["base"], 1) == (0.0, 1.0, 0.0) and colour_at(pants, imgs["base"], 0) == (0.0, 0.0, 1.0)
assert colour_at(shirt, imgs["normal"], 0) == (0.2, 0.8, 0.6) and colour_at(shirt, imgs["normal"], 1) == (0.8, 0.2, 0.6)
assert colour_at(shirt, imgs["emission"], 0) == (1.0, 0.5, 0.0)
mat = shirt.material_slots[0].material
node = next(n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
assert node.inputs["Normal"].is_linked and node.inputs["Emission Color"].is_linked

# ---- cut-out transparency is kept, and does not mix with opaque materials ----
bpy.ops.wm.read_factory_settings(use_empty=True)
leaf = lambda n, a: material(n, solid_image(n + "i", (0, 1, 0, a)), cutout=True)  # noqa: E731
flat = lambda n, c: material(n, solid_image(n + "i", c))  # noqa: E731
mixed = quads("Mixed", [leaf("Leaf1", 0.25), leaf("Leaf2", 0.75), flat("Wall1", (1, 0, 0, 1)), flat("Wall2", (0, 0, 1, 1))])
built, skipped = atlas.build([mixed], size=512, name="split", folder="")
assert len(built) == 2 and sorted(sorted(g.materials) for g in built) == [["Leaf1", "Leaf2"], ["Wall1", "Wall2"]], built
cut = next(g for g in built if "Leaf1" in g.materials)
assert abs(pixels(cut.images["base"])[..., 3].max() - 0.75) < 0.02, "alpha is kept"
leaf_mat = next(s.material for s in mixed.material_slots if s.material.name == cut.name)
principled = next(n for n in leaf_mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
assert principled.inputs["Alpha"].is_linked

# ---- FBX imports flag colour images Non-Color: they are kept as they are, and never mixed with sRGB ones ----
bpy.ops.wm.read_factory_settings(use_empty=True)
flagged = []
for n, c in (("F1", (1, 0, 0, 1)), ("F2", (0, 1, 0, 1))):
    flagged.append(material(n + "m", solid_image(n, c, space="Non-Color")))
plain = [material("S1m", solid_image("S1", (0, 0, 1, 1))), material("S2m", solid_image("S2", (1, 1, 0, 1)))]
two_kinds = quads("TwoKinds", flagged + plain)
built, _ = atlas.build([two_kinds], size=256, name="spaces", folder="")
assert len(built) == 2
for g in built:
    want = "Non-Color" if "F1m" in g.materials else "sRGB"
    assert g.images["base"].colorspace_settings.name == want, (g.materials, want)
nc = next(g for g in built if "F1m" in g.materials)
assert colour_at(two_kinds, nc.images["base"], 0) == (1.0, 0.0, 0.0), colour_at(two_kinds, nc.images["base"], 0)
# a flat colour in a Non-Color group is stored as it is, not gamma-encoded
bpy.ops.wm.read_factory_settings(use_empty=True)
im = solid_image("NCimg", (0.2, 0.2, 0.2, 1), space="Non-Color")
mixed_flat = quads("Flat", [material("NCa", im), material("NCb", color=(0.2, 0.2, 0.2, 1))])
b1, _ = atlas.build([mixed_flat], size=256, name="flat", folder="")
assert len(b1) == 0, "a flat colour is sRGB, so it does not join a Non-Color image group"

# ---- transparency wired to the colour (what FBX imports do) is refused unless asked to be ignored ----
bpy.ops.wm.read_factory_settings(use_empty=True)


def colour_alpha(name, rgba):
    m = material(name, solid_image(name + "i", rgba))
    tree = m.node_tree
    base_tex = next(n for n in tree.nodes if n.type == "TEX_IMAGE")
    second = tree.nodes.new("ShaderNodeTexImage")
    second.image = base_tex.image
    node = next(n for n in tree.nodes if n.type == "BSDF_PRINCIPLED")
    tree.links.new(second.outputs["Color"], node.inputs["Alpha"])
    return m


wired = quads("Wired", [colour_alpha("W1", (1, 0, 0, 1)), colour_alpha("W2", (0, 1, 0, 1))])
assert atlas.build([wired], 256, "w", "")[0] == []
assert dict(atlas.plan([wired])[1]) == {"W1": "transparency is read from the colour, not the alpha channel",
                                         "W2": "transparency is read from the colour, not the alpha channel"}
built, _ = atlas.build([wired], 256, "w", "", loose=True)
assert len(built) == 1 and sorted(built[0].materials) == ["W1", "W2"]
principled_w = next(n for n in bpy.data.materials[built[0].name].node_tree.nodes if n.type == "BSDF_PRINCIPLED")
assert not principled_w.inputs["Alpha"].is_linked, "treated as opaque"

# ---- different metallic or roughness values never mix ----
bpy.ops.wm.read_factory_settings(use_empty=True)
shiny = quads("Shiny", [material("M1", color=(1, 0, 0, 1), metallic=1.0), material("M2", color=(0, 1, 0, 1), metallic=1.0),
                        material("D1", color=(0, 0, 1, 1)), material("D2", color=(1, 1, 0, 1))])
built, _ = atlas.build([shiny], size=256, name="metal", folder="")
assert len(built) == 2
for g in built:
    m = bpy.data.materials[g.name]
    value = next(n for n in m.node_tree.nodes if n.type == "BSDF_PRINCIPLED").inputs["Metallic"].default_value
    assert value == (1.0 if "M1" in g.materials else 0.0)

# ---- images the atlas would mis-encode or mis-place are refused, with the reason ----
linear = solid_image("linear", (0.2, 0.2, 0.2, 1))
linear.colorspace_settings.name = "Linear Rec.709"
assert atlas.spec_of(material("Linear", linear))[1] == "base colour image uses the Linear Rec.709 colour space"
mapped = material("Mapped", solid_image("mapped", (1, 0, 1, 1)))
tex = next(n for n in mapped.node_tree.nodes if n.type == "TEX_IMAGE")
mapping = mapped.node_tree.nodes.new("ShaderNodeMapping")
mapped.node_tree.links.new(mapping.outputs["Vector"], tex.inputs["Vector"])
assert atlas.spec_of(mapped)[1] == "base colour uses custom texture coordinates"
rough = material("Rough", solid_image("r", (1, 1, 1, 1)))
rn = next(n for n in rough.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
rt = rough.node_tree.nodes.new("ShaderNodeTexImage")
rt.image = solid_image("rimg", (0.5, 0.5, 0.5, 1))
rough.node_tree.links.new(rt.outputs["Color"], rn.inputs["Roughness"])
assert atlas.spec_of(rough)[1] == "Roughness is driven by something"

# an unwritable folder falls back to packing instead of raising
bpy.ops.wm.read_factory_settings(use_empty=True)
two = quads("Two", [material("T1", color=(1, 0, 0, 1)), material("T2", color=(0, 1, 0, 1))])
blocker = os.path.join(tempfile.mkdtemp(prefix="kat_block_"), "blocker")
open(blocker, "w").close()  # a file where a folder is needed, so the save cannot succeed
built, _ = atlas.build([two], size=128, name="fallback", folder=os.path.join(blocker, "sub"))
assert len(built) == 1 and built[0].images["base"].packed_file is not None

# nothing to combine -> nothing happens
bpy.ops.wm.read_factory_settings(use_empty=True)
solo = quads("Solo", [material("Only", color=(1, 0, 0, 1))])
assert atlas.build([solo], 256) == ([], []) or atlas.build([solo], 256)[0] == []

# the planner agrees with the builder without changing anything
bpy.ops.wm.read_factory_settings(use_empty=True)
pair = quads("Pair", [material("P1", color=(1, 0, 0, 1)), material("P2", color=(0, 1, 0, 1)), material("Glass2", alpha=0.3)])
groups, skipped = atlas.plan([pair])
assert len(groups) == 1 and sorted(groups[0].materials) == ["P1", "P2"] and dict(skipped) == {"Glass2": "transparent"}
assert [s.material.name for s in pair.material_slots] == ["P1", "P2", "Glass2"], "plan changes nothing"
print("ATLAS OK")
