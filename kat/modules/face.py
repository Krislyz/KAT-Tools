# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Krislyz
"""Face rigging, all picked inline in the panel: VRChat visemes, blink names, eye bones, face tracking shapes."""
from types import SimpleNamespace

import bpy
import numpy as np
from mathutils import Vector

from ..core import facetrack, humanoid, synth, visemes
from ..core.safe import operator_guard
from . import armature, uiutil


def key_blocks(ob):
    return ob.data.shape_keys.key_blocks if ob and ob.type == "MESH" and ob.data.shape_keys else None


def _coords(block):
    out = np.empty(len(block.data) * 3, dtype=np.float32)
    block.data.foreach_get("co", out)
    return out


def _delta(block):
    """What the shape adds on top of the shape it is relative to."""
    return _coords(block) - _coords(block.relative_key)


def guess_face_mesh(objects):
    """The mesh that most likely holds the face: one called Body (or Face, Head) that has shape keys, else the mesh
    with the most shape keys. A mesh called Body without shape keys is still picked when nothing else qualifies."""
    meshes = [ob for ob in objects if ob.type == "MESH"]
    shaped = [ob for ob in meshes if (key_blocks(ob) and len(key_blocks(ob)) > 1)]

    def count(ob):
        return len(key_blocks(ob))

    for word in ("body", "face", "head"):
        hit = [ob for ob in shaped if ob.name.lower() == word] or [ob for ob in shaped if word in ob.name.lower()]
        if hit:
            return max(hit, key=count)
    if shaped:
        return max(shaped, key=count)
    return next((ob for ob in meshes if ob.name.lower() == "body"), None)


def face_object(context):
    """The mesh picked in the Face panel, or the best guess in the scene."""
    ob = context.scene.kat.face_mesh
    if ob is None:
        ob = guess_face_mesh(context.scene.objects)
    if ob is None or ob.type != "MESH":
        raise ValueError("Pick the mesh with the face shape keys in the Face panel")
    return ob


def move_to_top(ob, names):
    """Put these shape keys right under the basis, in the order given. The active key stays active."""
    blocks = key_blocks(ob)
    if blocks is None:
        return
    keep = blocks[ob.active_shape_key_index].name if 0 <= ob.active_shape_key_index < len(blocks) else None
    with bpy.context.temp_override(object=ob, active_object=ob):
        for name in reversed([n for n in names if n in blocks]):
            if blocks.find(name) > 1:  # index 0 is the basis, 1 is already the top
                ob.active_shape_key_index = blocks.find(name)
                bpy.ops.object.shape_key_move(type="TOP")
    if keep in blocks:
        ob.active_shape_key_index = blocks.find(keep)


def make_visemes(ob, a_name, o_name, ch_name, intensity=1.0):
    """Create or overwrite the 15 vrc.v_* shape keys and put them at the top of the list in alphabetical order
    (aa, ch, dd ...). Returns how many were written."""
    blocks = key_blocks(ob)
    if blocks is None:
        raise ValueError(f"{ob.name} has no shape keys")
    sources = {}
    for role, name in (("a", a_name), ("o", o_name), ("ch", ch_name)):
        if name not in blocks:
            raise ValueError(f"Pick a shape key for the {role.upper()} mouth shape")
        sources[role] = name
    basis = _coords(blocks[0])
    delta = [_coords(blocks[sources[r]]) - basis for r in ("a", "o", "ch")]
    for viseme, weights in visemes.RECIPES.items():
        name = visemes.PREFIX + viseme
        target = blocks.get(name) or ob.shape_key_add(name=name, from_mix=False)
        mixed = basis.copy()
        for w, d in zip(weights, delta):
            mixed += intensity * w * d
        target.data.foreach_set("co", mixed)
        target.relative_key = blocks[0]
        target.value = 0.0  # new keys start at 1.0, which would distort the model
    ob.data.update()
    move_to_top(ob, sorted(visemes.NAMES))
    return len(visemes.RECIPES)


