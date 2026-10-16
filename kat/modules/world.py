# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Krislyz
"""Worlds and props for Unity (and VRChat worlds): scan what a scene costs, combine static meshes, make collision
proxies, LOD copies and lightmap UVs, make props, and export with a sidecar the KAT Unity package turns into
colliders, static flags, LOD groups, lightmap UVs and pickups.

Roles live on the objects as the custom property kat_role: static, dynamic or pickup. Collision proxies are named
<mesh>_collision and parented to their mesh, LOD copies follow Unity's own Name_LOD0, Name_LOD1 ... convention, which
Unity turns into a LODGroup by itself.
"""
import json
import math
import os

import bmesh
import bpy
import numpy as np
from bpy.props import BoolProperty, EnumProperty, FloatProperty, IntProperty
from bpy_extras.io_utils import ExportHelper
from mathutils import Matrix, Vector

from ..core import vault, vrc
from ..core.safe import operator_guard
from . import export, textures, uiutil

ROLE = "kat_role"
ROLES = [("static", "Static", "Never moves: batched, baked into lightmaps, occludes"),
         ("dynamic", "Dynamic", "Moves or animates: left out of combining and static baking"),
         ("pickup", "Pickup", "Can be picked up: gets a rigidbody and a collider in Unity")]
COLLISION = "_collision"
COLLECTION = "KAT_collision"
LIGHTMAP_UV = "KAT_lightmap"
LOD_SUFFIX = "_LOD"
stats = {"text": [], "alert": False}


def is_world_mesh(ob):
    return ob.type == "MESH" and not any(m.type == "ARMATURE" for m in ob.modifiers)


def is_proxy(ob):
    return ob.name.endswith(COLLISION) or ob.get("kat_proxy") is True


def scope(context, proxies=False):
    """The selected meshes, or every visible mesh in the scene when nothing is selected. Collision proxies are left
    out unless asked for."""
    chosen = [o for o in context.selected_objects if is_world_mesh(o)]
    pool = chosen or [o for o in context.view_layer.objects if is_world_mesh(o) and o.visible_get()]
    return pool if proxies else [o for o in pool if not is_proxy(o)]


def triangles(ob):
    me = ob.data
    me.calc_loop_triangles()
    return len(me.loop_triangles)


def role_of(ob):
    return ob.get(ROLE, "static")


# ---- scanning ---------------------------------------------------------------------------------------------------

def scan(context, objs):
    """What the scene costs: triangles, objects, draw calls before and after batching, textures, lights."""
    tris = sum(triangles(o) for o in objs)
    slots = sum(max(1, len([m for m in o.data.materials if m])) for o in objs)
    mats = {m.name for o in objs for m in o.data.materials if m}
    images = {}
    for mat in textures.materials_of(objs):
        for node in textures.image_nodes(mat):
            if node.image is not None and node.image.size[0] > 0:
                images[node.image.name] = tuple(node.image.size)
    mb = vrc.texture_mb(list(images.values()))
    lights = sum(1 for o in context.view_layer.objects if o.type == "LIGHT")
    singles = sum(1 for o in objs if o.data.users == 1)
    statics = [o for o in objs if role_of(o) == "static"]
    return dict(objects=len(objs), triangles=tris, slots=slots, materials=len(mats), textures=len(images), texture_mb=mb,
                lights=lights, static=len(statics), unique_meshes=len({o.data for o in objs}), single_user=singles,
                big=sorted(((triangles(o), o.name) for o in objs), reverse=True)[:5])


def describe(s):
    """Plain advice lines for a scan result. Each is (text, needs attention)."""
    lines = [(f"{s['objects']} objects, {s['triangles']:,} triangles", s["triangles"] > 1_000_000),
             (f"{s['slots']} material slots, {s['materials']} materials", s["slots"] > 4 * max(1, s["materials"])),
             (f"{s['textures']} textures, about {s['texture_mb']:.0f} MB", s["texture_mb"] > 512)]
    if s["slots"] > s["materials"] * 2:
        lines.append((f"Combining static meshes could cut draw calls from {s['slots']} toward {s['materials']}", True))
    if s["lights"] > 8:
        lines.append((f"{s['lights']} lights: bake them, real-time lights are costly", True))
    return lines


