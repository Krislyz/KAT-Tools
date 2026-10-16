# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Krislyz
"""Material atlas: pack many materials, across many meshes, into one texture and one material slot.

A material can go in the atlas when its base colour is a plain image or a colour, with an optional normal map,
emission image or colour, and cut-out transparency taken from the base image. Metallic and roughness have to be plain
numbers. Materials that differ in those numbers, or in which of the three maps they use, go into separate atlases.
Anything else (soft transparency, texture-driven roughness, UVs outside the 0-1 square ...) is left alone and listed
with the reason.
"""
import os
from types import SimpleNamespace

import bpy
import numpy as np
from bpy.props import EnumProperty

from ..core import pack
from ..core.safe import operator_guard
from . import armature, materials, textures

UV_SLACK = 1e-3
PAD = 4  # pixels of edge copied around every tile, so smooth filtering and mip maps do not mix neighbours
COLOUR_TILE = 16
FLAT_NORMAL = (0.5, 0.5, 1.0, 1.0)
LINKABLE = {"Base Color", "Alpha", "Normal", "Emission Color"}
# Models imported from FBX often have their colour images flagged Non-Color. The atlas copies pixels as they are, so it
# keeps whichever of the two the images use, and never mixes them in one atlas.
SPACES = ("sRGB", "Non-Color")

last = {"groups": [], "skipped": [], "text": ""}  # what the panel shows after a scan or a build


def principled(mat):
    tree = mat.node_tree  # always there since Blender 5.0; use_nodes is deprecated
    return next((n for n in tree.nodes if n.type == "BSDF_PRINCIPLED"), None) if tree else None


def to_srgb(linear):
    c = np.clip(np.asarray(linear, dtype=np.float32), 0.0, 1.0)
    return np.where(c <= 0.0031308, c * 12.92, 1.055 * np.power(c, 1 / 2.4) - 0.055)


def plain_image(socket):
    """The image of a TEX_IMAGE node plugged straight into `socket` with default texture coordinates, else (None, why)."""
    src = socket.links[0].from_node
    if src.type != "TEX_IMAGE" or src.image is None:
        return None, "is not a plain image"
    if src.inputs["Vector"].is_linked:
        return None, "uses custom texture coordinates"
    img = src.image
    if img.is_float or img.size[0] == 0:
        return None, "image is not 8-bit or has no pixels"
    return src, ""


def spec_of(mat, loose=False):
    """(spec, reason). The spec says what goes in the atlas; the reason says why not.
    `loose` treats transparency read from the base image's colour (a wiring FBX imports often make, which only dims
    dark pixels) as no transparency at all."""
    node = principled(mat)
    if node is None:
        return None, "no Principled BSDF"
    for sock in node.inputs:
        if sock.is_linked and sock.name not in LINKABLE:
            return None, f"{sock.name} is driven by something"
    spec = SimpleNamespace(base=None, alpha="opaque", normal=None, emission=None, strength=0.0, base_space="sRGB",
                           emission_space="sRGB",
                           metallic=float(node.inputs["Metallic"].default_value),
                           roughness=float(node.inputs["Roughness"].default_value))
    base = node.inputs["Base Color"]
    if base.is_linked:
        src, why = plain_image(base)
        if src is None:
            return None, "base colour " + why
        space = src.image.colorspace_settings.name
        if space not in SPACES:
            return None, f"base colour image uses the {space} colour space"
        spec.base = ("image", src.image)
        spec.base_space = space
        base_node = src
    else:
        spec.base = ("color", tuple(base.default_value))
        base_node = None
    alpha = node.inputs["Alpha"]
    if alpha.is_linked:
        link = alpha.links[0]
        same_image = base_node is not None and link.from_node.type == "TEX_IMAGE" and link.from_node.image == base_node.image
        if not same_image:
            return None, "transparency does not come from the base image"
        if link.from_socket.name == "Alpha":
            spec.alpha = "cutout"
        elif not loose:
            return None, "transparency is read from the colour, not the alpha channel"
    elif alpha.default_value < 0.999:
        return None, "transparent"
    normal = node.inputs["Normal"]
    if normal.is_linked:
        nm = normal.links[0].from_node
        if nm.type != "NORMAL_MAP" or not nm.inputs["Color"].is_linked or abs(nm.inputs["Strength"].default_value - 1.0) > 1e-3 \
                or nm.space != "TANGENT" or nm.inputs["Strength"].is_linked:
            return None, "normal setup is not a plain tangent normal map"
        src, why = plain_image(nm.inputs["Color"])
        if src is None:
            return None, "normal map " + why
        spec.normal = src.image
    emission = node.inputs["Emission Color"]
    strength = node.inputs["Emission Strength"]
    if strength.is_linked:
        return None, "emission strength is driven by something"
    spec.strength = float(strength.default_value)
    if spec.strength > 0:
        if emission.is_linked:
            src, why = plain_image(emission)
            if src is None:
                return None, "emission " + why
            space = src.image.colorspace_settings.name
            if space not in SPACES:
                return None, f"emission image uses the {space} colour space"
            spec.emission = ("image", src.image)
            spec.emission_space = space
        elif max(emission.default_value[:3]) > 0.001:
            spec.emission = ("color", tuple(emission.default_value))
    return spec, ""


