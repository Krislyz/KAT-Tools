# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Krislyz
"""Import model: pick any model file, KAT chooses the importer, tidies an untouched default scene,
switches the viewport to flat texture shading and looks for textures the model could not find."""
import logging
import os
from collections import namedtuple

import bpy
from bpy.props import StringProperty

from ..core import formats, prefs, vault
from ..core.safe import operator_guard
from . import face, textures, unitysync

log = logging.getLogger("kat")

DEFAULT_KINDS = {"MESH": "meshes", "CAMERA": "cameras", "LIGHT": "lights"}
ImportResult = namedtuple("ImportResult", "new cleared textures flat_view unity")


def _is_default(ob):
    if ob.type == "MESH":
        return ob.name == "Cube" and len(ob.data.vertices) == 8 and len(ob.data.polygons) == 6
    return ob.type in ("CAMERA", "LIGHT") and ob.name == ob.type.capitalize()


def default_scene_objects(scene):
    """The startup Cube, Camera and Light, when they are all the scene holds. Empty list otherwise,
    so loading a model on top of something already there never deletes anything."""
    objects = list(scene.objects)
    return objects if objects and all(_is_default(ob) for ob in objects) else []


def remove_objects(objects):
    """Delete the objects and the mesh, camera, light and material data only they used."""
    data = [(DEFAULT_KINDS[ob.type], ob.data) for ob in objects]
    materials = [m for ob in objects if ob.type == "MESH" for m in ob.data.materials if m]
    for ob in objects:
        bpy.data.objects.remove(ob, do_unlink=True)
    for kind, block in data:
        if block.users == 0:
            getattr(bpy.data, kind).remove(block)
    for mat in materials:
        if mat.users == 0:
            bpy.data.materials.remove(mat)


def select_only(context, new):
    """Leave one thing selected and active, the armature if there is one. Selection colours would hide the textures."""
    target = next((ob for ob in new if ob.type == "ARMATURE"), new[0])
    for ob in context.view_layer.objects:
        ob.select_set(False)
    target.select_set(True)
    context.view_layer.objects.active = target
    return target


def apply_flat_view(context, new):
    """Frame the new model, then Solid shading with Flat lighting and Texture colour, which is what shows textures
    honestly. Returns whether any 3D viewport was changed (there are none when running without a window)."""
    for ob in new:
        ob.select_set(True)
    changed = False
    for win in context.window_manager.windows:
        if win.screen is None:
            continue
        for area in win.screen.areas:
            if area.type != "VIEW_3D":
                continue
            shading = area.spaces.active.shading
            shading.type = "SOLID"
            shading.light = "FLAT"
            shading.color_type = "TEXTURE"
            region = next((r for r in area.regions if r.type == "WINDOW"), None)
            if region is not None:
                with context.temp_override(window=win, area=area, region=region):
                    bpy.ops.view3d.view_selected()
            changed = True
    return changed


def pick_face_mesh(context, new):
    """Pre-select the face mesh in the Face panel: the one called Body, else the mesh with the most shape keys."""
    options = getattr(context.scene, "kat", None)
    ob = face.guess_face_mesh(new)
    if options is not None and ob is not None:
        options.face_mesh = ob


def import_file(context, path):
    """Import `path`. Returns an ImportResult: new objects, how many default-scene objects were removed,
    the texture search result (or None) and whether the viewport was switched to flat texture view."""
    if not os.path.isfile(path):
        raise ValueError(f"No such file: {path}")
    ext = os.path.splitext(path)[1].lower()
    importer = formats.find_importer(ext)
    if importer is None:
        known = ", ".join(formats.importable_extensions())
        raise ValueError(f"This Blender cannot import {ext or 'that'} files. It can import: {known}")
    scene = context.scene
    junk = default_scene_objects(scene)
    before = set(scene.objects)
    importer(filepath=path)
    new = [ob for ob in scene.objects if ob not in before]
    if not new:
        raise ValueError(f"The importer added nothing from {os.path.basename(path)}")
    remove_objects(junk)  # only now: a failed import must not leave the user with an empty scene
    for ob in new:
        ob["kat_source"] = os.path.abspath(path)  # lets "Sync textures from Unity" find the file later

    # Nice-to-haves. None of them may ever turn a successful import into a failure.
    p = prefs.get()
    found, flat = None, False
    if p.import_find_textures:
        try:
            found = textures.find_textures([ob for ob in new if ob.type == "MESH"], textures.search_roots(path), 8.0)
        except Exception:
            log.exception("kat: texture search failed")
    synced = unitysync.NOTHING
    if p.import_unity_sync:
        try:
            synced = unitysync.sync_for_import(path, [ob for ob in new if ob.type == "MESH"])
        except Exception:
            log.exception("kat: Unity texture sync failed")
    if p.import_flat_view:
        try:
            textures.activate_image_nodes(textures.materials_of([ob for ob in new if ob.type == "MESH"]))
            flat = apply_flat_view(context, new)
        except Exception:
            log.exception("kat: could not switch the viewport")
    try:  # after the texture steps, so the snapshot holds the textures the model really ended up with
        # called from Python, so Blender does not remember it as a file import
        vault.record(path, "import", textures=textures.files_of([ob for ob in new if ob.type == "MESH"]))
    except Exception:
        log.exception("kat: could not record the import in the version history")
    select_only(context, new)  # last, so framing could see every new object
    try:
        pick_face_mesh(context, new)
    except Exception:
        log.exception("kat: could not pick the face mesh")
    return ImportResult(new, len(junk), found, flat, synced)


class KAT_OT_import_model(bpy.types.Operator):
    bl_idname = "kat.import_model"
    bl_label = "Import model"
    bl_description = ("Choose a model file (FBX, glTF/GLB, OBJ, USD and more). A fresh default scene is cleared, "
                      "the view switches to flat textures and missing textures are searched for")
    bl_options = {"REGISTER", "UNDO"}
    filepath: StringProperty(subtype="FILE_PATH")
    filter_glob: StringProperty(default="", options={"HIDDEN"})

    def invoke(self, context, event):
        self.filter_glob = ";".join("*" + ext for ext in formats.importable_extensions())
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    @operator_guard
    def execute(self, context):
        result = import_file(context, self.filepath)
        notes = [f"Imported {os.path.basename(self.filepath)}"]
        if result.cleared:
            notes.append("cleared the default scene")
        found = textures.summary(result.textures) if result.textures else ""
        if found:
            notes.append(found)
        if result.unity.file:
            notes.append(unitysync.summary(result.unity))
        self.report({"INFO"}, ", ".join(notes))
        if result.textures and result.textures.unresolved:
            self.report({"WARNING"}, "Some textures were not found. Press Pick the textures folder in Quick access")
        return {"FINISHED"}


classes = (KAT_OT_import_model,)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
