# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Krislyz
"""World and prop tools. Run: blender -b --factory-startup --python tests/blender_world.py"""
import json
import os
import sys
import tempfile

import bpy
import numpy as np
from mathutils import Vector

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import strict  # noqa: E402,F401

import kat  # noqa: E402
from kat.modules import world  # noqa: E402

bpy.ops.wm.read_factory_settings(use_empty=True)
kat.register()
scene = bpy.context.scene
ctx = bpy.context


def cube(name, at, size=1.0, mat=None):
    bpy.ops.mesh.primitive_cube_add(size=size, location=at)
    ob = bpy.context.object
    ob.name = name
    if mat:
        ob.data.materials.append(mat)
    return ob


def select(*objs):
    for o in ctx.view_layer.objects:
        o.select_set(False)
    for o in objs:
        o.select_set(True)
    ctx.view_layer.objects.active = objs[0]


brick = bpy.data.materials.new("Brick")
wood = bpy.data.materials.new("Wood")
a = cube("A", (0, 0, 0), mat=brick)
b = cube("B", (2, 0, 0), mat=brick)
c = cube("C", (4, 0, 0), mat=brick)
far = cube("Far", (500, 0, 0), mat=brick)        # another cell: stays apart
other = cube("Other", (1, 3, 0), mat=wood)       # other materials: stays apart
mover = cube("Mover", (3, 3, 0), mat=brick)
mover[world.ROLE] = "dynamic"                    # dynamic: never joined
mod = cube("Modded", (5, 3, 0), mat=brick)
mod.modifiers.new("Bevel", "BEVEL")              # modifiers: left alone

# ---- scan ----
for o in ctx.view_layer.objects:
    o.select_set(False)
s = world.scan(ctx, world.scope(ctx))
assert s["objects"] == 7 and s["triangles"] == 7 * 12 and s["materials"] == 2 and s["slots"] == 7, s
assert world.describe(s)[0][0].startswith("7 objects")
assert bpy.ops.kat.world_scan() == {"FINISHED"} and world.stats["text"]

# ---- combine ----
before = len(world.scope(ctx))
joined, was, now = world.combine_static(ctx, world.scope(ctx), cell=20.0)
assert joined == 1 and was == before and now == before - 2, (joined, was, now)
names = {o.name for o in bpy.data.objects}
assert {"Far", "Other", "Mover", "Modded"} <= names, names
merged = next(o for o in bpy.data.objects if o.name.startswith("Brick_"))
assert len(merged.data.vertices) == 24 and len(merged.data.polygons) == 18

# ---- collision ----
select(merged)
assert bpy.ops.kat.world_collision(kind="BOX") == {"FINISHED"}
proxy = bpy.data.objects[merged.name + "_collision"]
assert proxy.parent is merged and proxy.get("kat_proxy") and proxy.hide_render and proxy.display_type == "WIRE"
xs = [v.co.x for v in proxy.data.vertices]
assert abs(min(xs) - min(v.co.x for v in merged.data.vertices)) < 1e-5 and len(proxy.data.vertices) == 8
bpy.ops.mesh.primitive_uv_sphere_add(radius=1, segments=32, ring_count=16, location=(20, 20, 0))
ball = ctx.object
ball.name = "Ball"
hull = world.make_collision(ctx, ball, "CONVEX", 64)
assert 0 < len(hull.data.polygons) <= 400 and hull.data.vertices
assert world.make_collision(ctx, ball, "CONVEX", 64) is not hull and len([o for o in bpy.data.objects if o.name.startswith("Ball_collision")]) == 1, "redo replaces"
assert world.scope(ctx) and all(not world.is_proxy(o) for o in world.scope(ctx)), "proxies are not scanned as scenery"

# ---- LODs ----
select(ball)
assert bpy.ops.kat.world_lods(min_tris=100) == {"FINISHED"}
lods = sorted(o.name for o in bpy.data.objects if "_LOD" in o.name)
assert lods == ["Ball_LOD0", "Ball_LOD1", "Ball_LOD2"], lods
t0, t1, t2 = (world.triangles(bpy.data.objects[n]) for n in lods)
assert t0 > t1 > t2, (t0, t1, t2)
assert bpy.ops.kat.world_lods.poll() and not [o for o in world.scope(ctx) if o.name == "Ball"], "renamed to LOD0"