def copy_key(ob, source, new_name):
    blocks = key_blocks(ob)
    if source not in blocks:
        raise ValueError(f"No shape key called {source}")
    block = blocks.get(new_name) or ob.shape_key_add(name=new_name, from_mix=False)
    block.data.foreach_set("co", _coords(blocks[source]))
    block.relative_key = blocks[0]
    block.value = 0.0
    return block


def create_facetrack(ob, fill_empty=False, overwrite=False, synthesize=True):
    """Make the VRCFaceTracking Unified Expression shape keys the model's own shapes allow, then approximate the rest
    from the shapes and regions it has (see core/synth.py). Sources are never touched.
    Returns made, skipped (already there), missing (still not there), approximated and the matched groups."""
    blocks = key_blocks(ob)
    if blocks is None:
        raise ValueError(f"{ob.name} has no shape keys")
    mixes, groups = facetrack.plan([b.name for b in blocks])
    basis = _coords(blocks[0])
    pts = basis.reshape(-1, 3)
    x0 = float((pts[:, 0].min() + pts[:, 0].max()) / 2)
    masks = synth.side_masks(pts[:, 0], x0, float(pts[:, 0].max() - pts[:, 0].min()))
    deltas, made, skipped, approximated = {}, [], [], []

    def delta_of(name):
        if name not in deltas:
            deltas[name] = _delta(blocks[name])
        return deltas[name]

    for target, sources in mixes.items():
        if target in blocks and not overwrite:
            skipped.append(target)
            continue
        side = "Left" if target.endswith("Left") else "Right" if target.endswith("Right") else None
        mixed = basis.copy()
        for source, weight in sources:
            d = delta_of(source).reshape(-1, 3)
            if side and synth.is_symmetric(d, pts[:, 0], x0):
                # a shape that moves both sides cannot feed a one-sided one as it is: take this side's half, in full
                d = d * masks[side][:, None]
                weight *= 2.0 if sum(w for _, w in sources) <= 1.001 else 1.0
            mixed += (weight * d).reshape(-1)
        block = blocks.get(target) or ob.shape_key_add(name=target, from_mix=False)
        block.data.foreach_set("co", mixed)
        block.relative_key = blocks[0]
        block.value = 0.0
        made.append(target)
    if synthesize:
        lookup = {facetrack.norm(b.name): b.name for b in blocks}

        def get(*names):
            for name in names:
                real = lookup.get(facetrack.norm(name))
                if real is not None:
                    return delta_of(real).reshape(-1, 3)
            return None

        wanted = {n for n in facetrack.UNIFIED if n not in facetrack.OPTIONAL and n not in made
                  and (overwrite or n not in blocks)}
        for name, (delta, how) in synth.build(pts, get, wanted).items():
            block = blocks.get(name) or ob.shape_key_add(name=name, from_mix=False)
            block.data.foreach_set("co", (pts + delta).reshape(-1))
            block.relative_key = blocks[0]
            block.value = 0.0
            made.append(name)
            approximated.append((name, how))
    if fill_empty:
        for name in facetrack.UNIFIED:
            if name not in blocks and name not in facetrack.OPTIONAL:
                block = ob.shape_key_add(name=name, from_mix=False)
                block.relative_key = blocks[0]
                block.value = 0.0
                made.append(name)
    ob.data.update()
    missing = [n for n in facetrack.UNIFIED if n not in blocks and n not in facetrack.OPTIONAL]
    return SimpleNamespace(made=made, skipped=skipped, missing=missing, groups=groups, approximated=approximated)


