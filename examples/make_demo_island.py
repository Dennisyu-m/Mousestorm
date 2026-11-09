# SPDX-License-Identifier: GPL-3.0-or-later
"""Builds examples/demo_island.blend the way an LLM agent typically would:
one long bpy script, primitives + modifiers + a Principled material each.

    blender -b --factory-startup -P examples/make_demo_island.py -- out.blend
"""
import math
import random
import sys

import bmesh
import bpy
from mathutils import Vector, noise

OUT = sys.argv[sys.argv.index("--") + 1]
random.seed(11)
for ob in list(bpy.data.objects):
    bpy.data.objects.remove(ob)


def mat(name, color, rough=0.6, metal=0.0, emit=None, emit_s=0.0, glass=0.0):
    if name in bpy.data.materials:
        return bpy.data.materials[name]
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    p = next(n for n in m.node_tree.nodes if n.bl_idname == "ShaderNodeBsdfPrincipled")
    p.inputs["Base Color"].default_value = (*color, 1)
    p.inputs["Roughness"].default_value = rough
    p.inputs["Metallic"].default_value = metal
    if emit:
        p.inputs["Emission Color"].default_value = (*emit, 1)
        p.inputs["Emission Strength"].default_value = emit_s
    if glass:
        p.inputs["Transmission Weight"].default_value = glass
    return m


def noisy_mat(name, a, b, scale=6.0, rough=0.85):
    """Procedural Noise → ColorRamp, as generated scripts love to do."""
    m = mat(name, a, rough)
    t = m.node_tree
    p = next(n for n in t.nodes if n.bl_idname == "ShaderNodeBsdfPrincipled")
    tc = t.nodes.new("ShaderNodeTexCoord")
    nz = t.nodes.new("ShaderNodeTexNoise")
    nz.inputs["Scale"].default_value = scale
    nz.inputs["Detail"].default_value = 8
    ramp = t.nodes.new("ShaderNodeValToRGB")
    ramp.color_ramp.elements[0].position = 0.35
    ramp.color_ramp.elements[1].position = 0.68
    ramp.color_ramp.elements[0].color = (*a, 1)
    ramp.color_ramp.elements[1].color = (*b, 1)
    tc.location, nz.location, ramp.location = (-720, 200), (-520, 200), (-300, 200)
    t.links.new(tc.outputs["Object"], nz.inputs["Vector"])
    t.links.new(nz.outputs[0], ramp.inputs["Fac"])
    t.links.new(ramp.outputs["Color"], p.inputs["Base Color"])
    return m


def coll(name):
    c = bpy.data.collections.new(name)
    bpy.context.scene.collection.children.link(c)
    return c


def finish(ob, c, m=None, smooth=False):
    for owner in list(ob.users_collection):
        owner.objects.unlink(ob)
    c.objects.link(ob)
    if m is not None:
        ob.data.materials.append(m)
    if smooth and ob.type == "MESH":
        ob.data.polygons.foreach_set("use_smooth", [True] * len(ob.data.polygons))
        ob.data.update()
    return ob


def cube(name, c, loc, size, m, rot=(0, 0, 0), bevel=0.0):
    bpy.ops.mesh.primitive_cube_add(location=loc, rotation=rot)
    ob = bpy.context.active_object
    ob.name = name
    ob.scale = (size[0] / 2, size[1] / 2, size[2] / 2)
    if bevel:
        mod = ob.modifiers.new("Bevel", "BEVEL")
        mod.width, mod.segments = bevel, 3
    return finish(ob, c, m)


def cyl(name, c, loc, r, h, m, verts=32, rot=(0, 0, 0), r2=None):
    bpy.ops.mesh.primitive_cone_add(vertices=verts, radius1=r, radius2=r if r2 is None else r2,
                                    depth=h, location=loc, rotation=rot)
    ob = bpy.context.active_object
    ob.name = name
    return finish(ob, c, m, smooth=verts > 12)


