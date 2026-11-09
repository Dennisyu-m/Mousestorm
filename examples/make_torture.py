# SPDX-License-Identifier: GPL-3.0-or-later
"""Builds .blend files that break naive tools. Each one is a real thing that
turns up in generated scenes.

    blender -b --factory-startup -P examples/make_torture.py -- OUTPUT_DIR
"""
import math
import os
import sys

import bpy

out = sys.argv[sys.argv.index("--") + 1]
os.makedirs(out, exist_ok=True)


def wipe():
    bpy.ops.wm.read_factory_settings(use_empty=True)


def save(name):
    bpy.ops.wm.save_as_mainfile(filepath=os.path.join(out, name + ".blend"))


def mesh(name, verts, faces):
    me = bpy.data.meshes.new(name)
    me.from_pydata(verts, [], faces)
    me.update()
    ob = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(ob)
    return ob


# ---- a watch movement modelled in metres: the whole scene is 4 cm across
wipe()
for i in range(14):
    a = i / 14 * math.tau
    bpy.ops.mesh.primitive_cylinder_add(radius=0.0035, depth=0.002, location=(math.cos(a) * 0.016, math.sin(a) * 0.016, 0))
bpy.ops.mesh.primitive_cylinder_add(radius=0.012, depth=0.003, location=(0, 0, 0))
save("tiny_scale")

# ---- a city block in centimetres-as-units, far from the origin
wipe()
for x in range(6):
    for y in range(6):
        bpy.ops.mesh.primitive_cube_add(size=2000, location=(120000 + x * 3500, -80000 + y * 3500, 1000 + (x * y) * 40))
        bpy.context.object.scale.z = 1 + (x + y) % 4
save("huge_far")

# ---- the broken-geometry drawer
wipe()
mesh("flat_plane_in_z", [(0, 0, 0), (2, 0, 0), (2, 2, 0), (0, 2, 0)], [(0, 1, 2, 3)])
mesh("zero_area_face", [(3, 0, 0), (3, 0, 0), (3, 0, 0)], [(0, 1, 2)])
mesh("collinear", [(4, 0, 0), (5, 0, 0), (6, 0, 0)], [(0, 1, 2)])
mesh("nan_vertices", [(float("nan"), 0, 0), (7, 0, 0), (7, 1, float("inf"))], [(0, 1, 2)])
mesh("wire_only", [(8, 0, 0), (9, 1, 1)], [])
bpy.ops.mesh.primitive_cube_add(size=1, location=(0, 4, 0))
bpy.context.object.scale = (0, 1, 1)
bpy.context.object.name = "zero_scale_axis"
bpy.ops.mesh.primitive_cube_add(size=1, location=(3, 4, 0))
bpy.context.object.scale = (-1, 1, 1)
bpy.context.object.name = "mirrored_negative_scale"
bpy.ops.mesh.primitive_monkey_add(location=(6, 4, 1))
bpy.context.object.name = "ordinary_object"
save("broken_geometry")

# ---- one enormous mesh: several million triangles straight from a generator
wipe()
bpy.ops.mesh.primitive_monkey_add()
ob = bpy.context.object
ob.modifiers.new("s", "SUBSURF").levels = 6
ob.modifiers["s"].render_levels = 6
bpy.ops.object.shade_smooth()
bpy.ops.object.modifier_apply(modifier="s")
print("triangles:", sum(len(p.vertices) - 2 for p in ob.data.polygons))
save("dense_mesh")

# ---- only things that cannot be rendered as surfaces
wipe()
bpy.ops.object.empty_add()
bpy.ops.object.light_add(type="POINT")
bpy.ops.object.camera_add()
save("no_geometry")
print("[torture] done")