def empty_keys(ob, tolerance=1e-6, keep=()):
    """Names of shape keys that do not move any vertex. Basis, vrc.*, KAT.* and Unified Expression keys are never listed."""
    blocks = key_blocks(ob)
    if blocks is None:
        return []
    protected = set(facetrack.UNIFIED)
    doomed = []
    for block in list(blocks)[1:]:
        if block.name in keep or block.name.startswith(("vrc.", "KAT.")) or block.name in protected:
            continue
        if np.abs(_coords(block) - _coords(block.relative_key)).max(initial=0.0) <= tolerance:
            doomed.append(block.name)
    return doomed


def remove_empty_keys(ob, tolerance=1e-6, keep=()):
    """Delete shape keys that do not move any vertex. Returns how many went."""
    doomed = empty_keys(ob, tolerance, keep)
    blocks = key_blocks(ob)
    for name in doomed:
        ob.shape_key_remove(blocks[name])
    return len(doomed)


def group_centre(ob, group_name):
    group = ob.vertex_groups.get(group_name)
    if group is None:
        raise ValueError(f"No vertex group called {group_name}")
    pts = []
    for v in ob.data.vertices:
        for g in v.groups:
            if g.group == group.index and g.weight > 0.01:
                pts.append(ob.matrix_world @ v.co)
                break
    if not pts:
        raise ValueError(f"Vertex group {group_name} has no weights")
    return sum(pts, Vector()) / len(pts)


def make_eye_bone(context, rig, ob, group_name, key, forward=0.03):
    """Add an eye bone at the centre of `group_name`, under Head, and make the group follow it."""
    head = armature.map_bones(rig)[0].get("Head")
    if head is None:
        raise ValueError("The rig has no recognisable Head bone to attach eyes to")
    centre = rig.matrix_world.inverted() @ group_centre(ob, group_name)
    name = humanoid.style_name(key, "unity")
    if name in ob.vertex_groups and name != group_name:
        raise ValueError(f"{ob.name} already has a vertex group called {name}")

    def action(edit, _):
        if name in edit:
            edit.remove(edit[name])
        eb = edit.new(name)
        eb.head = centre
        eb.tail = centre + Vector((0.0, -forward, 0.0))  # Blender avatars face -Y
        eb.parent = edit[head]
        eb.use_connect = False

    armature.edit_bones(context, rig, [], action)
    ob.vertex_groups[group_name].name = name
    return name


def needs_report(ob):
    """What face tracking could be built from this mesh and what it still lacks. Cached per set of shape keys."""
    names = tuple(b.name for b in key_blocks(ob) or ())
    if _report["key"] != (ob.name, names):
        _report.update(key=(ob.name, names), value=facetrack.needed(list(names)))
    return _report["value"]


_report = {"key": None, "value": None}


class KAT_OT_face_pick(bpy.types.Operator):
    bl_idname = "kat.face_pick"
    bl_label = "Pick the face mesh"
    bl_description = "Choose the mesh called Body (or the one with the most shape keys) as the face mesh"

    @operator_guard
    def execute(self, context):
        rig = armature.rig_of(context)
        pool = armature.meshes_of(rig) if rig else list(context.scene.objects)
        ob = guess_face_mesh(pool) or guess_face_mesh(context.scene.objects)
        if ob is None:
            raise ValueError("No mesh with shape keys found")
        context.scene.kat.face_mesh = ob
        self.report({"INFO"}, f"Face mesh: {ob.name}")
        return {"FINISHED"}


class KAT_OT_visemes(bpy.types.Operator):
    bl_idname = "kat.visemes"
    bl_label = "Create visemes"
    bl_description = ("Build the 15 VRChat viseme shape keys from the three mouth shapes picked above. "
                      "They go to the top of the shape key list, in alphabetical order")
    bl_options = {"REGISTER", "UNDO"}

    @operator_guard
    def execute(self, context):
        o = context.scene.kat
        n = make_visemes(face_object(context), o.viseme_a, o.viseme_o, o.viseme_ch, o.viseme_intensity)
        self.report({"INFO"}, f"Wrote {n} viseme shape keys at the top of the list")
        return {"FINISHED"}