def blob(name, c, loc, scale, m, subdiv=4, amp=0.25, freq=1.4, seed=0):
    bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=subdiv, location=(0, 0, 0))
    ob = bpy.context.active_object
    ob.name = name
    off = Vector((seed * 7.3, seed * 3.1, seed * 5.9))
    for v in ob.data.vertices:
        d = noise.fractal(v.co * freq + off, 0.9, 2.1, 5, noise_basis="PERLIN_ORIGINAL")
        v.co *= 1 + amp * d
    ob.location = loc
    ob.scale = scale
    return finish(ob, c, m, smooth=True)


# ---------------------------------------------------------------- materials
rock = noisy_mat("Rock_Procedural", (0.25, 0.23, 0.22), (0.46, 0.42, 0.37), 5.5)
sand = noisy_mat("Sand_Procedural", (0.72, 0.56, 0.34), (0.86, 0.72, 0.48), 22, 0.95)
grass = noisy_mat("Grass_Procedural", (0.20, 0.40, 0.09), (0.40, 0.58, 0.14), 9)
wood = noisy_mat("Wood_Planks", (0.28, 0.15, 0.07), (0.46, 0.27, 0.12), 18, 0.7)
bark = mat("Bark", (0.19, 0.11, 0.06), 0.9)
leafA = noisy_mat("Leaves_Summer", (0.09, 0.30, 0.07), (0.24, 0.50, 0.10), 8)
leafB = noisy_mat("Leaves_Autumn", (0.72, 0.26, 0.05), (0.91, 0.52, 0.09), 8)
white = mat("Paint_White", (0.86, 0.85, 0.80), 0.42)
red = mat("Paint_Red", (0.62, 0.06, 0.05), 0.35)
roofm = mat("Roof_Slate", (0.13, 0.17, 0.24), 0.5)
brass = mat("Brass", (0.86, 0.60, 0.25), 0.25, 1.0)
iron = mat("Iron_Dark", (0.08, 0.08, 0.09), 0.4, 1.0)
glassm = mat("Glass_Lantern", (0.9, 0.95, 1.0), 0.03, glass=1.0)
lamp = mat("Lamp_Emission", (1, 0.8, 0.4), 0.3, emit=(1.0, 0.72, 0.32), emit_s=26.0)
window = mat("Window_Glow", (1, 0.7, 0.3), 0.2, emit=(1.0, 0.62, 0.22), emit_s=5.0)
sail = mat("Sail_Cloth", (0.92, 0.88, 0.76), 0.75)
hull = mat("Hull_Teal", (0.04, 0.33, 0.36), 0.3)
water = mat("Sea_Water", (0.02, 0.17, 0.25), 0.09)

world_c = coll("Terrain")
house_c = coll("Cottage")
light_c = coll("Lighthouse")
nature_c = coll("Nature")
dock_c = coll("Dock_Boat")

# ------------------------------------------------------------------ terrain
blob("Island_Base", world_c, (0, 0, -0.62), (6.0, 5.0, 1.42), rock, 6, 0.32, 1.1, 1)
blob("Beach_Ring", world_c, (0.2, -0.1, -0.5), (6.5, 5.4, 0.72), sand, 5, 0.1, 1.8, 2)
blob("Meadow_Top", world_c, (0, 0.3, 0.05), (5.0, 3.9, 0.72), grass, 6, 0.14, 1.7, 3)
blob("Hill_Lighthouse", world_c, (-2.6, 1.3, 0.55), (2.1, 1.9, 1.0), grass, 5, 0.18, 1.8, 4)

SEA_R = 9.6
SEA_Z = -0.62
rings, segs = 96, 320
verts = [(0.0, 0.0, SEA_Z)]
for j in range(1, rings + 1):
    r = SEA_R * j / rings
    for k in range(segs):
        a = math.tau * k / segs
        verts.append((r * math.cos(a), r * math.sin(a), SEA_Z))
faces = [(0, 1 + k, 1 + (k + 1) % segs) for k in range(segs)]
for j in range(rings - 1):
    for k in range(segs):
        a0 = 1 + j * segs + k
        b0 = 1 + j * segs + (k + 1) % segs
        faces.append((a0, a0 + segs, b0 + segs, b0))