# ---- combining static meshes ------------------------------------------------------------------------------------

def combinable(ob):
    if not is_world_mesh(ob) or role_of(ob) != "static" or ob.type != "MESH" or is_proxy(ob):
        return False
    if ob.get("kat_name") or (LOD_SUFFIX in ob.name and ob.name.rsplit(LOD_SUFFIX, 1)[1].isdigit()):
        return False  # LOD levels have to stay separate objects
    if ob.data.shape_keys or ob.modifiers or ob.children or ob.data.library:
        return False
    return ob.visible_get()


def combine_groups(objs, cell, max_verts):
    """{(materials, cell x, cell y, cell z, part): [objects]} for the meshes that can be joined: same materials, close
    together (so culling still works) and small enough once joined."""
    buckets = {}
    for ob in objs:
        if not combinable(ob):
            continue
        mats = tuple(sorted({m.name for m in ob.data.materials if m}))
        pos = ob.matrix_world.translation
        key = (mats,) + tuple(int(math.floor(c / cell)) for c in pos)
        buckets.setdefault(key, []).append(ob)
    out = {}
    for key, members in buckets.items():
        part, count, current = 0, 0, []
        for ob in sorted(members, key=lambda o: o.name):
            n = len(ob.data.vertices)
            if current and count + n > max_verts:
                out[key + (part,)] = current
                part, count, current = part + 1, 0, []
            current.append(ob)
            count += n
        if current:
            out[key + (part,)] = current
    return {k: v for k, v in out.items() if len(v) > 1}


def combine_static(context, objs, cell=20.0, max_verts=60000):
    """Join static meshes that share materials and sit near each other. Returns (groups joined, objects before, after)."""
    groups = combine_groups(objs, cell, max_verts)
    before = len(objs)
    joined = 0
    for key, members in groups.items():
        for ob in members:
            if ob.data.users > 1:
                ob.data = ob.data.copy()
        target = members[0]
        label = (key[0][0] if key[0] else "mesh")
        with context.temp_override(active_object=target, object=target, selected_objects=members,
                                   selected_editable_objects=members):
            bpy.ops.object.join()
        target.name = f"{label}_{key[1]}_{key[2]}_{key[3]}" + (f"_{key[4]}" if key[4] else "")
        joined += 1
    after = before - sum(len(m) - 1 for m in groups.values())
    return joined, before, after


# ---- collision and LODs -----------------------------------------------------------------------------------------

def collection_for_proxies(context):
    coll = bpy.data.collections.get(COLLECTION)
    if coll is None:
        coll = bpy.data.collections.new(COLLECTION)
        context.scene.collection.children.link(coll)
    return coll


def make_collision(context, ob, kind="CONVEX", max_faces=64):
    """A collision proxy for `ob`: its bounding box, or its convex hull simplified to about `max_faces` faces.
    Named <mesh>_collision, parented to the mesh, drawn as a wire box and left out of renders."""
    old = bpy.data.objects.get(ob.name + COLLISION)
    if old is not None:
        bpy.data.objects.remove(old)
    bm = bmesh.new()
    try:
        if kind == "BOX":
            lo = Vector(min(v[i] for v in ob.bound_box) for i in range(3))
            hi = Vector(max(v[i] for v in ob.bound_box) for i in range(3))
            bmesh.ops.create_cube(bm, size=2.0)
            for v in bm.verts:
                v.co = Vector((hi.x if v.co.x > 0 else lo.x, hi.y if v.co.y > 0 else lo.y, hi.z if v.co.z > 0 else lo.z))
        else:
            bm.from_mesh(ob.data)
            hull = bmesh.ops.convex_hull(bm, input=bm.verts, use_existing_faces=False)
            doomed = [g for g in hull["geom_interior"] + hull["geom_unused"] if isinstance(g, bmesh.types.BMVert)]
            bmesh.ops.delete(bm, geom=doomed, context="VERTS")
            angle = 0.05
            while len(bm.faces) > max_faces and angle < 1.5:
                bmesh.ops.dissolve_limit(bm, angle_limit=angle, verts=bm.verts, edges=bm.edges)
                angle *= 1.6
            bmesh.ops.triangulate(bm, faces=bm.faces)
        me = bpy.data.meshes.new(ob.name + COLLISION)
        bm.to_mesh(me)
    finally:
        bm.free()
    proxy = bpy.data.objects.new(ob.name + COLLISION, me)
    collection_for_proxies(context).objects.link(proxy)
    proxy.parent = ob
    proxy.matrix_parent_inverse = Matrix.Identity(4)  # same space as the mesh, so it follows it
    proxy.display_type = "WIRE"
    proxy.hide_render = True
    proxy["kat_proxy"] = True
    proxy["kat_collision_kind"] = kind
    return proxy