class KAT_OT_blink(bpy.types.Operator):
    bl_idname = "kat.blink"
    bl_label = "Name blink shapes"
    bl_description = "Copy the blink shape keys picked above to the vrc.blink_* names VRChat looks for"
    bl_options = {"REGISTER", "UNDO"}

    @operator_guard
    def execute(self, context):
        o, ob = context.scene.kat, face_object(context)
        picks = (o.blink_left, o.blink_right, o.blink_lower_left, o.blink_lower_right)
        done = 0
        for src, dest in zip(picks, visemes.BLINK):
            if src:
                copy_key(ob, src, dest)
                done += 1
        if not done:
            raise ValueError("Pick at least one blink shape key")
        self.report({"INFO"}, f"Wrote {done} blink shape keys")
        return {"FINISHED"}


class KAT_OT_eyes(bpy.types.Operator):
    bl_idname = "kat.eyes"
    bl_label = "Make eye bones"
    bl_description = "Create LeftEye and RightEye bones from the eyeball vertex groups picked above"
    bl_options = {"REGISTER", "UNDO"}

    @operator_guard
    def execute(self, context):
        o, ob = context.scene.kat, face_object(context)
        rig = armature.rig_of(context) or (ob.parent if ob.parent and ob.parent.type == "ARMATURE" else None)
        if rig is None:
            raise ValueError("Select the armature, or the face mesh rigged to it")
        armature.check_ready(context, rig)
        pairs = ((o.eye_left, "LeftEye"), (o.eye_right, "RightEye"))
        for group, _ in pairs:  # check both before changing anything
            group_centre(ob, group)
        for group, key in pairs:
            make_eye_bone(context, rig, ob, group, key)
        self.report({"INFO"}, "Eye bones created")
        return {"FINISHED"}


class KAT_OT_facetrack(bpy.types.Operator):
    bl_idname = "kat.facetrack"
    bl_label = "Create face tracking shapes"
    bl_description = ("Build VRCFaceTracking Unified Expression shape keys from the shapes the mesh already has "
                      "(ARKit, MMD or VRChat visemes). The original shapes are never touched")
    bl_options = {"REGISTER", "UNDO"}

    @operator_guard
    def execute(self, context):
        o = context.scene.kat
        result = create_facetrack(face_object(context), fill_empty=o.ft_fill_empty, overwrite=o.ft_overwrite,
                                  synthesize=o.ft_synthesize)
        if not result.made and not result.skipped:
            raise ValueError("No ARKit, MMD or viseme shapes found to build from. Pick the right mesh, or sculpt the shapes")
        self.report({"INFO"}, f"Created {len(result.made)} shapes, {len(result.skipped)} already existed")
        if result.approximated:
            self.report({"INFO"}, f"{len(result.approximated)} shapes are approximations, sculpt over them where they look wrong")
        if result.missing and not o.ft_fill_empty:
            by_region = {}
            for name in result.missing:
                by_region.setdefault(facetrack.region_of(name), []).append(name)
            self.report({"WARNING"}, "Still needed: " + ", ".join(f"{r} {len(v)}" for r, v in by_region.items())
                        + ". The Face tracking panel lists them")
        return {"FINISHED"}


class KAT_OT_clean_keys(bpy.types.Operator):
    bl_idname = "kat.clean_keys"
    bl_label = "Remove empty shape keys"
    bl_description = "Delete shape keys that do not move anything (vrc.* and face tracking keys stay)"
    bl_options = {"REGISTER", "UNDO"}

    @operator_guard
    def execute(self, context):
        self.report({"INFO"}, f"Removed {remove_empty_keys(face_object(context))} empty shape keys")
        return {"FINISHED"}


class FacePanel:
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "KAT"