me_sea = bpy.data.meshes.new("Sea")
me_sea.from_pydata(verts, [], faces)
sea = bpy.data.objects.new("Sea_Surface", me_sea)
bpy.context.scene.collection.objects.link(sea)
for v in me_sea.vertices:
    if math.hypot(v.co.x, v.co.y) < SEA_R * 0.995:
        p = v.co * 0.8
        v.co.z += 0.085 * noise.fractal(p, 1.0, 2.0, 4)
finish(sea, world_c, water, smooth=True)
walnut = mat("Walnut_Base", (0.085, 0.045, 0.024), 0.45)
cyl("Diorama_Base", world_c, (0, 0, SEA_Z - 1.14), SEA_R, 1.92, walnut, 160)
bpy.ops.mesh.primitive_torus_add(major_radius=SEA_R, minor_radius=0.11, location=(0, 0, SEA_Z - 0.04), major_segments=200, minor_segments=14)
finish(bpy.context.active_object, world_c, brass, True).name = "Diorama_Trim"

for i in range(14):
    a = random.uniform(0, math.tau)
    rr = random.uniform(5.2, 7.2)
    s = random.uniform(0.25, 0.75)
    blob("Shore_Rock_%02d" % i, world_c, (math.cos(a) * rr, math.sin(a) * rr * 0.85, -0.45),
         (s, s * random.uniform(0.7, 1.1), s * 0.8), rock, 3, 0.4, 2.2, 10 + i)

# ----------------------------------------------------------------- lighthouse
Z0 = 1.25
TX, TY = -2.6, 1.3
cyl("Lighthouse_Foundation", light_c, (TX, TY, Z0 + 0.1), 0.95, 0.5, rock, 40, r2=0.85)
sections = 6
H = 0.62
rb, rt = 0.74, 0.43
for i in range(sections):
    r1 = rb + (rt - rb) * i / sections
    r2 = rb + (rt - rb) * (i + 1) / sections
    z = Z0 + 0.35 + H * (i + 0.5)
    cyl("Tower_Section_%d" % (i + 1), light_c, (TX, TY, z), r1, H, red if i % 2 else white, 48, r2=r2)
top = Z0 + 0.35 + H * sections
cyl("Gallery_Deck", light_c, (TX, TY, top + 0.05), 0.68, 0.1, iron, 48)
for i in range(24):
    a = math.tau * i / 24
    cyl("Railing_Post_%02d" % i, light_c, (TX + math.cos(a) * 0.62, TY + math.sin(a) * 0.62, top + 0.26), 0.012, 0.36, iron, 8)
bpy.ops.mesh.primitive_torus_add(major_radius=0.62, minor_radius=0.014, location=(TX, TY, top + 0.44), major_segments=64, minor_segments=8)
finish(bpy.context.active_object, light_c, iron).name = "Railing_Ring"
cyl("Lantern_Glass", light_c, (TX, TY, top + 0.5), 0.34, 0.74, glassm, 32)
blob("Lantern_Bulb", light_c, (TX, TY, top + 0.5), (0.16, 0.16, 0.22), lamp, 3, 0.0)
cyl("Lantern_Roof", light_c, (TX, TY, top + 1.08), 0.46, 0.44, red, 32, r2=0.03)
bpy.ops.mesh.primitive_uv_sphere_add(radius=0.07, location=(TX, TY, top + 1.33))
finish(bpy.context.active_object, light_c, brass, True).name = "Roof_Finial"
for i in range(5):
    a = 0.5 + i * 1.28
    cube("Tower_Window_%d" % i, light_c, (TX + math.cos(a) * 0.6, TY + math.sin(a) * 0.6, Z0 + 0.9 + i * 0.62), (0.03, 0.16, 0.26), window, rot=(0, 0, a))