def make_lods(context, ob, ratios=(0.5, 0.2)):
    """Rename `ob` to Name_LOD0 and make Name_LOD1, Name_LOD2 ... decimated copies. Unity groups them into a LODGroup
    by itself. Returns the new objects."""
    base = ob.get("kat_name") or ob.name
    if ob.name.rsplit(LOD_SUFFIX, 1)[0] != ob.name:
        base = ob.name.rsplit(LOD_SUFFIX, 1)[0]
    for name in [o.name for o in bpy.data.objects if o.name.startswith(base + LOD_SUFFIX) and o is not ob]:
        bpy.data.objects.remove(bpy.data.objects[name])
    ob["kat_name"] = base
    ob.name = f"{base}{LOD_SUFFIX}0"
    made = []
    for level, ratio in enumerate(ratios, start=1):
        me = ob.data.copy()
        me.name = f"{base}{LOD_SUFFIX}{level}"
        copy = ob.copy()
        copy.data = me
        copy.name = me.name
        for child in list(copy.children):
            child.parent = None
        for coll in ob.users_collection:
            coll.objects.link(copy)
        mod = copy.modifiers.new("KAT_lod", "DECIMATE")
        mod.ratio = ratio
        with context.temp_override(object=copy, active_object=copy, selected_objects=[copy]):
            bpy.ops.object.modifier_apply(modifier=mod.name)
        made.append(copy)
    return made


# ---- lightmap UVs and transforms --------------------------------------------------------------------------------

def add_lightmap_uv(context, ob, margin=0.03):
    """A second UV map for lightmapping, from Smart UV Project (no overlaps, unlike the texture UVs). Returns False
    when the mesh already has two UV maps."""
    me = ob.data
    if len(me.uv_layers) >= 2:
        return False
    keep = me.uv_layers.active_index if me.uv_layers else 0
    layer = me.uv_layers.new(name=LIGHTMAP_UV)
    me.uv_layers.active = layer
    layer_objects = context.view_layer.objects
    was = [(o, o.select_get()) for o in layer_objects]
    for o in layer_objects:  # edit mode takes every selected mesh with it, and they would all be unwrapped
        o.select_set(o is ob)
    previous = layer_objects.active
    layer_objects.active = ob
    try:
        with context.temp_override(object=ob, active_object=ob, selected_objects=[ob], selected_editable_objects=[ob]):
            bpy.ops.object.mode_set(mode="EDIT")
            try:
                bpy.ops.mesh.select_all(action="SELECT")
                bpy.ops.uv.smart_project(angle_limit=math.radians(66), island_margin=margin)
            finally:
                bpy.ops.object.mode_set(mode="OBJECT")
    finally:
        for o, state in was:
            o.select_set(state)
        layer_objects.active = previous
    if len(me.uv_layers) > keep:
        me.uv_layers.active_index = keep  # the texture UVs stay the active (first) map
    return True


