# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Krislyz
"""Draw every KAT panel against a layout that checks what it is asked to show: each property exists, each button is a
registered operator, each list is a registered list. Run: blender -b --factory-startup --python tests/blender_panels_draw.py"""
import inspect
import os
import sys

import bpy

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import strict  # noqa: E402,F401

import kat  # noqa: E402

BONES = ["Hips", "Spine", "Chest", "Neck", "Head"]
seen = {"props": 0, "operators": 0, "lists": 0}


def has_prop(data, name):
    if hasattr(data, name):
        return True
    props = getattr(getattr(data, "bl_rna", None), "properties", None)
    return props is not None and name in props


class Op:
    def __setattr__(self, key, value):
        object.__setattr__(self, key, value)

    def __getattr__(self, key):
        return Op()


class Layout:
    """Anything a layout can do, remembered or ignored. Properties, operators and lists are checked."""

    def __init__(self, label="layout"):
        object.__setattr__(self, "_label", label)

    def __setattr__(self, key, value):
        pass

    def __getattr__(self, name):
        if name == "prop":
            def prop(data, key, *a, **kw):
                assert has_prop(data, key), f"{self._label}: {type(data).__name__} has no property {key!r}"
                seen["props"] += 1
            return prop
        if name == "prop_search":
            def prop_search(data, key, other, collection, *a, **kw):
                assert has_prop(data, key), f"{self._label}: {type(data).__name__} has no property {key!r}"
                assert has_prop(other, collection), f"{self._label}: no collection {collection!r}"
                seen["props"] += 1
            return prop_search
        if name == "props_enum":
            def props_enum(data, key, *a, **kw):
                assert has_prop(data, key), f"{self._label}: no property {key!r}"
            return props_enum
        if name == "operator":
            def operator(idname, *a, **kw):
                module, _, op = idname.partition(".")
                assert hasattr(getattr(bpy.ops, module), op), f"{self._label}: no operator {idname!r}"
                seen["operators"] += 1
                return Op()
            return operator
        if name == "template_list":
            def template_list(list_class, list_id, data, key, *a, **kw):
                assert hasattr(bpy.types, list_class), f"{self._label}: no list class {list_class!r}"
                assert has_prop(data, key), f"{self._label}: no collection {key!r}"
                seen["lists"] += 1
            return template_list
        if name in ("row", "column", "box", "split", "column_flow", "grid_flow"):
            return lambda *a, **kw: Layout(self._label)
        if name in ("menu", "popover", "label", "separator", "progress", "alignment", "template_ID", "prop_enum", "prop_menu_enum"):
            return lambda *a, **kw: None
        return lambda *a, **kw: Layout(self._label)


class Fake:
    """Stands in for a panel: its own methods work, the layout is ours."""

    def __init__(self, cls, layout):
        self._cls = cls
        self.layout = layout
        self.bl_label = cls.bl_label

    def __getattr__(self, name):
        raw = inspect.getattr_static(self._cls, name)
        if isinstance(raw, (staticmethod, classmethod)):
            return getattr(self._cls, name)
        if callable(raw):
            return lambda *a, **kw: raw(self, *a, **kw)
        return getattr(self._cls, name)


def draw_all(context, tag):
    drawn = 0
    for name in sorted(dir(bpy.types)):
        if not name.startswith("KAT_PT_"):
            continue
        cls = getattr(bpy.types, name)
        me = Fake(cls, Layout(f"{tag}/{name}"))
        if hasattr(cls, "poll") and not cls.poll(context):
            continue
        if "draw_header" in cls.__dict__ or hasattr(cls, "draw_header"):
            try:
                cls.draw_header(me, context)
            except AttributeError as e:
                if "draw_header" not in str(e):
                    raise
        cls.draw(me, context)
        drawn += 1
    return drawn


kat.register()
context = bpy.context
scene = context.scene

# 1. an empty scene
n1 = draw_all(context, "empty")
assert n1 >= 10, n1

# 2. a rigged model with clothes, the face, shape keys, the outfit list and the refit picker filled in
arm = bpy.data.armatures.new("Rig")
rig = bpy.data.objects.new("Armature", arm)
scene.collection.objects.link(rig)
context.view_layer.objects.active = rig
bpy.ops.object.mode_set(mode="EDIT")
for i, bone in enumerate(BONES):
    eb = arm.edit_bones.new(bone)
    eb.head, eb.tail = (0, 0, i * 0.3), (0, 0, (i + 1) * 0.3)
    if i:
        eb.parent = arm.edit_bones[BONES[i - 1]]
bpy.ops.object.mode_set(mode="OBJECT")
meshes = []
for name in ("Body", "Skin", "Shirt"):
    me = bpy.data.meshes.new(name)
    me.from_pydata([(0, 0, 0), (0.1, 0, 0), (0.1, 0.1, 0), (0, 0.1, 0.5)], [], [(0, 1, 2, 3)])
    ob = bpy.data.objects.new(name, me)
    scene.collection.objects.link(ob)
    ob.parent = rig
    ob.modifiers.new("Armature", "ARMATURE").object = rig
    ob.shape_key_add(name="Basis")
    ob.shape_key_add(name="vrc.v_aa")
    meshes.append(ob)
for ob in meshes:
    ob.select_set(True)
rig.select_set(True)
context.view_layer.objects.active = meshes[-1]
outfits = scene.kat_outfits
outfits.skins.add().obj = meshes[1]
outfits.items.add().obj = meshes[2]
scene.kat_refit.rig = rig
scene.kat_custom.base = rig
scene.kat_custom.other = rig
n2 = draw_all(context, "model")
assert n2 >= n1, (n1, n2)

# 3. the same with every yes-or-no and every choice in the options turned the other way
flipped = 0
for group in (scene.kat, scene.kat_refit, scene.kat_outfits, scene.kat_custom):
    for prop in group.bl_rna.properties:
        if prop.type == "BOOLEAN" and not prop.is_readonly and not prop.is_hidden:
            setattr(group, prop.identifier, not getattr(group, prop.identifier))
            flipped += 1
draw_all(context, "flipped")
assert flipped >= 20, flipped

# every panel starts closed, except Updates and Credits
panels = [c for c in (getattr(bpy.types, n) for n in dir(bpy.types) if n.startswith("KAT_PT_"))]
assert len(panels) > 15, len(panels)
for cls in panels:
    closed = "DEFAULT_CLOSED" in getattr(cls, "bl_options", set())
    assert closed == (cls.bl_idname not in ("KAT_PT_updates", "KAT_PT_credits")), cls.bl_idname

kat.unregister()
print(f"drew {n2} panels, checked {seen['props']} properties, {seen['operators']} buttons, {seen['lists']} lists")
print("PANELS DRAW OK")