# -------------------------------------------------------------------- cottage
HX, HY, HZ = 1.75, -0.65, 0.62
cube("Cottage_Walls", house_c, (HX, HY, HZ + 0.55), (2.2, 1.6, 1.1), white, bevel=0.02)
bm = bmesh.new()
w, d, h = 1.34, 1.02, 0.98
v = [bm.verts.new(p) for p in [(-w, -d, 0), (w, -d, 0), (w, d, 0), (-w, d, 0), (-w, 0, h), (w, 0, h)]]
for f in ([v[0], v[1], v[5], v[4]], [v[2], v[3], v[4], v[5]], [v[0], v[4], v[3]], [v[1], v[2], v[5]], [v[3], v[2], v[1], v[0]]):
    bm.faces.new(f)
me = bpy.data.meshes.new("Roof")
bm.to_mesh(me)
bm.free()
roof = bpy.data.objects.new("Cottage_Roof", me)
bpy.context.scene.collection.objects.link(roof)
roof.location = (HX, HY, HZ + 1.1)
roof.modifiers.new("Solidify", "SOLIDIFY").thickness = 0.06
roof.modifiers.new("Bevel", "BEVEL").width = 0.012
finish(roof, house_c, roofm)
cube("Chimney", house_c, (HX + 0.62, HY + 0.32, HZ + 2.0), (0.34, 0.34, 0.95), rock, bevel=0.015)
cyl("Chimney_Cap", house_c, (HX + 0.62, HY + 0.32, HZ + 2.5), 0.18, 0.1, iron, 16)
cube("Door", house_c, (HX, HY - 0.81, HZ + 0.34), (0.42, 0.04, 0.68), wood, bevel=0.012)
bpy.ops.mesh.primitive_uv_sphere_add(radius=0.028, location=(HX + 0.14, HY - 0.84, HZ + 0.34))
finish(bpy.context.active_object, house_c, brass, True).name = "Door_Knob"
for side, x in (("L", -0.72), ("R", 0.72)):
    cube("Window_%s_Glow" % side, house_c, (HX + x, HY - 0.8, HZ + 0.62), (0.4, 0.03, 0.42), window)
    cube("Window_%s_Bar_V" % side, house_c, (HX + x, HY - 0.83, HZ + 0.62), (0.03, 0.03, 0.46), wood)
    cube("Window_%s_Bar_H" % side, house_c, (HX + x, HY - 0.83, HZ + 0.62), (0.46, 0.03, 0.03), wood)
    cube("Window_%s_Box" % side, house_c, (HX + x, HY - 0.88, HZ + 0.36), (0.5, 0.14, 0.1), wood, bevel=0.01)
    for k in range(4):
        blob("Flower_%s_%d" % (side, k), house_c, (HX + x - 0.17 + k * 0.115, HY - 0.88, HZ + 0.45), (0.05, 0.05, 0.05), leafB if k % 2 else red, 2, 0.1, 3.0, k)
for i in range(8):
    cube("Path_Stone_%d" % i, house_c, (HX + 0.1 * math.sin(i), HY - 1.05 - i * 0.34, HZ - 0.04 - i * 0.055), (0.4, 0.26, 0.05), rock, rot=(0, 0, random.uniform(-.3, .3)), bevel=0.012)

# --------------------------------------------------------------------- nature
def tree(i, x, y, z, s, kind):
    cyl("Tree_%02d_Trunk" % i, nature_c, (x, y, z + 0.5 * s), 0.11 * s, 1.05 * s, bark, 12, r2=0.06 * s)
    if kind == 0:
        for k in range(3):
            cyl("Tree_%02d_Pine_%d" % (i, k), nature_c, (x, y, z + (1.0 + k * 0.42) * s), (0.62 - k * 0.15) * s, 0.8 * s, leafA, 16, r2=0.02)
    else:
        blob("Tree_%02d_Canopy" % i, nature_c, (x, y, z + 1.32 * s), (0.62 * s, 0.62 * s, 0.68 * s), leafB if i % 3 == 0 else leafA, 4, 0.3, 2.4, i)


spots = [(-0.3, 1.7, 0.72), (0.7, 1.9, 0.68), (3.3, 0.9, 0.5), (3.5, -1.4, 0.42), (-0.6, -1.6, 0.62),
         (-3.9, -0.5, 0.52), (-1.2, -0.3, 0.72), (2.1, 1.5, 0.62), (-4.4, 1.1, 0.42), (0.1, -2.6, 0.42)]
