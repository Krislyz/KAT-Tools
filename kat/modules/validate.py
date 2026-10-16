# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Krislyz
"""Check an avatar against VRChat's PC performance ranks and common rig mistakes."""
import math

import bpy

from ..core import humanoid, jp, visemes, vrc
from ..core.safe import operator_guard
from . import armature, face, uiutil

last = {"rank": None, "stats": [], "bars": [], "notes": []}  # what the panel shows after the last check


def triangles(ob, depsgraph):
    ev = ob.evaluated_get(depsgraph)
    mesh = ev.to_mesh()
    try:
        mesh.calc_loop_triangles()
        return len(mesh.loop_triangles)
    finally:
        ev.to_mesh_clear()


def texture_sizes(objs):
    seen = {}
    for ob in objs:
        for slot in ob.material_slots:
            mat = slot.material
            if mat and mat.node_tree:
                for node in mat.node_tree.nodes:
                    if node.type == "TEX_IMAGE" and node.image:
                        seen[node.image.name] = tuple(node.image.size)
    return list(seen.values())


def collect(context, rig):
    meshes = armature.meshes_of(rig)
    graph = context.evaluated_depsgraph_get()
    return {
        "triangles": sum(triangles(ob, graph) for ob in meshes),
        "texture_mb": round(vrc.texture_mb(texture_sizes(meshes)), 1),
        "skinned_meshes": len(meshes),
        "material_slots": sum(len(ob.material_slots) for ob in meshes),
        "bones": len(rig.data.bones),
    }


def arm_elevation(rig, best, side):
    """Degrees the arm points above (+) or below (-) horizontal, or None if the rig lacks the bones."""
    top, tip = best.get(side + "UpperArm"), best.get(side + "Hand")
    if not top or not tip:
        return None
    a = rig.matrix_world @ rig.data.bones[top].head_local
    b = rig.matrix_world @ rig.data.bones[tip].head_local
    d = b - a
    return math.degrees(math.asin(d.z / d.length)) if d.length else None


def check_rig(context, rig, meshes):
    """[(severity, text)]. Severity is ERROR, WARNING or INFO."""
    notes = []
    best, rest = armature.map_bones(rig)
    missing = armature.missing_required(best)
    if missing:
        notes.append(("ERROR", "Missing required humanoid bones: " + ", ".join(missing)))
    _, turn, scale = rig.matrix_world.decompose()  # works for any rotation mode
    if any(abs(s - 1) > 1e-4 for s in scale) or turn.angle > 1e-4:
        notes.append(("WARNING", "Armature has unapplied scale or rotation (Model > Fix model)"))
    for side in humanoid.SIDES:
        angle = arm_elevation(rig, best, side)
        if angle is not None and abs(angle) > 25:
            notes.append(("WARNING", f"{side} arm is {angle:.0f} degrees off horizontal, not a T-pose"))
    bones = {b.name for b in rig.data.bones}
    for ob in meshes:
        deform = {g.index for g in ob.vertex_groups if g.name in bones}
        heavy = sum(1 for v in ob.data.vertices if sum(1 for g in v.groups if g.group in deform and g.weight > 0) > 4)
        if heavy:
            notes.append(("WARNING", f"{ob.name}: {heavy} vertices have more than 4 bone weights"))
    if not any(all(n in (face.key_blocks(ob) or ()) for n in visemes.NAMES) for ob in meshes):
        notes.append(("INFO", "No vrc.v_* viseme shape keys yet (Face > Make visemes)"))
    if "LeftEye" not in best or "RightEye" not in best:
        notes.append(("INFO", "No eye bones, eye tracking needs them (Face > Make eye bones)"))
    names = [b.name for b in rig.data.bones] + [m.name for m in bpy.data.materials]
    if any(jp.has_japanese(n) for n in names):
        notes.append(("INFO", "Some names are still Japanese (Model > Translate names)"))
    if any(m.name[-4:-3] == "." and m.name[-3:].isdigit() for m in bpy.data.materials):
        notes.append(("INFO", "Duplicate .001 materials found (Model > Clean materials)"))
    return notes


def run(context, rig):
    meshes = armature.meshes_of(rig)
    stats = collect(context, rig)
    platform = getattr(getattr(context.scene, "kat", None), "platform", "PC")
    per, worst = vrc.evaluate(stats, platform)
    limits = vrc.PLATFORMS[platform]
    last["rank"] = vrc.RANKS[worst]
    last["platform"] = platform
    last["stats"] = [(vrc.LABELS[m], stats[m], vrc.RANKS[per[m]]) for m in vrc.LIMITS]
    last["bars"] = [(vrc.LABELS[m], stats[m], vrc.RANKS[per[m]], stats[m] / limits[m][-1]) for m in vrc.LIMITS]
    last["notes"] = check_rig(context, rig, meshes)
    return last


class KAT_OT_validate(bpy.types.Operator):
    bl_idname = "kat.validate"
    bl_label = "Check avatar"
    bl_description = "Rank the avatar against VRChat's limits for the chosen platform and look for common rig problems"

    @operator_guard
    def execute(self, context):
        rig = armature.rig_of(context)
        if rig is None:
            raise ValueError("Select an armature, or a mesh rigged to one")
        result = run(context, rig)
        self.report({"INFO"}, f"VRChat {'Quest' if result['platform'] == 'QUEST' else 'PC'} rank: {result['rank']}")
        for severity, text in result["notes"]:
            self.report({severity}, text)
        return {"FINISHED"}


class KAT_PT_check(bpy.types.Panel):
    bl_label = "Check"
    bl_idname = "KAT_PT_check"
    bl_order = 1070
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "KAT"
    bl_options = {"DEFAULT_CLOSED"}

    def draw_header(self, context):
        self.layout.label(icon="CHECKMARK")

    def draw(self, context):
        layout = self.layout
        layout.prop(context.scene.kat, "platform", expand=True)
        row = layout.row()
        row.scale_y = 1.3
        row.enabled = armature.rig_of(context) is not None
        row.operator("kat.validate", icon="CHECKMARK")
        if last["rank"] is None:
            return
        row = layout.row()
        row.alert = last["rank"] in ("Poor", "Very Poor")
        row.label(text=f"VRChat {'Quest' if last.get('platform') == 'QUEST' else 'PC'} rank: {last['rank']}", icon="INFO")
        col = layout.column(align=True)
        for label, value, rank, factor in last["bars"]:
            uiutil.bar(col, factor, f"{label.split(' (')[0]}: {value:,}  {rank}")
        col = layout.column(align=True)
        for severity, text in last["notes"]:
            uiutil.paragraph(col, context, text, {"ERROR": "ERROR", "WARNING": "ERROR", "INFO": "INFO"}[severity])


classes = (KAT_OT_validate, KAT_PT_check)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