def apply_transforms(context, objs):
    done = 0
    for ob in objs:
        if ob.data.users > 1:
            ob.data = ob.data.copy()
    with context.temp_override(active_object=objs[0], object=objs[0], selected_objects=objs, selected_editable_objects=objs):
        bpy.ops.object.transform_apply(location=False, rotation=True, scale=True)
        done = len(objs)
    return done


def origin_to(ob, where="BOTTOM"):
    """Move the origin to the bottom centre of the bounds (props sit on the floor there) or to the centre, keeping the
    mesh where it is in the world."""
    if ob.data.users > 1:
        ob.data = ob.data.copy()
    pts = np.array([v[:] for v in ob.bound_box])
    lo, hi = pts.min(axis=0), pts.max(axis=0)
    point = Vector(((lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, lo[2] if where == "BOTTOM" else (lo[2] + hi[2]) / 2))
    shift = Matrix.Translation(-point)
    ob.data.transform(shift)
    placed = ob.matrix_world.copy()
    placed.translation = ob.matrix_world @ point
    ob.matrix_world = placed
    for child in ob.children:
        child.matrix_parent_inverse = shift @ child.matrix_parent_inverse
    return point


# ---- props ------------------------------------------------------------------------------------------------------

def box_uv(me, scale=1.0):
    """World-sized box-projected UVs (one metre is `scale` of a UV unit), so textures tile at the same size everywhere."""
    layer = me.uv_layers.active or me.uv_layers.new(name="UVMap")
    for poly in me.polygons:
        n = poly.normal
        axis = max(range(3), key=lambda i: abs(n[i]))
        keep = [i for i in range(3) if i != axis]
        for li in poly.loop_indices:
            co = me.vertices[me.loops[li].vertex_index].co
            layer.data[li].uv = (co[keep[0]] * scale, co[keep[1]] * scale)


PROPS = [("CUBE", "Cube", "1 m cube"), ("FLOOR", "Floor", "A flat slab"), ("WALL", "Wall", "A wall section"),
         ("STAIRS", "Stairs", "Steps up along Y"), ("RAMP", "Ramp", "A slope up along Y"),
         ("CYLINDER", "Pillar", "A round pillar"), ("ARCH", "Doorway", "A wall with a doorway")]


def build_prop(kind, width, depth, height, steps):
    """A prop of real size in metres, sitting on the floor with its origin at the bottom centre."""
    bm = bmesh.new()
    try:
        if kind == "CYLINDER":
            bmesh.ops.create_cone(bm, cap_ends=True, segments=24, radius1=width / 2, radius2=width / 2, depth=height)
            bmesh.ops.translate(bm, verts=bm.verts, vec=(0, 0, height / 2))
        elif kind == "STAIRS":
            for i in range(steps):
                r = bmesh.ops.create_cube(bm, size=1.0)
                s = Vector((width, depth / steps, height / steps * (i + 1)))
                for v in r["verts"]:
                    v.co = Vector((v.co.x * s.x, (v.co.y + 0.5 + i) * s.y - depth / 2, (v.co.z + 0.5) * s.z))
        elif kind == "RAMP":
            hw = width / 2
            v = [bm.verts.new(p) for p in ((-hw, -depth / 2, 0), (hw, -depth / 2, 0), (hw, depth / 2, 0), (-hw, depth / 2, 0),
                                           (-hw, depth / 2, height), (hw, depth / 2, height))]
            for f in ((v[0], v[1], v[2], v[3]), (v[3], v[2], v[5], v[4]), (v[0], v[3], v[4]), (v[1], v[5], v[2]),
                      (v[0], v[4], v[5], v[1])):
                bm.faces.new(f)
        elif kind == "ARCH":
            hw, door, dh = width / 2, min(1.0, width / 3), min(2.1, height * 0.8)
            for (x0, x1, z0, z1) in ((-hw, -door / 2, 0, height), (door / 2, hw, 0, height), (-door / 2, door / 2, dh, height)):
                r = bmesh.ops.create_cube(bm, size=1.0)
                for v in r["verts"]:
                    v.co = Vector(((x0 + x1) / 2 + v.co.x * (x1 - x0), v.co.y * depth, (z0 + z1) / 2 + v.co.z * (z1 - z0)))
        else:  # CUBE, FLOOR, WALL are boxes
            r = bmesh.ops.create_cube(bm, size=1.0)
            for v in r["verts"]:
                v.co = Vector((v.co.x * width, v.co.y * depth, (v.co.z + 0.5) * height))
        bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
        me = bpy.data.meshes.new(kind.title())
        bm.to_mesh(me)
    finally:
        bm.free()
    box_uv(me)
    for p in me.polygons:
        p.use_smooth = kind == "CYLINDER"
    return me


# ---- operators --------------------------------------------------------------------------------------------------

class KAT_OT_world_scan(bpy.types.Operator):
    bl_idname = "kat.world_scan"
    bl_label = "Scan scene"
    bl_description = "Count what the selected meshes (or the whole scene) cost: triangles, draw calls, textures, lights"

    @operator_guard
    def execute(self, context):
        objs = scope(context)
        if not objs:
            raise ValueError("No meshes in the scene")
        s = scan(context, objs)
        lines = describe(s)
        stats.update(text=lines, alert=any(a for _, a in lines), big=s["big"])
        self.report({"INFO"}, "; ".join(t for t, _ in lines))
        return {"FINISHED"}


class KAT_OT_world_combine(bpy.types.Operator):
    bl_idname = "kat.world_combine"
    bl_label = "Combine static meshes"
    bl_description = ("Join static meshes that share materials and sit close together, so Unity draws fewer objects. "
                      "Dynamic and pickup meshes, and meshes with modifiers or shape keys, are left alone")
    bl_options = {"REGISTER", "UNDO"}
    cell: FloatProperty(name="Cell size (m)", default=20.0, min=1.0, max=500.0,
                        description="Only meshes inside the same cell are joined, so culling still works")
    max_verts: IntProperty(name="Most vertices per mesh", default=60000, min=1000, max=500000)

    @operator_guard
    def execute(self, context):
        objs = scope(context)
        joined, before, after = combine_static(context, objs, self.cell, self.max_verts)
        if not joined:
            raise ValueError("Nothing to combine: no static meshes share materials inside one cell")
        self.report({"INFO"}, f"Joined {joined} groups: {before} objects to {after}")
        return {"FINISHED"}


class KAT_OT_world_collision(bpy.types.Operator):
    bl_idname = "kat.world_collision"
    bl_label = "Make collision"
    bl_description = "Add a simple collision proxy (<name>_collision) to each selected mesh. Unity turns it into a collider"
    bl_options = {"REGISTER", "UNDO"}
    kind: EnumProperty(name="Shape", items=[("CONVEX", "Convex hull", "Wraps the shape tightly"),
                                            ("BOX", "Box", "The bounding box")], default="CONVEX")
    max_faces: IntProperty(name="Most faces", default=64, min=6, max=512)

    @operator_guard
    def execute(self, context):
        objs = [o for o in scope(context) if role_of(o) != "dynamic" or o.get(ROLE) == "pickup"]
        if not objs:
            raise ValueError("Select the meshes that need collision")
        for ob in objs:
            make_collision(context, ob, self.kind, self.max_faces)
        self.report({"INFO"}, f"Made {len(objs)} collision proxies")
        return {"FINISHED"}


class KAT_OT_world_lods(bpy.types.Operator):
    bl_idname = "kat.world_lods"
    bl_label = "Make LODs"
    bl_description = ("Rename each selected mesh to Name_LOD0 and make lighter copies Name_LOD1, Name_LOD2. Unity "
                      "builds the LOD group from the names")
    bl_options = {"REGISTER", "UNDO"}
    min_tris: IntProperty(name="Only above (triangles)", default=2000, min=100)
    first: FloatProperty(name="LOD1 keeps", default=0.5, min=0.05, max=0.9)
    second: FloatProperty(name="LOD2 keeps", default=0.2, min=0.02, max=0.8)

    @operator_guard
    def execute(self, context):
        objs = [o for o in scope(context) if triangles(o) >= self.min_tris and not o.get("kat_name")]
        if not objs:
            raise ValueError(f"No selected mesh has {self.min_tris:,} triangles or more")
        count = 0
        for ob in objs:
            count += len(make_lods(context, ob, (self.first, self.second)))
        self.report({"INFO"}, f"Made {count} LOD meshes for {len(objs)} objects")
        return {"FINISHED"}


class KAT_OT_world_lightmap(bpy.types.Operator):
    bl_idname = "kat.world_lightmap"
    bl_label = "Lightmap UVs"
    bl_description = "Give each selected static mesh a second UV map without overlaps, for baked lighting in Unity"
    bl_options = {"REGISTER", "UNDO"}

    @operator_guard
    def execute(self, context):
        objs = [o for o in scope(context) if role_of(o) == "static"]
        if not objs:
            raise ValueError("Select static meshes")
        made = sum(1 for ob in objs if add_lightmap_uv(context, ob))
        self.report({"INFO"}, f"Added a lightmap UV map to {made} meshes ({len(objs) - made} already had two)")
        return {"FINISHED"}


class KAT_OT_world_role(bpy.types.Operator):
    bl_idname = "kat.world_role"
    bl_label = "Set role"
    bl_description = "Mark the selected meshes as static, dynamic or pickup"
    bl_options = {"REGISTER", "UNDO"}
    role: EnumProperty(name="Role", items=ROLES, default="static")

    @operator_guard
    def execute(self, context):
        objs = [o for o in context.selected_objects if o.type == "MESH"]
        if not objs:
            raise ValueError("Select meshes first")
        for ob in objs:
            ob[ROLE] = self.role
        self.report({"INFO"}, f"{len(objs)} meshes are now {self.role}")
        return {"FINISHED"}


class KAT_OT_world_fix(bpy.types.Operator):
    bl_idname = "kat.world_fix"
    bl_label = "Apply scale and rotation"
    bl_description = "Bake rotation and scale into the selected meshes (linked copies become single-user first)"
    bl_options = {"REGISTER", "UNDO"}

    @operator_guard
    def execute(self, context):
        objs = scope(context)
        if not objs:
            raise ValueError("Select meshes first")
        self.report({"INFO"}, f"Applied rotation and scale on {apply_transforms(context, objs)} objects")
        return {"FINISHED"}


class KAT_OT_prop_add(bpy.types.Operator):
    bl_idname = "kat.prop_add"
    bl_label = "Add prop"
    bl_description = "Add a simple prop of real size, origin at the bottom centre, with world-sized UVs and a collision proxy"
    bl_options = {"REGISTER", "UNDO"}
    kind: EnumProperty(name="Kind", items=PROPS, default="CUBE")
    width: FloatProperty(name="Width (m)", default=1.0, min=0.05, max=100.0)
    depth: FloatProperty(name="Depth (m)", default=1.0, min=0.02, max=100.0)
    height: FloatProperty(name="Height (m)", default=1.0, min=0.02, max=100.0)
    steps: IntProperty(name="Steps", default=6, min=2, max=40)
    collision: BoolProperty(name="Collision", default=True)

    @operator_guard
    def execute(self, context):
        me = build_prop(self.kind, self.width, self.depth, self.height, self.steps)
        ob = bpy.data.objects.new(me.name, me)
        context.collection.objects.link(ob)
        ob.location = context.scene.cursor.location
        ob[ROLE] = "static"
        for o in context.view_layer.objects:
            o.select_set(False)
        ob.select_set(True)
        context.view_layer.objects.active = ob
        if self.collision:
            make_collision(context, ob, "BOX" if self.kind in {"CUBE", "FLOOR", "WALL", "RAMP"} else "CONVEX")
        return {"FINISHED"}


class KAT_OT_prop_prepare(bpy.types.Operator):
    bl_idname = "kat.prop_prepare"
    bl_label = "Prepare prop"
    bl_description = ("Get the selected meshes ready as Unity props: apply scale and rotation, put the origin on the "
                      "floor, add collision, optionally LODs, and set what they do")
    bl_options = {"REGISTER", "UNDO"}
    origin: EnumProperty(name="Origin", items=[("BOTTOM", "Bottom centre", ""), ("CENTER", "Centre", ""), ("KEEP", "Leave", "")],
                         default="BOTTOM")
    collision: EnumProperty(name="Collision", items=[("NONE", "None", ""), ("BOX", "Box", ""), ("CONVEX", "Convex hull", "")],
                            default="CONVEX")
    lods: BoolProperty(name="LODs", default=False)
    role: EnumProperty(name="Role", items=ROLES, default="static")

    @operator_guard
    def execute(self, context):
        objs = [o for o in context.selected_objects if is_world_mesh(o) and not is_proxy(o)]
        if not objs:
            raise ValueError("Select the meshes to prepare")
        apply_transforms(context, objs)
        for ob in objs:
            if self.origin != "KEEP":
                origin_to(ob, self.origin)
            ob[ROLE] = self.role
            if self.collision != "NONE":
                make_collision(context, ob, self.collision)
            if self.lods and triangles(ob) >= 500:
                make_lods(context, ob)
        self.report({"INFO"}, f"Prepared {len(objs)} props")
        return {"FINISHED"}


def sidecar(context, objs):
    """The world section of the .kat.json: what Unity should do with each object."""
    names = {o.name for o in objs}
    lods = {}
    for o in objs:
        if LOD_SUFFIX in o.name and o.name.rsplit(LOD_SUFFIX, 1)[1].isdigit():
            lods.setdefault(o.name.rsplit(LOD_SUFFIX, 1)[0], []).append(o.name)
    return {
        "static": [o.name for o in objs if role_of(o) == "static" and not is_proxy(o)],
        "dynamic": [o.name for o in objs if role_of(o) == "dynamic" and not is_proxy(o)],
        "pickups": [o.name for o in objs if role_of(o) == "pickup" and not is_proxy(o)],
        "colliders": [{"mesh": o.name, "target": o.parent.name if o.parent else "", "kind": o.get("kat_collision_kind", "CONVEX")}
                      for o in objs if is_proxy(o)],
        "lods": [{"group": g, "levels": sorted(v)} for g, v in sorted(lods.items())],
        "lightmap": [o.name for o in objs if not is_proxy(o) and LIGHTMAP_UV in o.data.uv_layers],
        "meshes": sorted(names),
    }


class KAT_OT_export_world(bpy.types.Operator, ExportHelper):
    bl_idname = "kat.export_world"
    bl_label = "Export world or props"
    bl_description = "Export the selected meshes (or all) as an FBX with a .kat.json the KAT Unity package reads"
    filename_ext = ".fbx"
    filter_glob: bpy.props.StringProperty(default="*.fbx", options={"HIDDEN"})

    def invoke(self, context, event):
        self.filepath = "World.fbx"
        return super().invoke(context, event)

    @operator_guard
    def execute(self, context):
        objs = scope(context, proxies=True)
        # proxies and LOD copies of the chosen meshes come along
        extra = [o for o in bpy.data.objects if o.type == "MESH" and (is_proxy(o) and o.parent in objs)]
        objs = list(dict.fromkeys(objs + extra))
        if not objs:
            raise ValueError("No meshes to export")
        layer = context.view_layer
        was = [(o, o.select_get()) for o in layer.objects]
        json_path = os.path.splitext(self.filepath)[0] + ".kat.json"
        scenery = [o for o in objs if not is_proxy(o)]
        data = {"kat": export.SIDECAR_VERSION, "blender": bpy.app.version_string, "world": sidecar(context, objs),
                "materials": sorted({m.name for o in scenery for m in o.data.materials if m}),
                "materialTextures": export.material_textures(scenery, os.path.dirname(os.path.abspath(self.filepath)))}
        try:
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            for o in layer.objects:
                o.select_set(False)
            for o in objs:
                o.hide_set(False)
                o.select_set(True)
            options = dict(export.fbx_options())
            options.update(object_types={"MESH", "EMPTY"})
            bpy.ops.export_scene.fbx(filepath=self.filepath, **options)
        except BaseException:
            if os.path.exists(json_path):
                os.remove(json_path)
            raise
        finally:
            for o, state in was:
                o.select_set(state)
        vault.record(self.filepath, "export", textures=textures.files_of([o for o in objs if not is_proxy(o)]))
        self.report({"INFO"}, f"Wrote {os.path.basename(self.filepath)} and its .kat.json with {len(objs)} objects")
        return {"FINISHED"}


class WorldPanel:
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "KAT"


class KAT_PT_world(WorldPanel, bpy.types.Panel):
    bl_label = "World"
    bl_idname = "KAT_PT_world"
    bl_order = 1080
    bl_options = {"DEFAULT_CLOSED"}

    def draw_header(self, context):
        self.layout.label(icon="WORLD")

    def draw(self, context):
        layout = self.layout
        row = layout.row(align=True)
        row.operator("kat.world_scan", icon="VIEWZOOM")
        for text, alert in stats["text"]:
            r = layout.row()
            r.alert = alert
            r.label(text=text, icon="ERROR" if alert else "CHECKMARK")
        box = layout.box()
        box.label(text="Optimize", icon="MOD_DECIM")
        col = box.column(align=True)
        col.operator("kat.world_combine", icon="AUTOMERGE_ON")
        col.operator("kat.world_lods", icon="MOD_DECIM")
        col.operator("kat.world_collision", icon="MESH_CUBE")
        col.operator("kat.world_lightmap", icon="LIGHT_AREA")
        col.operator("kat.world_fix", icon="OBJECT_ORIGIN")
        box = layout.box()
        box.label(text="Role of the selected meshes", icon="OBJECT_DATA")
        row = box.row(align=True)
        for ident, label, _ in ROLES:
            row.operator("kat.world_role", text=label).role = ident
        layout.operator("kat.export_world", icon="EXPORT")


class KAT_PT_props(WorldPanel, bpy.types.Panel):
    bl_label = "Props"
    bl_idname = "KAT_PT_props"
    bl_parent_id = "KAT_PT_world"
    bl_order = 1081
    bl_options = {"DEFAULT_CLOSED"}

    def draw_header(self, context):
        self.layout.label(icon="MESH_CUBE")

    def draw(self, context):
        layout = self.layout
        box = layout.box()
        box.label(text="Add", icon="ADD")
        grid = box.grid_flow(row_major=True, columns=2, even_columns=True, align=True)
        presets = {"CUBE": (1, 1, 1), "FLOOR": (4, 4, 0.2), "WALL": (4, 0.2, 3), "STAIRS": (1.2, 2.4, 1.2),
                   "RAMP": (1.5, 3, 1), "CYLINDER": (0.6, 0.6, 3), "ARCH": (3, 0.25, 3)}
        for ident, label, _ in PROPS:
            op = grid.operator("kat.prop_add", text=label)
            op.kind = ident
            op.width, op.depth, op.height = presets[ident]
        o = context.scene.kat
        box = layout.box()
        box.label(text="Make the selected meshes props", icon="CHECKMARK")
        box.prop(o, "prop_origin", text="Origin")
        box.prop(o, "prop_collision", text="Collision")
        box.prop(o, "prop_role", text="Role")
        box.prop(o, "prop_lods")
        op = box.operator("kat.prop_prepare", icon="MODIFIER")
        op.origin, op.collision, op.role, op.lods = o.prop_origin, o.prop_collision, o.prop_role, o.prop_lods
        uiutil.paragraph(box, context, "Applies scale and rotation first. Pickups get a rigidbody in Unity.")


classes = (KAT_OT_world_scan, KAT_OT_world_combine, KAT_OT_world_collision, KAT_OT_world_lods, KAT_OT_world_lightmap,
           KAT_OT_world_role, KAT_OT_world_fix, KAT_OT_prop_add, KAT_OT_prop_prepare, KAT_OT_export_world,
           KAT_PT_world, KAT_PT_props)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