for i, (x, y, z) in enumerate(spots):
    tree(i, x, y, z, random.uniform(0.75, 1.15), i % 2)
for i in range(18):
    a = random.uniform(0, math.tau)
    rr = random.uniform(1.0, 4.0)
    blob("Bush_%02d" % i, nature_c, (math.cos(a) * rr, math.sin(a) * rr * 0.8, 0.63), (0.17, 0.17, 0.13), leafA, 2, 0.3, 3.0, 40 + i)

# ------------------------------------------------------------------ dock boat
DX, DY = 3.6, -3.0
cube("Dock_Plank", dock_c, (DX, DY, -0.22), (0.22, 1.3, 0.05), wood, bevel=0.008)
arr = dock_c.objects["Dock_Plank"].modifiers.new("Array", "ARRAY")
arr.count = 15
arr.use_relative_offset, arr.use_constant_offset = False, True
arr.constant_offset_displace = (0.24, 0, 0)
for i in range(4):
    for sgn in (-1, 1):
        cyl("Dock_Post_%d%s" % (i, "L" if sgn < 0 else "R"), dock_c, (DX + i * 1.1, DY + sgn * 0.62, -0.4), 0.06, 0.95, wood, 12)
bm = bmesh.new()
rings = []
for i in range(14):
    u = i / 13
    half = 0.32 * math.sin(math.pi * (0.05 + u * 0.92)) ** 0.6
    ring = []
    for k in range(9):
        a = -math.pi / 2 + math.pi * k / 8
        ring.append(bm.verts.new((-1.1 + 2.2 * u, half * math.sin(a), -0.28 * math.cos(a) ** 0.8 + 0.28 * (u - 0.5) ** 2)))
    rings.append(ring)
for i in range(13):
    for k in range(8):
        bm.faces.new([rings[i][k], rings[i + 1][k], rings[i + 1][k + 1], rings[i][k + 1]])
me = bpy.data.meshes.new("Hull")
bm.to_mesh(me)
bm.free()
boat = bpy.data.objects.new("Boat_Hull", me)
bpy.context.scene.collection.objects.link(boat)
boat.location = (7.2, -3.4, -0.32)
boat.rotation_euler.z = 0.4
boat.modifiers.new("Solidify", "SOLIDIFY").thickness = 0.03
boat.modifiers.new("Smooth", "SUBSURF").levels = 2
finish(boat, dock_c, hull, smooth=True)
cyl("Boat_Mast", dock_c, (7.2, -3.4, 0.75), 0.03, 2.0, wood, 12)
bm = bmesh.new()
pts = [bm.verts.new((0.05 + u, 0, 0.12 + u * 0.1 + 1.6 * (1 - u) * (0.2 + 0.8 * (1 - u)) * h * 0)) for u, h in zip([0], [0])]
bm.free()
me = bpy.data.meshes.new("Sail")
grid_v, grid_f = [], []
for j in range(13):
    for i in range(13):
        u, v = i / 12, j / 12
        grid_v.append((0.05 + 1.1 * u * (1 - v * 0.95), 0.13 * math.sin(u * math.pi) * (1 - v), 0.14 + 1.6 * v))
for j in range(12):
    for i in range(12):
        a = j * 13 + i
        grid_f.append((a, a + 1, a + 14, a + 13))
me.from_pydata(grid_v, [], grid_f)
sail_ob = bpy.data.objects.new("Boat_Sail", me)
bpy.context.scene.collection.objects.link(sail_ob)
sail_ob.location = (7.2, -3.4, -0.1)
sail_ob.rotation_euler.z = 0.4 + math.pi
finish(sail_ob, dock_c, sail, smooth=True)
for i in range(4):
    cyl("Lobster_Buoy_%d" % i, dock_c, (5.8 + i * 0.5, -1.8 - i * 0.3, -0.5), 0.09, 0.2, red if i % 2 else white, 16)

bpy.ops.wm.save_as_mainfile(filepath=OUT)
print("saved", OUT, len(bpy.data.objects), "objects")