class KAT_PT_face(FacePanel, bpy.types.Panel):
    bl_label = "Face"
    bl_idname = "KAT_PT_face"
    bl_order = 1040
    bl_options = {"DEFAULT_CLOSED"}

    def draw_header(self, context):
        self.layout.label(icon="SHAPEKEY_DATA")

    def draw(self, context):
        o = context.scene.kat
        layout = self.layout
        row = layout.row(align=True)
        row.prop(o, "face_mesh", text="Mesh")
        row.operator("kat.face_pick", text="", icon="EYEDROPPER")
        ob = o.face_mesh
        if ob is None:
            uiutil.paragraph(layout, context, "Pick the mesh with the face shapes. It is usually called Body.", "INFO")
            return
        keys = ob.data.shape_keys
        if keys is None or len(keys.key_blocks) < 2:
            uiutil.status(layout, False, "", "This mesh has no shape keys")
        else:
            layout.label(text=f"{len(keys.key_blocks) - 1} shape keys", icon="CHECKMARK")


class KAT_PT_visemes(FacePanel, bpy.types.Panel):
    bl_label = "Visemes"
    bl_idname = "KAT_PT_visemes"
    bl_parent_id = "KAT_PT_face"
    bl_order = 1041

    @classmethod
    def poll(cls, context):
        return context.scene.kat.face_mesh is not None

    def draw_header(self, context):
        self.layout.label(icon="PLAY_SOUND")

    def draw(self, context):
        o = context.scene.kat
        ob = o.face_mesh
        keys = ob.data.shape_keys
        layout = self.layout
        if keys is None:
            uiutil.status(layout, False, "", "Needs a mesh with shape keys")
            return
        blocks = keys.key_blocks
        made = sum(1 for n in visemes.NAMES if n in blocks)
        layout.label(text=f"{made} of {len(visemes.NAMES)} made" if made else "Not made yet",
                     icon="CHECKMARK" if made == len(visemes.NAMES) else "SHAPEKEY_DATA")
        col = layout.column(align=True)
        missing = []
        for prop, label in (("viseme_a", "AA"), ("viseme_o", "OH"), ("viseme_ch", "CH")):
            row = col.row(align=True)
            row.alert = getattr(o, prop) not in blocks
            row.prop_search(o, prop, keys, "key_blocks", text=label)
            if row.alert:
                missing.append(label)
        if missing:
            uiutil.paragraph(layout, context, "Needs a shape key for: " + ", ".join(missing), "ERROR")
        layout.prop(o, "viseme_intensity", slider=True)
        row = layout.row()
        row.scale_y = 1.3
        row.enabled = not missing
        row.operator("kat.visemes", icon="SHAPEKEY_DATA")