def group_key(spec):
    return (spec.alpha, round(spec.metallic, 2), round(spec.roughness, 2), spec.normal is not None,
            spec.emission[0] if spec.emission else "", round(spec.strength, 2) if spec.emission else 0.0,
            spec.base_space, spec.emission_space if spec.emission else "")


def loop_arrays(mesh):
    """(uvs (loops, 2), material index per loop, polygon index per loop) of the active UV map."""
    layer = mesh.uv_layers.active
    if layer is None:
        return None, None, None
    uvs = np.empty(len(mesh.loops) * 2, dtype=np.float32)
    layer.data.foreach_get("uv", uvs)
    n = len(mesh.polygons)
    start, total, mat = (np.empty(n, dtype=np.int32) for _ in range(3))
    mesh.polygons.foreach_get("loop_start", start)
    mesh.polygons.foreach_get("loop_total", total)
    mesh.polygons.foreach_get("material_index", mat)
    return uvs.reshape(-1, 2), np.repeat(mat, total), np.repeat(np.arange(n), total)


def uv_bounds_ok(uvs, loop_mat, index):
    mask = loop_mat == index
    if not mask.any():
        return True
    lo, hi = uvs[mask].min(), uvs[mask].max()
    return lo >= -UV_SLACK and hi <= 1 + UV_SLACK