# ---- lightmap UVs ----
select(far)
far.data.uv_layers.active  # the default cube has one UV map
assert bpy.ops.kat.world_lightmap() == {"FINISHED"}
assert [u.name for u in far.data.uv_layers] == ["UVMap", world.LIGHTMAP_UV] and far.data.uv_layers.active_index == 0
assert bpy.ops.kat.world_lightmap() == {"FINISHED"}  # a second run does nothing and does not fail
assert len(far.data.uv_layers) == 2

# ---- roles, transforms, origin ----
select(other)
assert bpy.ops.kat.world_role(role="pickup") == {"FINISHED"} and other[world.ROLE] == "pickup"
big = cube("Tilted", (10, 10, 5), mat=wood)
big.scale = (2, 2, 2)
big.rotation_euler = (0, 0, 0.5)
select(big)
assert bpy.ops.kat.world_fix() == {"FINISHED"} and all(abs(x - 1) < 1e-6 for x in big.scale) and abs(big.rotation_euler.z) < 1e-6
before_world = np.array([(big.matrix_world @ v.co)[:] for v in big.data.vertices])
point = world.origin_to(big, "BOTTOM")
after_world = np.array([(big.matrix_world @ v.co)[:] for v in big.data.vertices])
assert np.allclose(before_world, after_world, atol=1e-5), "the mesh does not move when the origin does"
assert abs(big.matrix_world.translation.z - before_world[:, 2].min()) < 1e-5, "origin sits on the bottom"

# ---- props ----
for kind, w, d, h in (("CUBE", 1, 1, 1), ("FLOOR", 4, 4, 0.2), ("WALL", 4, 0.2, 3), ("STAIRS", 1.2, 2.4, 1.2),
                      ("RAMP", 1.5, 3, 1), ("CYLINDER", 0.6, 0.6, 3), ("ARCH", 3, 0.25, 3)):
    me = world.build_prop(kind, w, d, h, 6)
    pts = np.array([v.co[:] for v in me.vertices])
    size = pts.max(axis=0) - pts.min(axis=0)
    assert abs(pts[:, 2].min()) < 1e-5, (kind, "sits on the floor")
    assert abs(size[2] - h) < 1e-4, (kind, size)
    assert abs(size[0] - w) < 1e-4 or kind == "CYLINDER", (kind, size)
    assert len(me.uv_layers) == 1 and len(me.polygons) > 0
scene.cursor.location = (7, 7, 0)
assert bpy.ops.kat.prop_add(kind="STAIRS", width=1.2, depth=2.4, height=1.2, steps=6) == {"FINISHED"}
stairs = ctx.object
assert stairs.get(world.ROLE) == "static" and stairs.location.x == 7 and bpy.data.objects.get(stairs.name + "_collision")

# prepare: apply, origin, collision, role
rock = cube("Rock", (30, 0, 3))
rock.scale = (1, 2, 1)
select(rock)
assert bpy.ops.kat.prop_prepare(origin="BOTTOM", collision="BOX", role="pickup", lods=False) == {"FINISHED"}
assert rock[world.ROLE] == "pickup" and bpy.data.objects.get("Rock_collision") and abs(rock.scale.y - 1) < 1e-6

# ---- sidecar and export ----
for o in ctx.view_layer.objects:
    o.select_set(False)
objs = world.scope(ctx, proxies=True)
side = world.sidecar(ctx, objs)
assert "Ball_LOD0" in side["static"] and "Rock" in side["pickups"] and "Other" in side["pickups"] and "Mover" in side["dynamic"]
assert {"mesh": "Rock_collision", "target": "Rock", "kind": "BOX"} in side["colliders"]
assert side["lods"] == [{"group": "Ball", "levels": ["Ball_LOD0", "Ball_LOD1", "Ball_LOD2"]}], side["lods"]
assert "Far" in side["lightmap"] and "Other" not in side["lightmap"]
out = os.path.join(tempfile.mkdtemp(prefix="kat_world_"), "World.fbx")
select(rock, stairs)
assert bpy.ops.kat.export_world(filepath=out) == {"FINISHED"}
data = json.load(open(os.path.splitext(out)[0] + ".kat.json", encoding="utf-8"))
assert data["world"]["pickups"] == ["Rock"] and "Stairs_collision" in data["world"]["meshes"] or any(
    m.endswith("_collision") for m in data["world"]["meshes"]), data["world"]
assert os.path.getsize(out) > 1000

kat.unregister()
print("WORLD OK")
