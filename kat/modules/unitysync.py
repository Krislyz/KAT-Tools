# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Krislyz
"""Texture sync with Unity. The KAT Unity package writes <model>.kat-unity.json next to the model: for each renderer
and material slot, which texture assets the Unity material really uses. This reads it and points the Blender
materials at the same files, so what you see here is what Unity shows."""
import json
import logging
import os
from collections import namedtuple

import bpy
from bpy.props import StringProperty

from ..core.safe import operator_guard
from . import armature, textures

log = logging.getLogger("kat")

SUFFIX = ".kat-unity.json"
Result = namedtuple("Result", "applied missing file")
NOTHING = Result(0, [], "")


def sync_file_for(model_path):
    return os.path.splitext(model_path)[0] + SUFFIX


def load(path):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and isinstance(data.get("renderers"), list) else None


def project_root(data, model_path):
    """The Unity project folder: what the file says, or the folder above the nearest 'Assets' above the model."""
    given = data.get("project", "")
    if given and os.path.isdir(given):
        return given
    folder = os.path.dirname(os.path.abspath(model_path))
    while True:
        parent = os.path.dirname(folder)
        if os.path.basename(folder) == "Assets":
            return parent
        if parent == folder:
            return ""
        folder = parent


def resolve(project, asset_path):
    """Absolute path of a Unity asset ('Assets/Foo/bar.png'), or '' when it is not on disk."""
    file = os.path.normpath(os.path.join(project, asset_path)) if project else ""
    return file if file and os.path.isfile(file) else ""


def _set_image(tree, socket, image, colour_space, make_normal_map=False):
    """Make `socket` show `image`: reuse the image node already feeding it, or add one."""
    existing = textures.upstream_image(socket) if socket.is_linked else None
    if existing is not None:
        for node in tree.nodes:
            if node.type == "TEX_IMAGE" and node.image == existing:
                node.image = image
                image.colorspace_settings.name = colour_space
                return
    tex = tree.nodes.new("ShaderNodeTexImage")
    tex.image = image
    image.colorspace_settings.name = colour_space
    owner = socket.node
    tex.location = (owner.location.x - 640, owner.location.y - 280 * (len(textures.image_nodes_of(tree)) - 1))
    if make_normal_map:
        nmap = tree.nodes.new("ShaderNodeNormalMap")
        nmap.location = (owner.location.x - 300, owner.location.y - 300)
        tree.links.new(tex.outputs["Color"], nmap.inputs["Color"])
        tree.links.new(nmap.outputs["Normal"], socket)
    else:
        tree.links.new(tex.outputs["Color"], socket)


def _set_alpha(tree, node, mode):
    """Opaque Unity materials ignore the alpha of their texture (it is often a mask, not see-through), so the image's
    alpha is switched off or Blender would show holes. Cutout and transparent ones use it, so it is wired to Alpha."""
    socket = node.inputs["Base Color"]
    image = textures.upstream_image(socket) if socket.is_linked else None
    if image is None:
        return
    if mode == "opaque":
        image.alpha_mode = "NONE"
        return
    image.alpha_mode = "STRAIGHT"
    tex = next((n for n in tree.nodes if n.type == "TEX_IMAGE" and n.image == image), None)
    if tex is not None and not node.inputs["Alpha"].is_linked:
        tree.links.new(tex.outputs["Alpha"], node.inputs["Alpha"])


def apply(objects, data, model_path=""):
    """Point the materials of `objects` at the textures `data` lists. Matching is by renderer name and slot number,
    so it still works when a Unity material is named differently from the FBX material."""
    project = project_root(data, model_path) if model_path else data.get("project", "")
    by_name = {ob.name: ob for ob in objects if ob.type == "MESH"}
    applied, missing = 0, []
    for renderer in data["renderers"]:
        ob = by_name.get(renderer.get("name"))
        if ob is None:
            continue
        if len(renderer.get("slots", [])) != len(ob.material_slots):
            continue  # joined or atlased since Unity wrote the file: the slot numbers no longer mean the same materials
        for index, slot in enumerate(renderer.get("slots", [])):
            if index >= len(ob.material_slots) or ob.material_slots[index].material is None:
                continue
            mat = ob.material_slots[index].material
            if mat.get("kat_atlas"):
                continue  # KAT made this one; Unity has never heard of it
            node = textures.principled(mat)
            if node is None:
                continue
            mode = slot.get("mode", "")
            for entry in slot.get("textures", []):
                role, asset = entry.get("role"), entry.get("path", "")
                socket = {"baseColor": node.inputs["Base Color"], "normal": node.inputs["Normal"],
                          "emission": node.inputs["Emission Color"]}.get(role)
                if socket is None:
                    continue
                file = resolve(project, asset)
                if not file:
                    missing.append(asset)
                    continue
                image = bpy.data.images.load(file, check_existing=True)
                _set_image(mat.node_tree, socket, image, "Non-Color" if role == "normal" else "sRGB",
                           make_normal_map=role == "normal")
                if role == "emission" and node.inputs["Emission Strength"].default_value == 0.0:
                    node.inputs["Emission Strength"].default_value = 1.0  # Blender's default is 0, which would hide the map
                applied += 1
            if mode:
                _set_alpha(mat.node_tree, node, mode)
    textures.activate_image_nodes(textures.materials_of(by_name.values()))
    return Result(applied, sorted(set(missing)), "")


def sync_for_import(path, objects):
    """Called after Import model: use the Unity file beside the model, if there is one."""
    file = sync_file_for(path)
    data = load(file) if os.path.isfile(file) else None
    if data is None:
        return NOTHING
    result = apply(objects, data, path)
    return result._replace(file=file)


def summary(result):
    if not result.file:
        return ""
    text = f"Unity textures: {result.applied} linked"
    if result.missing:
        text += f", {len(result.missing)} not on disk (e.g. {os.path.basename(result.missing[0])})"
    return text


class KAT_OT_sync_unity(bpy.types.Operator):
    bl_idname = "kat.sync_unity"
    bl_label = "Sync textures from Unity"
    bl_description = "Point this model's materials at the textures its Unity materials use (reads the .kat-unity.json file)"
    bl_options = {"REGISTER", "UNDO"}
    filepath: StringProperty(subtype="FILE_PATH")
    filter_glob: StringProperty(default="*" + SUFFIX, options={"HIDDEN"})

    def invoke(self, context, event):
        source = self.source(context)
        if source and os.path.isfile(sync_file_for(source)):
            self.filepath = sync_file_for(source)
            return self.execute(context)
        context.window_manager.fileselect_add(self)  # no file next to the model: ask where it is
        return {"RUNNING_MODAL"}

    @staticmethod
    def source(context):
        for ob in (context.active_object, armature.rig_of(context)):
            if ob is not None and ob.get("kat_source"):
                return ob["kat_source"]
        return ""

    @operator_guard
    def execute(self, context):
        data = load(self.filepath)
        if data is None:
            raise ValueError("That is not a KAT Unity sync file")
        rig = armature.rig_of(context)
        objects = armature.meshes_of(rig) if rig else [o for o in context.selected_objects if o.type == "MESH"]
        if not objects:
            raise ValueError("Select a rigged model or some meshes")
        result = apply(objects, data, self.source(context) or self.filepath)
        self.report({"INFO"}, summary(result._replace(file=self.filepath)))
        if result.missing:
            self.report({"WARNING"}, "Some textures are not on disk: " + ", ".join(os.path.basename(m) for m in result.missing[:3]))
        return {"FINISHED"}


classes = (KAT_OT_sync_unity,)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
