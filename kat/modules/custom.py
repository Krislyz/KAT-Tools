# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Krislyz
"""Custom avatars: merge a second armature in, attach loose meshes to bones, split meshes apart."""
import bpy
from bpy.props import BoolProperty, PointerProperty, StringProperty

from ..core.safe import operator_guard
from . import armature, face, mesh, refit, uiutil

TEMP = ".__merge"  # on a bone of the merged armature that is about to be folded into one of the base


def merge_pairs(base, other):
    """{bone of `other`: bone of `base`} for the bones that mean the same thing: the humanoid ones first (a Hips is a
    Hips whatever it is called), then equal names with the side and the case ignored."""
    return refit.match_bones(other, base)


def merge_rigs(context, base, other, attach_to="", similar=False, keep_extra=True, apply=False, join=False,
               drop_unused=False, clean_keys=False):
    """Join `other` into `base`. Its root bones are parented to bone `attach_to` of `base` when given.
    With `similar`, the bones of `other` that match one of `base` (both have a Hips) are not added twice: their weights
    go to the base bone and what hung from them hangs from the base bone. `keep_extra` off folds the other bones of
    `other` into the nearest matching one too. Returns the number of bones that came across."""
    if attach_to and attach_to not in base.data.bones:
        raise ValueError(f"No bone called {attach_to} to attach to")
    if any(ob.name not in context.view_layer.objects for ob in (base, other)):
        raise ValueError("Both armatures must be in the current scene")  # join crashes Blender otherwise
    if apply:
        for rig in (base, other):
            mesh.apply_transforms(context, rig)
    targets = merge_pairs(base, other) if similar else {}
    if targets and not keep_extra:
        for bone in other.data.bones:
            if bone.name in targets:
                continue
            up = bone.parent
            while up is not None and up.name not in targets:
                up = up.parent
            if up is not None:
                targets[bone.name] = targets[up.name]
    folded = {}  # temporary name -> the bone of the base it goes into
    taken = {b.name for b in base.data.bones}
    for bone in list(other.data.bones):  # clashing names would get .001 suffixes and break the vertex groups
        old = bone.name
        if old in targets:
            bone.name = old + TEMP
            folded[bone.name] = targets[old]
        elif old in taken:
            bone.name = f"{old}.{other.name}"
        taken.add(bone.name)
    roots = [b.name for b in other.data.bones if b.parent is None and b.name not in folded]
    count = len(other.data.bones) - len(folded)
    followers = [(ob, ob.matrix_world.copy(), ob.parent == other) for ob in armature.rigged_meshes(other)]
    objs = [base, other]
    context.view_layer.update()  # join reads each rig's pose, which the depsgraph builds lazily
    with context.temp_override(active_object=base, object=base, selected_objects=objs, selected_editable_objects=objs):
        bpy.ops.object.join()
    for ob, world, was_child in followers:  # join leaves these pointing at the armature it deleted
        for mod in ob.modifiers:
            if mod.type == "ARMATURE" and mod.object is None:
                mod.object = base
        if was_child:
            ob.parent = base
            ob.matrix_parent_inverse = base.matrix_world.inverted()
        ob.matrix_world = world
    if folded:
        for ob in armature.meshes_of(base):  # weights of the folded bones go to the base bones
            for name, into in folded.items():
                group = ob.vertex_groups.get(name)
                if group is None:
                    continue
                target = ob.vertex_groups.get(into) or ob.vertex_groups.new(name=into)
                for v in ob.data.vertices:
                    for g in v.groups:
                        if g.group == group.index and g.weight > 0:
                            target.add([v.index], g.weight, "ADD")
                ob.vertex_groups.remove(group)

        def fold(edit, _):
            for name, into in folded.items():
                for child in list(edit[name].children):
                    if child.name not in folded:
                        child.parent = edit[into]
                        child.use_connect = False
            for name in folded:
                edit.remove(edit[name])
        armature.edit_bones(context, base, [], fold)
    if attach_to:
        def action(edit, _):
            for name in roots:
                edit[name].parent = edit[attach_to]
        armature.edit_bones(context, base, [], action)
    if join:
        mesh.join_meshes(context, base)
    if clean_keys:
        for ob in armature.meshes_of(base):
            face.remove_empty_keys(ob)
    if drop_unused:
        armature.delete_bones(context, base, armature.prunable_bones(base, armature.meshes_of(base)))
    return count


