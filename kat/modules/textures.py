# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Krislyz
"""Find missing textures. A model remembers which files it wanted even when they weren't where it looked,
so we search near the model for those names (and, for materials with no texture at all, for the material's name)."""
import logging
import os
import time
from collections import namedtuple

import bpy
from bpy.app.handlers import persistent
from bpy.props import StringProperty

from ..core import prefs, texsearch
from ..core.safe import operator_guard
from . import armature

log = logging.getLogger("kat")

Result = namedtuple("Result", "fixed created unresolved scanned seconds")
NOTHING = Result(0, 0, [], 0, 0.0)


def materials_of(objects):
    seen = []
    for ob in objects:
        for slot in ob.material_slots:
            if slot.material and slot.material not in seen:
                seen.append(slot.material)
    return seen


def image_nodes_of(tree):
    return [n for n in tree.nodes if n.type == "TEX_IMAGE"]


def image_nodes(mat):
    return image_nodes_of(mat.node_tree) if mat.node_tree else []


def principled(mat):
    return next((n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None) if mat.node_tree else None


def file_of(img):
    return bpy.path.abspath(img.filepath, library=img.library) if img.filepath else ""


def files_of(objects):
    """Every image file on disk that the objects' materials use, once each. What a version snapshot has to keep."""
    out = []
    for mat in materials_of(objects):
        for node in image_nodes(mat):
            img = node.image
            if img is not None and img.source == "FILE" and not img.packed_file:
                path = file_of(img)
                if path and os.path.isfile(path) and path not in out:
                    out.append(path)
    return out


def is_missing(img):
    return img.source == "FILE" and not img.packed_file and not os.path.isfile(file_of(img))


def is_glass(mat):
    """A material that is see-through on purpose (glass, a visor, a lens): transmission, or an alpha below 1, with nothing
    plugged into either. It has no texture and must not be given one."""
    node = principled(mat)
    if node is None:
        return False
    for name, clear in (("Transmission Weight", lambda v: v > 0.01), ("Alpha", lambda v: v < 0.99)):
        socket = node.inputs.get(name)
        if socket is not None and not socket.is_linked and clear(socket.default_value):
            return True
    return "glass" in mat.name.lower()


def bare_materials(mats):
    """Materials that have a shader but no image at all behind their base colour. Glass is left out: no texture belongs on it."""
    out = []
    for mat in mats:
        node = principled(mat)
        if node is None or node.inputs["Base Color"].is_linked or any(n.image for n in image_nodes(mat)) or is_glass(mat):
            continue
        out.append(mat)
    return out


def base_colour_image_node(mat, depth=6):
    """The image node that feeds the Principled base colour, following links back through colour mixes.
    Falls back to the first image node that has an image."""
    node = principled(mat)
    wanted = None
    if node is not None and node.inputs["Base Color"].is_linked:
        todo = [(node.inputs["Base Color"].links[0].from_node, 0)]
        while todo and wanted is None:
            current, level = todo.pop(0)
            if current.type == "TEX_IMAGE" and current.image:
                wanted = current
            elif level < depth:
                todo += [(link.from_node, level + 1) for sock in current.inputs for link in sock.links]
    return wanted or next((n for n in image_nodes(mat) if n.image), None)


def upstream_image(socket, depth=6):
    """The first image found by following links back from `socket`, through colour mixes, normal map nodes and so on."""
    todo = [(link.from_node, 0) for link in socket.links]
    while todo:
        node, level = todo.pop(0)
        if node.type == "TEX_IMAGE" and node.image:
            return node.image
        if level < depth:
            todo += [(link.from_node, level + 1) for sock in node.inputs for link in sock.links]
    return None


def material_roles(mat):
    """{'baseColor' | 'normal' | 'emission': Image} for the images a Principled material really uses."""
    node = principled(mat)
    if node is None:
        return {}
    roles = {}
    for role, name in (("baseColor", "Base Color"), ("normal", "Normal"), ("emission", "Emission Color")):
        socket = node.inputs.get(name)
        image = upstream_image(socket) if socket is not None and socket.is_linked else None
        if image is not None:
            roles[role] = image
    if "baseColor" not in roles and not is_glass(mat):  # glass has no texture: a stray image node is not one
        fallback = base_colour_image_node(mat)
        if fallback is not None:
            roles["baseColor"] = fallback.image
    return roles


def activate_image_nodes(mats):
    """Solid view in Texture colour shows each material's active image node, and importers leave the Material
    Output active, so textures would show as plain grey. Returns how many materials were set."""
    count = 0
    for mat in mats:
        node = base_colour_image_node(mat) if mat.node_tree else None
        active = mat.node_tree.nodes.active if node is not None else None
        if node is not None and (active is None or active.name != node.name):  # wrappers differ per access, so compare names
            mat.node_tree.nodes.active = node
            count += 1
    return count


def find_textures(objects, roots, seconds=8.0):
    """Relink missing images and give bare materials a base colour image. Does nothing, and scans
    nothing, when nothing is missing."""
    mats = materials_of(objects)
    activate_image_nodes(mats)  # so the Texture view has something to show, whether or not anything was missing
    missing = []
    for mat in mats:
        for node in image_nodes(mat):
            if node.image and is_missing(node.image) and node.image not in missing:
                missing.append(node.image)
    bare = bare_materials(mats)
    if not missing and not bare:
        return NOTHING
    start = time.monotonic()
    paths = texsearch.scan(roots, seconds)
    fixed, created, unresolved = 0, 0, []
    for img in missing:
        hit = texsearch.match_reference(img.filepath or img.name, paths)
        if hit:
            img.filepath = hit
            img.reload()
            fixed += img.size[0] > 0
            if img.size[0] == 0:
                unresolved.append(os.path.basename(hit))
        else:
            unresolved.append(os.path.basename((img.filepath or img.name).replace("\\", "/")))
    for mat in bare:
        hit = texsearch.match_material(mat.name, paths)
        if hit:
            node = principled(mat)
            tex = mat.node_tree.nodes.new("ShaderNodeTexImage")
            tex.image = bpy.data.images.load(hit, check_existing=True)
            tex.location = (node.location.x - 340, node.location.y)
            mat.node_tree.links.new(tex.outputs["Color"], node.inputs["Base Color"])
            created += 1
    activate_image_nodes(mats)
    return Result(fixed, created, unresolved, len(paths), time.monotonic() - start)


def search_roots(model_path="", extra=""):
    roots = [extra] if extra else []
    if model_path:
        folder = os.path.dirname(os.path.abspath(model_path))
        roots += [folder, os.path.dirname(folder)]
    if bpy.data.filepath:
        roots.append(os.path.dirname(bpy.data.filepath))
    return roots


def scene_meshes(context):
    """Every mesh in the scene: the model, an outfit that was just imported next to it, a prop."""
    return [o for o in context.scene.objects if o.type == "MESH"]


_missing_cache = {"at": 0.0, "key": None, "count": 0}


def missing_count(context):
    """How many textures the scene's materials ask for but cannot find. Cached for a couple of seconds: panels ask on every redraw."""
    now = time.monotonic()
    key = (id(context.scene), len(bpy.data.images), len(bpy.data.materials))
    if _missing_cache["key"] != key or now - _missing_cache["at"] > 2.0:
        _missing_cache.update(at=now, key=key, count=len(missing_images(scene_meshes(context))))
    return _missing_cache["count"]


def missing_images(objects):
    out = []
    for mat in materials_of(objects):
        for node in image_nodes(mat):
            if node.image and is_missing(node.image) and node.image not in out:
                out.append(node.image)
    return out


def auto_find(objects=None, seconds=4.0, extra=""):
    """Look for missing textures without being asked: next to where each model was imported from, next to the .blend,
    and in the folder the images were last known to be in. Quiet when nothing is missing."""
    objects = [o for o in (objects or bpy.data.objects) if o.type == "MESH"]
    if not missing_images(objects):
        return NOTHING
    roots = search_roots(extra=extra)
    for ob in objects:
        source = ob.get("kat_source")
        if source:
            roots += [r for r in search_roots(source) if r not in roots]
    for img in missing_images(objects):
        folder = os.path.dirname(file_of(img))
        for up in (folder, os.path.dirname(folder)):
            if up and os.path.isdir(up) and up not in roots:
                roots.append(up)
    return find_textures(objects, roots, seconds)


@persistent
def _on_load(*_):
    """After a .blend opens: relink what is missing, once the window is up."""
    if bpy.app.background:  # no window, nobody to look at it: scripts and render farms are left alone
        return

    def later():
        try:
            if prefs.get().load_find_textures:
                auto_find()
        except Exception:  # never let a convenience break opening a file
            log.exception("kat: texture search after load failed")
        return None
    bpy.app.timers.register(later, first_interval=1.0)


def summary(result):
    if result is NOTHING or not (result.fixed or result.created or result.unresolved):
        return ""
    text = f"textures: {result.fixed} relinked"
    if result.created:
        text += f", {result.created} matched by material name"
    if result.unresolved:
        text += f", not found: {', '.join(result.unresolved[:3])}" + (" ..." if len(result.unresolved) > 3 else "")
    return text + f" ({result.seconds:.1f}s)"


class KAT_OT_find_textures(bpy.types.Operator):
    bl_idname = "kat.find_textures"
    bl_label = "Find missing textures"
    bl_description = ("Search near the models and in a folder you pick for the textures the materials ask for but cannot find "
                      "(they show purple), and put them back on every mesh in the scene, outfits you just imported too")
    bl_options = {"REGISTER", "UNDO"}
    directory: StringProperty(subtype="DIR_PATH")

    def invoke(self, context, event):
        # try on its own first; only when something is still missing is a folder asked for
        objects = scene_meshes(context)
        if objects:
            result = auto_find(objects)
            if not missing_images(objects):
                self.report({"INFO"}, summary(result) or "No missing textures")
                return {"FINISHED"}
        context.window_manager.fileselect_add(self)  # pick the folder to search, usually where the textures live
        return {"RUNNING_MODAL"}

    @operator_guard
    def execute(self, context):
        objects = scene_meshes(context)
        if not objects:
            raise ValueError("There are no meshes in this scene")
        _missing_cache["key"] = None
        result = find_textures(objects, search_roots(extra=self.directory), seconds=8.0)
        self.report({"INFO"}, summary(result) or "No missing textures")
        return {"FINISHED"}


classes = (KAT_OT_find_textures,)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    if _on_load not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_on_load)


def unregister():
    if _on_load in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_on_load)
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
