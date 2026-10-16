# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Krislyz
"""Export for Unity: an FBX with settings that behave, plus a .kat.json the Unity package reads."""
import json
import os

import bpy
from bpy.props import StringProperty
from bpy_extras.io_utils import ExportHelper

from ..core import vault, visemes
from ..core.safe import operator_guard
from . import armature, face, outfits, textures

SIDECAR_VERSION = 3  # 2 added materialTextures, 3 skin, outfits and fits; everything from 1 is still there

FBX_OPTIONS = dict(
    use_selection=True,
    object_types={"ARMATURE", "MESH"},
    add_leaf_bones=False,  # leaf bones confuse Unity's humanoid mapping
    apply_scale_options="FBX_SCALE_ALL",
    bake_anim=False,
    mesh_smooth_type="FACE",
)


def fbx_options():
    """Only the options this Blender's exporter still has, so a renamed option cannot break the export."""
    known = bpy.ops.export_scene.fbx.get_rna_type().properties.keys()
    return {k: v for k, v in FBX_OPTIONS.items() if k in known}


def relative_path(file, folder):
    """`file` as a forward-slash path relative to `folder`; absolute when it lives on another drive."""
    try:
        return os.path.relpath(file, folder).replace("\\", "/")
    except ValueError:
        return file.replace("\\", "/")


def write_unsaved(meshes, folder, stem):
    """Images that exist only inside Blender (an atlas packed into a .blend that was never saved, generated textures)
    have no file for Unity to use. Save each one as a PNG in `<stem>_textures` next to the FBX.
    Returns {image name: file path}."""
    out, mats = {}, []
    for ob in meshes:
        mats += [s.material for s in ob.material_slots if s.material and s.material not in mats]
    safe = lambda name: "".join(c if c.isalnum() or c in "-_ ." else "_" for c in name)
    target = os.path.join(folder, safe(stem) + "_textures")
    for mat in mats:
        for image in textures.material_roles(mat).values():
            file = textures.file_of(image)
            if image.name in out or (file and os.path.isfile(file)) or not (image.has_data or image.packed_file):
                continue
            os.makedirs(target, exist_ok=True)
            path = os.path.join(target, safe(image.name) + ".png")
            copy = image.copy()  # the original keeps living in the .blend, packed
            try:
                copy.filepath_raw = path
                copy.file_format = "PNG"
                copy.save()
            finally:
                bpy.data.images.remove(copy)
            out[image.name] = path
    return out


def material_textures(meshes, folder, saved=None):
    """Per material: the image files behind its base colour, normal and emission, relative to the model's folder.
    `saved` maps the names of images that had no file to the PNGs write_unsaved made for them."""
    out, seen = [], set()
    for ob in meshes:
        for slot in ob.material_slots:
            mat = slot.material
            if mat is None or mat.name in seen:
                continue
            seen.add(mat.name)
            entries = []
            for role, image in textures.material_roles(mat).items():
                file = textures.file_of(image)
                if not (file and os.path.isfile(file)) and saved:
                    file = saved.get(image.name, "")
                if file and os.path.isfile(file):  # packed or generated images have nothing for Unity to point at
                    entries.append({"role": role, "path": relative_path(file, folder)})
            out.append({"name": mat.name, "textures": entries})
    return out


def sidecar(rig, meshes, folder="", saved=None):
    """Lists of small objects only: Unity's JsonUtility cannot read dictionaries."""
    best, _ = armature.map_bones(rig)
    shaped = [ob for ob in meshes if face.key_blocks(ob)]
    return {
        "kat": SIDECAR_VERSION,
        "blender": bpy.app.version_string,
        "humanoid": [{"key": key, "bone": bone} for key, bone in best.items()],  # Unity HumanBodyBones name -> bone in the FBX
        "meshes": [ob.name for ob in meshes],
        "blendshapes": [{"mesh": ob.name, "names": [b.name for b in face.key_blocks(ob)]} for ob in shaped],
        "visemes": [{"mesh": ob.name, "names": [n for n in visemes.NAMES if n in face.key_blocks(ob)]} for ob in shaped],
        "materials": sorted({s.material.name for ob in meshes for s in ob.material_slots if s.material}),
        "materialTextures": material_textures(meshes, folder, saved) if folder else [],
        **outfits.sidecar(bpy.context, meshes),
    }


def export_unity(context, rig, path):
    """Write `path` (.fbx) and `path` with .kat.json next to it. Returns both paths."""
    armature.check_ready(context, rig)
    meshes = armature.meshes_of(rig)
    left_out = armature.unreachable_meshes(rig)
    if left_out:
        raise ValueError("Hidden or excluded meshes would be missing from the export: "
                         + ", ".join(o.name for o in left_out))
    layer = context.view_layer
    was = [(ob, ob.select_get()) for ob in layer.objects]
    json_path = os.path.splitext(path)[0] + ".kat.json"
    folder = os.path.dirname(os.path.abspath(path))
    saved = write_unsaved(meshes, folder, os.path.splitext(os.path.basename(path))[0])
    # sidecar first: Unity may import the FBX the moment it lands, and only reads the sidecar on first import
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(sidecar(rig, meshes, folder, saved), f, indent=2, ensure_ascii=False)
    try:
        for ob in layer.objects:
            ob.select_set(False)
        for ob in [rig, *meshes]:
            ob.select_set(True)
        stuck = [ob.name for ob in [rig, *meshes] if not ob.select_get()]
        if stuck:
            raise ValueError("Could not select for export: " + ", ".join(stuck))
        bpy.ops.export_scene.fbx(filepath=path, **fbx_options())
    except BaseException:
        if os.path.exists(json_path):
            os.remove(json_path)
        raise
    finally:
        for ob, state in was:
            ob.select_set(state)
    return path, json_path


class KAT_OT_export_unity(bpy.types.Operator, ExportHelper):
    bl_idname = "kat.export_unity"
    bl_label = "Export for Unity"
    bl_description = "Export the rig and its meshes as an FBX plus a .kat.json for the KAT Unity package"
    filename_ext = ".fbx"
    filter_glob: StringProperty(default="*.fbx", options={"HIDDEN"})

    def invoke(self, context, event):
        rig = armature.rig_of(context)
        if rig is None:
            self.report({"ERROR"}, "Select an armature, or a mesh rigged to one")
            return {"CANCELLED"}
        self.filepath = rig.name + ".fbx"
        return super().invoke(context, event)

    @operator_guard
    def execute(self, context):
        rig = armature.rig_of(context)
        if rig is None:
            raise ValueError("Select an armature, or a mesh rigged to one")
        fbx, meta = export_unity(context, rig, self.filepath)
        # called from Python, so Blender does not remember it as a file export
        vault.record(fbx, "export", textures=textures.files_of(armature.meshes_of(rig)))
        self.report({"INFO"}, f"Wrote {os.path.basename(fbx)} and {os.path.basename(meta)}")
        return {"FINISHED"}


classes = (KAT_OT_export_unity,)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