class KAT_PT_eyes(FacePanel, bpy.types.Panel):
    bl_label = "Blink & Eyes"
    bl_idname = "KAT_PT_eyes"
    bl_parent_id = "KAT_PT_face"
    bl_order = 1042

    @classmethod
    def poll(cls, context):
        return context.scene.kat.face_mesh is not None

    def draw_header(self, context):
        self.layout.label(icon="HIDE_OFF")

    def draw(self, context):
        o = context.scene.kat
        ob = o.face_mesh
        keys = ob.data.shape_keys
        layout = self.layout
        box = layout.box()
        box.label(text="Blink", icon="HIDE_OFF")
        if keys is None:
            uiutil.status(box, False, "", "Needs a mesh with shape keys")
        else:
            blocks = keys.key_blocks
            col = box.column(align=True)
            for prop, label in (("blink_left", "Left"), ("blink_right", "Right"), ("blink_lower_left", "Lid L"),
                                ("blink_lower_right", "Lid R")):
                row = col.row(align=True)
                row.alert = prop in ("blink_left", "blink_right") and getattr(o, prop) not in blocks
                row.prop_search(o, prop, keys, "key_blocks", text=label)
            have = [n for n in visemes.BLINK if n in blocks]
            if have:
                box.label(text=f"{len(have)} vrc.blink shapes made", icon="CHECKMARK")
            elif not (o.blink_left in blocks and o.blink_right in blocks):
                uiutil.paragraph(box, context, "Needs the left and right blink shapes", "ERROR")
            box.operator("kat.blink", icon="HIDE_OFF")
        box = layout.box()
        box.label(text="Eye bones", icon="BONE_DATA")
        rig = armature.rig_of(context) or (ob.parent if ob.parent and ob.parent.type == "ARMATURE" else None)
        if rig is None:
            uiutil.status(box, False, "", "Needs the armature selected")
        else:
            best = armature.cached_map(rig)[0]
            both = "LeftEye" in best and "RightEye" in best
            box.label(text="Both eye bones exist" if both else "No eye bones yet", icon="CHECKMARK" if both else "INFO")
        col = box.column(align=True)
        for prop, label in (("eye_left", "Left"), ("eye_right", "Right")):
            row = col.row(align=True)
            row.alert = not getattr(o, prop)
            row.prop_search(o, prop, ob, "vertex_groups", text=label)
        if not (o.eye_left and o.eye_right):
            uiutil.paragraph(box, context, "Needs a vertex group for each eyeball", "ERROR")
        row = box.row()
        row.enabled = bool(o.eye_left and o.eye_right and rig)
        row.operator("kat.eyes", icon="BONE_DATA")


class KAT_PT_facetrack(FacePanel, bpy.types.Panel):
    bl_label = "Face tracking"
    bl_idname = "KAT_PT_facetrack"
    bl_parent_id = "KAT_PT_face"
    bl_order = 1043

    @classmethod
    def poll(cls, context):
        return context.scene.kat.face_mesh is not None

    def draw_header(self, context):
        self.layout.label(icon="OUTLINER_OB_FORCE_FIELD")

    def draw(self, context):
        o = context.scene.kat
        ob = o.face_mesh
        layout = self.layout
        if key_blocks(ob) is None:
            uiutil.status(layout, False, "", "Needs a mesh with shape keys")
            return
        have, can, missing, groups = needs_report(ob)
        total = len(have) + len(can) + sum(len(v) for v in missing.values())
        uiutil.bar(layout, (len(have) + len(can)) / total, f"{len(have) + len(can)} of {total} shapes")
        col = layout.column(align=True)
        col.label(text=f"{len(have)} already on the mesh", icon="CHECKMARK")
        col.label(text=f"{len(can)} can be made now", icon="ADD")
        still = sum(len(v) for v in missing.values())
        col.label(text=f"{still} still needed", icon="ERROR" if still else "CHECKMARK")
        if still and uiutil.foldout(layout, o, "ft_show_needed", "What is still needed"):
            box = layout.box()
            for region, names in missing.items():
                box.label(text=f"{region}: {len(names)}", icon="DOT")
                uiutil.paragraph(box, context, ", ".join(names))
                hint = facetrack.HINTS.get(region)
                if hint:
                    col = uiutil.paragraph(box, context, "Add a shape like " + hint)
                    col.enabled = False
        layout.prop(o, "ft_synthesize", icon="MOD_MESHDEFORM")
        row = layout.row()
        row.prop(o, "ft_overwrite")
        row.prop(o, "ft_fill_empty")
        row = layout.row()
        row.scale_y = 1.3
        row.enabled = bool(can or o.ft_synthesize or o.ft_overwrite or o.ft_fill_empty)
        row.operator("kat.facetrack", icon="OUTLINER_OB_FORCE_FIELD")
        layout.operator("kat.clean_keys", icon="TRASH")


classes = (KAT_OT_face_pick, KAT_OT_visemes, KAT_OT_blink, KAT_OT_eyes, KAT_OT_facetrack, KAT_OT_clean_keys,
           KAT_PT_face, KAT_PT_visemes, KAT_PT_eyes, KAT_PT_facetrack)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