def attach_mesh(context, rig, ob, bone_name):
    """Skin every vertex of `ob` fully to one bone and parent it to `rig`, keeping it where it is."""
    if bone_name not in rig.data.bones:
        raise ValueError(f"No bone called {bone_name}")
    for g in list(ob.vertex_groups):
        ob.vertex_groups.remove(g)
    ob.vertex_groups.new(name=bone_name).add(range(len(ob.data.vertices)), 1.0, "REPLACE")
    for mod in [m for m in ob.modifiers if m.type == "ARMATURE"]:
        ob.modifiers.remove(mod)
    ob.modifiers.new("Armature", "ARMATURE").object = rig
    world = ob.matrix_world.copy()
    ob.parent = rig
    ob.matrix_parent_inverse = rig.matrix_world.inverted()
    ob.matrix_world = world


def separate(context, ob, kind):
    """Split `ob` by 'MATERIAL' or 'LOOSE'. Returns the new objects (the original keeps the first part)."""
    before = set(context.scene.objects)
    layer = context.view_layer
    previous = layer.objects.active
    layer.objects.active = ob
    with context.temp_override(active_object=ob, object=ob, selected_objects=[ob], selected_editable_objects=[ob]):
        bpy.ops.object.mode_set(mode="EDIT")
        try:
            bpy.ops.mesh.select_all(action="SELECT")
            bpy.ops.mesh.separate(type=kind)
        finally:
            bpy.ops.object.mode_set(mode="OBJECT")
    layer.objects.active = previous
    return [o for o in context.scene.objects if o not in before]


class KatCustomOp:
    bl_options = {"REGISTER", "UNDO"}

    def fail(self, message):
        self.report({"ERROR"}, message)
        return {"CANCELLED"}


class KAT_CustomSettings(bpy.types.PropertyGroup):
    base: PointerProperty(name="Base", type=bpy.types.Object, poll=lambda self, ob: ob.type == "ARMATURE",
                          description="The armature that stays. Empty: the active armature")
    other: PointerProperty(name="To merge", type=bpy.types.Object, poll=lambda self, ob: ob.type == "ARMATURE",
                           description="The armature that goes into the base. Empty: the other selected armature")
    attach_to: StringProperty(name="Attach to", description="Bone of the base that the other armature's loose roots hang "
                                                            "from. Empty leaves them where they are")
    similar: BoolProperty(name="Merge matching bones", default=True,
                          description="A bone that both armatures have (a Hips, a Head, equal names) is merged: the weights "
                                      "go to the base bone and what hung from it hangs from the base bone")
    keep_extra: BoolProperty(name="Keep the extra bones", default=True,
                             description="The other armature's bones that the base has no match for stay. Off, they are "
                                         "folded into the nearest matching bone and removed")
    apply: BoolProperty(name="Apply transforms", default=False,
                        description="Apply rotation and scale of both armatures and their meshes first, so they line up")
    join: BoolProperty(name="Join meshes", default=False, description="Join every mesh of the result into one")
    drop_unused: BoolProperty(name="Remove unused bones", default=False,
                              description="Delete bones nothing uses afterwards: no weights, not humanoid, nothing hung from them")
    clean_keys: BoolProperty(name="Clean shape keys", default=False,
                             description="Remove shape keys that do not move anything")


def settings(context):
    return context.scene.kat_custom


def chosen_rigs(context):
    """(base, [others]): what the panel names, or else the selection with the active armature as the base."""
    s = settings(context)
    selected = [o for o in context.selected_objects if o.type == "ARMATURE"]
    base = s.base or (context.active_object if context.active_object in selected else None)
    others = [s.other] if s.other else [o for o in selected if o != base]
    return base, [o for o in others if o != base]


class KAT_OT_merge_rigs(KatCustomOp, bpy.types.Operator):
    bl_idname = "kat.merge_rigs"
    bl_label = "Merge armatures"
    bl_description = ("Put the second armature into the first. Bones both have (a Hips, a Head) are merged, not added "
                      "twice. The rest are kept or folded in, as ticked")

    @operator_guard
    def execute(self, context):
        s = settings(context)
        base, others = chosen_rigs(context)
        if base is None or not others:
            raise ValueError("Pick the base armature and the one to merge, or select both with the base active")
        moved = sum(merge_rigs(context, base, o, s.attach_to, similar=s.similar, keep_extra=s.keep_extra, apply=s.apply,
                               join=s.join, drop_unused=s.drop_unused, clean_keys=s.clean_keys) for o in others)
        self.report({"INFO"}, f"Merged {moved} bones into {base.name}")
        s.other = None
        return {"FINISHED"}