def resize(px, height, width):
    """Resample to height x width: a box filter when shrinking, then bilinear."""
    h, w = px.shape[:2]
    if (h, w) == (height, width):
        return px
    fy, fx = max(h // height, 1), max(w // width, 1)
    if fy > 1 or fx > 1:
        px = px[:h - h % fy, :w - w % fx].reshape(h // fy, fy, w // fx, fx, 4).mean(axis=(1, 3))
        h, w = px.shape[:2]
    ys, xs = np.linspace(0, h - 1, height), np.linspace(0, w - 1, width)
    y0, x0 = np.floor(ys).astype(int), np.floor(xs).astype(int)
    y1, x1 = np.minimum(y0 + 1, h - 1), np.minimum(x0 + 1, w - 1)
    wy, wx = (ys - y0)[:, None, None], (xs - x0)[None, :, None]
    return (px[y0][:, x0] * (1 - wy) * (1 - wx) + px[y0][:, x1] * (1 - wy) * wx
            + px[y1][:, x0] * wy * (1 - wx) + px[y1][:, x1] * wy * wx)


def image_pixels(image):
    w, h = image.size
    px = np.empty(w * h * 4, dtype=np.float32)
    image.pixels.foreach_get(px)
    return px.reshape(h, w, 4)


def tile_pixels(source, height, width, space="sRGB"):
    """Pixels (height, width, 4) of a base or emission source, padded by PAD pixels of the edge on every side.
    A flat colour is written the way the atlas image will be read: encoded for sRGB, as it is for Non-Color."""
    kind, value = source
    ih, iw = height - 2 * PAD, width - 2 * PAD
    if kind == "color":
        rgb = to_srgb(value[:3]) if space == "sRGB" else np.clip(np.asarray(value[:3], dtype=np.float32), 0.0, 1.0)
        inner = np.broadcast_to(np.append(rgb, 1.0).astype(np.float32), (ih, iw, 4))
    else:
        inner = resize(image_pixels(value), ih, iw)
    return np.pad(inner, ((PAD, PAD), (PAD, PAD), (0, 0)), mode="edge")


def source_size(spec):
    """How big a material's tile would like to be: its largest image, or a tiny tile for a plain colour."""
    images = [s[1] for s in (spec.base, spec.emission) if s and s[0] == "image"] + ([spec.normal] if spec.normal else [])
    if not images:
        return COLOUR_TILE + 2 * PAD
    return max(max(i.size) for i in images) + 2 * PAD


def scope_objects(context):
    """The meshes to work on: the selected ones, else everything rigged to the armature."""
    chosen = [o for o in context.selected_objects if o.type == "MESH"]
    if chosen:
        return chosen
    rig = armature.rig_of(context)
    return armature.meshes_of(rig) if rig else []


def plan(objs, size=2048, loose=False):
    """Work out what could be atlased without changing anything. Returns (groups, skipped).
    A group is a namespace with `key`, `materials` (name -> spec) and `meshes`; skipped is [(material name, reason)]."""
    meshes = {}
    for ob in objs:
        if ob.type == "MESH":
            meshes.setdefault(ob.data, []).append(ob)
    verdict, reasons = {}, {}
    bare = [m.name for m in meshes if m.uv_layers.active is None]
    if meshes and len(bare) == len(meshes):
        raise ValueError(f"{', '.join(bare)} has no UV map" if len(bare) == 1 else f"{', '.join(bare)} have no UV map")
    for mesh in meshes:
        uvs, loop_mat, _ = loop_arrays(mesh)
        used = {i: s.material for i, s in enumerate(meshes[mesh][0].material_slots)}
        for i, mat in used.items():
            if mat is None or not (loop_mat is not None and (loop_mat == i).any()) and loop_mat is not None:
                continue
            if mat.name in reasons:
                continue
            if loop_mat is None:
                reasons[mat.name] = f"{mesh.name} has no UV map"
                continue
            spec, why = spec_of(mat, loose)
            if spec is not None and not uv_bounds_ok(uvs, loop_mat, i):
                spec, why = None, "UVs outside the 0-1 square"
            if spec is None:
                reasons[mat.name] = why
            else:
                verdict[mat.name] = (mat, spec)
    for mesh in meshes:  # a bad UV on any mesh that uses a material rules the material out everywhere
        uvs, loop_mat, _ = loop_arrays(mesh)
        for i, slot in enumerate(meshes[mesh][0].material_slots):
            mat = slot.material
            if mat is None or mat.name not in verdict:
                continue
            if loop_mat is None:  # shared with a mesh that has no UV map: it cannot be moved into the atlas
                del verdict[mat.name]
                reasons[mat.name] = f"{mesh.name} has no UV map"
            elif not uv_bounds_ok(uvs, loop_mat, i):
                del verdict[mat.name]
                reasons[mat.name] = "UVs outside the 0-1 square"
    groups = {}
    for name, (mat, spec) in verdict.items():
        g = groups.setdefault(group_key(spec), SimpleNamespace(key=group_key(spec), materials={}, meshes=set()))
        g.materials[name] = (mat, spec)
    skipped = sorted(reasons.items())
    out = []
    for g in groups.values():
        if len(g.materials) < 2:
            skipped += [(n, "the only one of its kind (other maps or settings)") for n in g.materials]
            continue
        g.meshes = [m for m in meshes if any(s.material and s.material.name in g.materials
                                             for s in meshes[m][0].material_slots)]
        out.append(g)
    return out, skipped


def make_image(name, pixels, size, colourspace, folder):
    image = bpy.data.images.new(name, size, size, alpha=True)
    image.colorspace_settings.name = colourspace
    image.pixels.foreach_set(pixels.ravel())
    saved = False
    if folder:
        try:
            image.filepath_raw = os.path.join(folder, name + ".png")
            image.file_format = "PNG"
            image.save()
            saved = image.source == "FILE"
        except (RuntimeError, OSError):
            saved = False  # bad file name or read-only folder: keep the atlas inside the .blend instead
    if not saved:
        image.pack()
    return image


def build_group(g, size, name, folder):
    """Make the atlas images and the new material for one group. Returns (material, tile rectangles, images)."""
    names = list(g.materials)
    wants = [source_size(g.materials[n][1]) for n in names]
    fitted = pack.fit([(w, w) for w in wants], size, gap=0, smallest=4 * PAD)
    if fitted is None:
        raise ValueError(f"{len(names)} materials do not fit in a {size} px atlas")
    spots, sizes, _ = fitted
    rects = {n: (spots[i][0], spots[i][1], sizes[i][0], sizes[i][1]) for i, n in enumerate(names)}
    some = g.materials[names[0]][1]
    base = np.zeros((size, size, 4), dtype=np.float32)
    base[..., :3] = 0.5
    base[..., 3] = 1.0 if some.alpha == "opaque" else 0.0
    normal = np.broadcast_to(np.array(FLAT_NORMAL, dtype=np.float32), (size, size, 4)).copy() if some.normal else None
    emission = np.zeros((size, size, 4), dtype=np.float32) if some.emission else None
    emission_tile = np.zeros(4, dtype=np.float32)
    emission_tile[3] = 1.0
    for n in names:
        spec = g.materials[n][1]
        x, y, w, h = rects[n]
        base[y:y + h, x:x + w] = tile_pixels(spec.base, h, w, spec.base_space)
        if normal is not None:
            ih, iw = h - 2 * PAD, w - 2 * PAD
            inner = resize(image_pixels(spec.normal), ih, iw)
            normal[y:y + h, x:x + w] = np.pad(inner, ((PAD, PAD), (PAD, PAD), (0, 0)), mode="edge")
        if emission is not None:
            emission[y:y + h, x:x + w] = tile_pixels(spec.emission, h, w, spec.emission_space)
    images = {"base": make_image(name + "_base", base, size, some.base_space, folder)}
    if normal is not None:
        images["normal"] = make_image(name + "_normal", normal, size, "Non-Color", folder)
    if emission is not None:
        images["emission"] = make_image(name + "_emission", emission, size, some.emission_space, folder)
    mat = bpy.data.materials.new(name)
    mat["kat_atlas"] = True
    tree = mat.node_tree
    node = principled(mat) or tree.nodes.new("ShaderNodeBsdfPrincipled")
    node.inputs["Metallic"].default_value = some.metallic
    node.inputs["Roughness"].default_value = some.roughness
    tex = tree.nodes.new("ShaderNodeTexImage")
    tex.image = images["base"]
    tree.links.new(tex.outputs["Color"], node.inputs["Base Color"])
    if some.alpha == "cutout":
        tree.links.new(tex.outputs["Alpha"], node.inputs["Alpha"])
        mat.surface_render_method = "DITHERED"
    if "normal" in images:
        nt = tree.nodes.new("ShaderNodeTexImage")
        nt.image = images["normal"]
        nm = tree.nodes.new("ShaderNodeNormalMap")
        tree.links.new(nt.outputs["Color"], nm.inputs["Color"])
        tree.links.new(nm.outputs["Normal"], node.inputs["Normal"])
    if "emission" in images:
        et = tree.nodes.new("ShaderNodeTexImage")
        et.image = images["emission"]
        tree.links.new(et.outputs["Color"], node.inputs["Emission Color"])
        node.inputs["Emission Strength"].default_value = some.strength
    return mat, rects, images


def build(objs, size=2048, name="KAT_atlas", folder="", loose=False):
    """Atlas every eligible material of `objs`. Returns (groups built, skipped [(material, reason)]).
    Each built group is a namespace with `name`, `materials`, `meshes` (names) and `images`."""
    groups, skipped = plan(objs, size, loose)
    if not groups:
        return [], skipped
    # every mesh's UVs are read once and written once, however many groups touch it
    meshes = {}
    for ob in objs:
        if ob.type == "MESH":
            meshes.setdefault(ob.data, ob)
    state = {}
    for mesh in meshes:
        uvs, loop_mat, _ = loop_arrays(mesh)
        if uvs is None:
            continue  # no UV map: nothing in it can be atlased
        poly_mat = np.empty(len(mesh.polygons), dtype=np.int32)
        mesh.polygons.foreach_get("material_index", poly_mat)
        state[mesh] = [uvs, loop_mat, poly_mat]
    built = []
    for number, g in enumerate(groups, start=1):
        label = name if len(groups) == 1 else f"{name}_{number}"
        mat, rects, images = build_group(g, size, label, folder)
        for mesh in g.meshes:
            ob = meshes[mesh]
            uvs, loop_mat, poly_mat = state[mesh]
            slot_of = {i: s.material.name for i, s in enumerate(ob.material_slots) if s.material}
            mesh.materials.append(mat)
            new_index = len(mesh.materials) - 1
            for i, mname in slot_of.items():
                if mname not in rects:
                    continue
                x, y, w, h = rects[mname]
                mask = loop_mat == i
                if mask.any():
                    uvs[mask, 0] = (x + PAD + uvs[mask, 0] * (w - 2 * PAD)) / size
                    uvs[mask, 1] = (y + PAD + uvs[mask, 1] * (h - 2 * PAD)) / size
                    loop_mat[mask] = new_index
                poly_mat[poly_mat == i] = new_index
        built.append(SimpleNamespace(name=label, materials=list(g.materials), meshes=[m.name for m in g.meshes],
                                     images=images))
    for mesh, (uvs, loop_mat, poly_mat) in state.items():
        mesh.uv_layers.active.data.foreach_set("uv", uvs.ravel())
        mesh.polygons.foreach_set("material_index", poly_mat)
        mesh.update()
    materials.remove_empty_slots([ob for ob in objs if ob.type == "MESH"])
    return built, skipped


class KAT_OT_atlas_scan(bpy.types.Operator):
    bl_idname = "kat.atlas_scan"
    bl_label = "Scan materials"
    bl_description = "List which materials can be combined into an atlas and why the others cannot. Nothing is changed"

    @operator_guard
    def execute(self, context):
        objs = scope_objects(context)
        if not objs:
            raise ValueError("Select meshes, or an armature with meshes on it")
        groups, skipped = plan(objs, loose=context.scene.kat.atlas_loose)
        last.update(groups=[(len(g.materials), len(g.meshes), g.key) for g in groups], skipped=skipped,
                    text=f"{sum(len(g.materials) for g in groups)} materials can be combined into {len(groups)} atlas(es)")
        self.report({"INFO"}, last["text"])
        return {"FINISHED"}


class KAT_OT_atlas(bpy.types.Operator):
    bl_idname = "kat.atlas"
    bl_label = "Combine materials"
    bl_description = ("Pack materials, across all the selected meshes (or the armature's), into one atlas texture and one "
                      "slot. Base colour, normal map, emission and cut-out transparency come along")
    bl_options = {"REGISTER", "UNDO"}
    size: EnumProperty(name="Atlas size", items=[(s, f"{s} px", "") for s in ("1024", "2048", "4096")], default="2048")

    @operator_guard
    def execute(self, context):
        objs = scope_objects(context)
        if not objs:
            raise ValueError("Select meshes, or an armature with meshes on it")
        folder = bpy.path.abspath("//") if bpy.data.filepath else ""
        stem = "".join(c if c.isalnum() or c in "-_ ." else "_" for c in (armature.rig_of(context) or objs[0]).name)
        built, skipped = build(objs, int(self.size), f"{stem}_atlas", folder, context.scene.kat.atlas_loose)
        for mname, reason in skipped:
            self.report({"WARNING"}, f"Left {mname} alone: {reason}")
        if not built:
            self.report({"INFO"}, "Fewer than two materials can be combined")
            last.update(groups=[], skipped=skipped, text="Nothing to combine")
            return {"CANCELLED"}
        where = "next to the .blend" if folder else "packed into the .blend (Export for Unity saves it as a PNG next to the FBX)"
        last.update(groups=[], skipped=skipped, text=f"Combined {sum(len(g.materials) for g in built)} materials into "
                                                      f"{len(built)} atlas(es), {where}")
        self.report({"INFO"}, last["text"])
        return {"FINISHED"}


classes = (KAT_OT_atlas_scan, KAT_OT_atlas)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
