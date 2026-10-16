# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Krislyz
"""Texture optimizer: see what the textures cost and shrink the big ones, without touching the original files.

Shrunk copies are saved next to the originals as name_512.png and the materials are pointed at them. Unity compresses
textures whose sides are powers of two, so by default the copies are rounded to powers of two.
"""
import os

import bpy
import numpy as np

from ..core import vrc
from ..core.safe import operator_guard
from . import armature, atlas, textures, uiutil

SIZES = [(str(s), f"{s} px", "") for s in (256, 512, 1024, 2048, 4096)]
last = {"text": ""}


def scope_objects(context):
    chosen = [o for o in context.selected_objects if o.type == "MESH"]
    if chosen:
        return chosen
    rig = armature.rig_of(context)
    return armature.meshes_of(rig) if rig else []


def used_images(objs):
    """Images (file or generated, 8-bit or float) used by the materials of `objs`, once each, biggest first."""
    seen = {}
    for mat in textures.materials_of(objs):
        for node in textures.image_nodes(mat):
            img = node.image
            if img is not None and img.size[0] > 0:
                seen[img.name] = img
    return sorted(seen.values(), key=lambda i: -i.size[0] * i.size[1])


def megabytes(images):
    return vrc.texture_mb([tuple(i.size) for i in images])


def power_of_two(n):
    """The power of two nearest n (never below 1)."""
    lo = 1 << (max(1, int(n)).bit_length() - 1)
    return lo if n - lo < 2 * lo - n else 2 * lo


def target_size(width, height, limit, pot):
    scale = min(1.0, limit / max(width, height))
    w, h = max(1, round(width * scale)), max(1, round(height * scale))
    if pot:
        w, h = min(limit, power_of_two(w)), min(limit, power_of_two(h))
    return w, h


def shrink(images, limit, pot=True):
    """Make smaller copies of the images bigger than `limit` (or not a power of two when `pot`), save them next to
    the originals when those are files, and point every node at the copy. Returns [(old name, new name, old, new)]."""
    done = []
    for img in images:
        w, h = img.size
        nw, nh = target_size(w, h, limit, pot)
        if (nw, nh) == (w, h) or img.is_float or img.source not in {"FILE", "GENERATED"}:
            continue
        px = atlas.image_pixels(img)
        small = atlas.resize(px, nh, nw)
        stem = os.path.splitext(img.name)[0]
        new = bpy.data.images.new(f"{stem}_{max(nw, nh)}", nw, nh, alpha=True)
        new.colorspace_settings.name = img.colorspace_settings.name
        new.alpha_mode = img.alpha_mode
        new.pixels.foreach_set(small.astype(np.float32).ravel())
        saved = False
        original = textures.file_of(img) if img.source == "FILE" else ""
        if original and os.path.isfile(original):
            target = os.path.join(os.path.dirname(original), f"{os.path.splitext(os.path.basename(original))[0]}_{max(nw, nh)}.png")
            try:
                new.filepath_raw = target
                new.file_format = "PNG"
                new.save()
                saved = new.source == "FILE"
            except (RuntimeError, OSError):
                saved = False
        if not saved:
            new.pack()
        for mat in bpy.data.materials:
            for node in textures.image_nodes(mat):
                if node.image == img:
                    node.image = new
        done.append((img.name, new.name, (w, h), (nw, nh)))
    return done


def unused_images():
    return [i for i in bpy.data.images if i.users == 0 and i.name not in {"Render Result", "Viewer Node"}]


class KAT_OT_textures_shrink(bpy.types.Operator):
    bl_idname = "kat.textures_shrink"
    bl_label = "Shrink textures"
    bl_description = ("Make smaller copies of the big textures of the selected meshes (or the armature's) and use them. "
                      "The original files are not touched")
    bl_options = {"REGISTER", "UNDO"}

    @operator_guard
    def execute(self, context):
        o = context.scene.kat
        objs = scope_objects(context)
        if not objs:
            raise ValueError("Select meshes, or an armature with meshes on it")
        images = used_images(objs)
        before = megabytes(images)
        done = shrink(images, int(o.tex_limit), o.tex_pot)
        if not done:
            self.report({"INFO"}, "No texture is above the limit")
            last["text"] = "Nothing to shrink"
            return {"CANCELLED"}
        after = megabytes(used_images(objs))
        last["text"] = f"{len(done)} textures shrunk, about {before:.0f} MB to {after:.0f} MB"
        self.report({"INFO"}, last["text"])
        return {"FINISHED"}


class KAT_OT_textures_unused(bpy.types.Operator):
    bl_idname = "kat.textures_unused"
    bl_label = "Remove unused images"
    bl_description = "Delete images that nothing uses from this file. The files on disk stay"
    bl_options = {"REGISTER", "UNDO"}

    @operator_guard
    def execute(self, context):
        doomed = unused_images()
        for img in doomed:
            bpy.data.images.remove(img)
        self.report({"INFO"}, f"Removed {len(doomed)} unused images")
        return {"FINISHED"}


class KAT_PT_textures(bpy.types.Panel):
    bl_label = "Textures"
    bl_idname = "KAT_PT_textures"
    bl_parent_id = "KAT_PT_optimize_auto"
    bl_order = 1033
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "KAT"
    bl_options = {"DEFAULT_CLOSED"}

    def draw_header(self, context):
        self.layout.label(icon="TEXTURE_DATA")

    def draw(self, context):
        o = context.scene.kat
        layout = self.layout
        objs = scope_objects(context)
        if not objs:
            uiutil.paragraph(layout, context, "Select meshes, or a rigged model.", "INFO")
            return
        images = used_images(objs)
        total = megabytes(images)
        rank = vrc.rank_of("texture_mb", total)
        row = layout.row()
        row.alert = rank >= 3
        row.label(text=f"{len(images)} textures, about {total:.0f} MB, {vrc.RANKS[rank]}", icon="TEXTURE")
        col = layout.column(align=True)
        for img in images[:6]:
            w, h = img.size
            odd = (w & (w - 1)) or (h & (h - 1))
            row = col.row()
            row.alert = bool(odd)
            row.label(text=f"{img.name}  {w}x{h}" + ("  not a power of two" if odd else ""), icon="IMAGE_DATA")
        if len(images) > 6:
            col.label(text=f"... and {len(images) - 6} more")
        box = layout.box()
        box.prop(o, "tex_limit", text="Largest")
        box.prop(o, "tex_pot")
        row = box.row()
        row.scale_y = 1.2
        row.operator("kat.textures_shrink", icon="FULLSCREEN_EXIT")
        if last["text"]:
            box.label(text=last["text"], icon="INFO")
        layout.operator("kat.textures_unused", icon="TRASH")
        col = layout.column(align=True)
        col.operator("kat.find_textures", icon="TEXTURE_DATA")
        col.operator("kat.sync_unity", icon="FILE_REFRESH")


classes = (KAT_OT_textures_shrink, KAT_OT_textures_unused, KAT_PT_textures)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
