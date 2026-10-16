# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Krislyz
"""Export for Unity: an FBX with settings that behave, plus a .kat.json the Unity package reads."""
import json
import os
import shutil

import bpy
from bpy.props import BoolProperty, EnumProperty, StringProperty
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


def fbx_options(apply_modifiers=True, triangulate=False, embed_textures=False, add_leaf_bones=False, smoothing="FACE"):
    """Only the options this Blender's exporter still has, so a renamed option cannot break the export."""
    wanted = dict(FBX_OPTIONS)
    wanted.update(use_mesh_modifiers=apply_modifiers, use_triangles=triangulate, add_leaf_bones=add_leaf_bones,
                  mesh_smooth_type=smoothing)
    if embed_textures:
        wanted.update(path_mode="COPY", embed_textures=True)
    known = bpy.ops.export_scene.fbx.get_rna_type().properties.keys()
    return {k: v for k, v in wanted.items() if k in known}


def unity_project_root(folder):
    """The Unity project a folder is in (the nearest parent with Assets and ProjectSettings), or ''."""
    here = os.path.abspath(folder)
    while True:
        if os.path.isdir(os.path.join(here, "Assets")) and os.path.isdir(os.path.join(here, "ProjectSettings")):
            return here
        parent = os.path.dirname(here)
        if parent == here:
            return ""
        here = parent


def inside(file, folder):
    try:
        return os.path.commonpath([os.path.abspath(file), os.path.abspath(folder)]) == os.path.abspath(folder)
    except ValueError:  # another drive
        return False


def relative_path(file, folder):
    """`file` as a forward-slash path relative to `folder`; absolute when it lives on another drive."""
    try:
        return os.path.relpath(file, folder).replace("\\", "/")
    except ValueError:
        return file.replace("\\", "/")


def copy_textures(meshes, target, outside_only, project_root):
    """Copy the texture files the materials use into `target`. With `outside_only` the ones that are already inside the
    Unity project's Assets folder stay where they are: those are what Unity already has. Returns {image name: new path}."""
    out, taken = {}, set()
    mats = []
    for ob in meshes:
        mats += [s.material for s in ob.material_slots if s.material and s.material not in mats]
    for mat in mats:
        for image in textures.material_roles(mat).values():
            file = textures.file_of(image)
            if image.name in out or not (file and os.path.isfile(file)):
                continue
            if outside_only and project_root and inside(file, os.path.join(project_root, "Assets")):
                continue
            os.makedirs(target, exist_ok=True)
            name, ext = os.path.splitext(os.path.basename(file))
            path, n = os.path.join(target, name + ext), 1
            while path.lower() in taken:  # two different files with one name: keep both
                n += 1
                path = os.path.join(target, f"{name}_{n}{ext}")
            taken.add(path.lower())
            if not (os.path.isfile(path) and os.path.samefile(path, file)):
                shutil.copy2(file, path)
            out[image.name] = path
    return out


def write_unsaved(meshes, folder, stem, target=""):
    """Images that exist only inside Blender (an atlas packed into a .blend that was never saved, generated textures)
    have no file for Unity to use. Save each one as a PNG in `target` (default `<stem>_textures` next to the FBX).
    Returns {image name: file path}."""
    out, mats = {}, []
    for ob in meshes:
        mats += [s.material for s in ob.material_slots if s.material and s.material not in mats]
    safe = lambda name: "".join(c if c.isalnum() or c in "-_ ." else "_" for c in name)
    target = target or os.path.join(folder, safe(stem) + "_textures")
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
                file = saved.get(image.name, "") if saved and image.name in saved else textures.file_of(image)
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


def export_unity(context, rig, path, sidecar_file=True, textures_mode="NONE", textures_folder="", **fbx_settings):
    """Write `path` (.fbx) and `path` with .kat.json next to it. Returns both paths (the second is '' without the sidecar).
    `textures_mode`: NONE (only images that exist nowhere but in Blender are written), OUTSIDE (also copy the texture
    files that are not in the Unity project yet) or ALL, into `textures_folder` (default `<name>_textures` next to the FBX).
    `fbx_settings`: apply_modifiers, triangulate, embed_textures, add_leaf_bones, smoothing."""
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
    stem = os.path.splitext(os.path.basename(path))[0]
    target = os.path.abspath(bpy.path.abspath(textures_folder)) if textures_folder else ""
    saved = write_unsaved(meshes, folder, stem, target)
    if textures_mode in ("OUTSIDE", "ALL"):
        safe = "".join(c if c.isalnum() or c in "-_ ." else "_" for c in stem)
        saved.update(copy_textures(meshes, target or os.path.join(folder, safe + "_textures"), textures_mode == "OUTSIDE",
                                   unity_project_root(folder)))
    # sidecar first: Unity may import the FBX the moment it lands, and only reads the sidecar on first import
    if sidecar_file:
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
        bpy.ops.export_scene.fbx(filepath=path, **fbx_options(**fbx_settings))
    except BaseException:
        if sidecar_file and os.path.exists(json_path):
            os.remove(json_path)
        raise
    finally:
        for ob, state in was:
            ob.select_set(state)
    return path, (json_path if sidecar_file else "")