class KAT_OT_attach_mesh(KatCustomOp, bpy.types.Operator):
    bl_idname = "kat.attach_mesh"
    bl_label = "Attach mesh to bone"
    bl_description = "Select an armature and a loose mesh (mesh active): skin the mesh to one bone"
    bone: StringProperty(name="Bone")

    def invoke(self, context, event):
        if self.rig(context) is None or context.active_object.type != "MESH":
            return self.fail("Select an armature, then the mesh to attach (mesh active)")
        return context.window_manager.invoke_props_dialog(self)

    def draw(self, context):
        self.layout.prop_search(self, "bone", self.rig(context).data, "bones")

    @staticmethod
    def rig(context):
        return next((o for o in context.selected_objects if o.type == "ARMATURE"), None)

    @operator_guard
    def execute(self, context):
        rig, ob = self.rig(context), context.active_object
        if rig is None or ob is None or ob.type != "MESH":
            raise ValueError("Select an armature, then the mesh to attach (mesh active)")
        attach_mesh(context, rig, ob, self.bone)
        self.report({"INFO"}, f"{ob.name} now follows {self.bone}")
        return {"FINISHED"}


class KAT_OT_separate(KatCustomOp, bpy.types.Operator):
    bl_idname = "kat.separate"
    bl_label = "Separate mesh"
    bl_description = "Split the active mesh into one object per material, or per loose part"
    kind: bpy.props.EnumProperty(name="By", items=[("MATERIAL", "Material", ""), ("LOOSE", "Loose parts", "")],
                                 default="MATERIAL")

    @operator_guard
    def execute(self, context):
        ob = context.active_object
        if ob is None or ob.type != "MESH":
            raise ValueError("Select a mesh")
        new = separate(context, ob, self.kind)
        self.report({"INFO"}, f"Made {len(new)} new objects")
        return {"FINISHED"}


class KAT_PT_custom(bpy.types.Panel):
    bl_label = "Custom avatar"
    bl_idname = "KAT_PT_custom"
    bl_order = 1030
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "KAT"
    bl_options = {"DEFAULT_CLOSED"}

    def draw_header(self, context):
        self.layout.label(icon="USER")

    def draw(self, context):
        uiutil.paragraph(self.layout, context, "Build an avatar from parts: put one armature into another, hang a loose "
                                               "mesh on a bone, split a mesh apart.", "INFO")


class KAT_PT_merge(bpy.types.Panel):
    bl_label = "Merge armatures"
    bl_idname = "KAT_PT_merge"
    bl_parent_id = "KAT_PT_custom"
    bl_order = 1
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "KAT"

    def draw_header(self, context):
        self.layout.label(icon="AUTOMERGE_ON")

    def draw(self, context):
        s = settings(context)
        layout = self.layout
        col = layout.column(align=True)
        col.prop(s, "base")
        col.prop(s, "other")
        base, others = chosen_rigs(context)
        if base is not None and others:
            if s.similar:
                same = merge_pairs(base, others[0])
                layout.label(text=f"{len(same)} bones match and will be merged", icon="CHECKMARK" if same else "INFO")
            else:
                layout.label(text="Armatures are joined as they are", icon="INFO")
            layout.prop_search(s, "attach_to", base.data, "bones", text="Attach to")
        else:
            uiutil.paragraph(layout, context, "Pick both armatures above, or select both with the base active.", "INFO")
        box = layout.box()
        box.prop(s, "similar")
        sub = box.column()
        sub.enabled = s.similar
        sub.prop(s, "keep_extra")
        box.prop(s, "apply")
        box.prop(s, "join")
        box.prop(s, "drop_unused")
        box.prop(s, "clean_keys")
        row = layout.row()
        row.scale_y = 1.3
        row.enabled = base is not None and bool(others)
        row.operator("kat.merge_rigs", icon="AUTOMERGE_ON")


class KAT_PT_attach(bpy.types.Panel):
    bl_label = "Meshes"
    bl_idname = "KAT_PT_attach"
    bl_parent_id = "KAT_PT_custom"
    bl_order = 2
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "KAT"

    def draw_header(self, context):
        self.layout.label(icon="MESH_DATA")

    def draw(self, context):
        col = self.layout.column(align=True)
        col.operator("kat.attach_mesh", icon="CONSTRAINT_BONE")
        col.operator("kat.separate", icon="MOD_BUILD")


classes = (KAT_CustomSettings, KAT_OT_merge_rigs, KAT_OT_attach_mesh, KAT_OT_separate, KAT_PT_custom, KAT_PT_merge,
           KAT_PT_attach)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.kat_custom = PointerProperty(type=KAT_CustomSettings)


def unregister():
    del bpy.types.Scene.kat_custom
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