class KAT_OT_export_unity(bpy.types.Operator, ExportHelper):
    bl_idname = "kat.export_unity"
    bl_label = "Export for Unity"
    bl_description = "Export the rig and its meshes as an FBX plus a .kat.json for the KAT Unity package"
    filename_ext = ".fbx"
    filter_glob: StringProperty(default="*.fbx", options={"HIDDEN"})

    textures_mode: EnumProperty(
        name="Textures", default="NONE",
        items=[("NONE", "Leave them", "Only images that exist nowhere but inside Blender are saved, next to the FBX"),
               ("OUTSIDE", "Copy those not in Unity yet", "Copy the texture files that are not inside the Unity project's Assets folder"),
               ("ALL", "Copy all", "Copy every texture file the materials use")],
        description="Whether to copy the textures the materials use, for a model whose textures Unity does not have yet")
    textures_folder: StringProperty(
        name="Textures folder", subtype="DIR_PATH", default="",
        description="Where the copied textures go. Empty: a <name>_textures folder next to the FBX. Pick a folder inside your Unity project's Assets to have them imported")
    sidecar_file: BoolProperty(name="Write the KAT file", default=True,
                               description="The .kat.json next to the FBX: the humanoid map, blendshapes, visemes, materials and textures, outfits. The KAT Unity package reads it")
    apply_modifiers: BoolProperty(name="Apply modifiers", default=True,
                                  description="Bake the modifiers (except the armature) into the exported meshes")
    triangulate: BoolProperty(name="Triangulate", default=False, description="Export triangles only, so Unity cannot triangulate differently")
    embed_textures: BoolProperty(name="Embed textures in the FBX", default=False,
                                 description="Put the texture files inside the FBX itself. Bigger file, and Unity extracts them on import")
    add_leaf_bones: BoolProperty(name="Add leaf bones", default=False,
                                 description="An extra bone at the end of every chain. Leave off: they confuse Unity's humanoid mapping")
    smoothing: EnumProperty(name="Smoothing", default="FACE",
                            items=[("FACE", "Face", "Smooth by face (flat or smooth shading)"), ("EDGE", "Edge", "Smooth by sharp edges"),
                                   ("OFF", "Normals only", "Write the normals as they are")])

    def draw(self, context):
        layout = self.layout
        box = layout.box()
        box.label(text="Textures", icon="TEXTURE")
        box.prop(self, "textures_mode", text="")
        if self.textures_mode != "NONE":
            box.prop(self, "textures_folder", text="")
            box.label(text="Empty: next to the FBX", icon="INFO")
        box = layout.box()
        box.label(text="Model", icon="MESH_DATA")
        box.prop(self, "apply_modifiers")
        box.prop(self, "triangulate")
        box.prop(self, "add_leaf_bones")
        box.prop(self, "embed_textures")
        box.prop(self, "smoothing")
        box = layout.box()
        box.label(text="KAT", icon="OUTLINER_OB_ARMATURE")
        box.prop(self, "sidecar_file")

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
        fbx, meta = export_unity(
            context, rig, self.filepath, sidecar_file=self.sidecar_file, textures_mode=self.textures_mode,
            textures_folder=self.textures_folder, apply_modifiers=self.apply_modifiers, triangulate=self.triangulate,
            embed_textures=self.embed_textures, add_leaf_bones=self.add_leaf_bones, smoothing=self.smoothing)
        # called from Python, so Blender does not remember it as a file export
        vault.record(fbx, "export", textures=textures.files_of(armature.meshes_of(rig)))
        self.report({"INFO"}, f"Wrote {os.path.basename(fbx)}" + (f" and {os.path.basename(meta)}" if meta else ""))
        return {"FINISHED"}


classes = (KAT_OT_export_unity,)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
