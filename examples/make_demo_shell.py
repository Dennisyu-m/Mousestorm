# SPDX-License-Identifier: GPL-3.0-or-later
"""Builds examples/demo_shell.blend: the *other* kind of AI output — a single
dense triangle mesh with vertex colours, like image-to-3D services export.

    blender -b --factory-startup -P examples/make_demo_shell.py -- out.blend
"""
import math
import sys

import bpy
import numpy as np

OUT = sys.argv[sys.argv.index("--") + 1]
for ob in list(bpy.data.objects):
    bpy.data.objects.remove(ob)

NU, NV = 1500, 110
turns = 5.4
u = np.linspace(0, 1, NU)
v = np.linspace(0, math.tau, NV, endpoint=False)
U, V = np.meshgrid(u, v, indexing="ij")
theta = U * turns * math.tau
grow = np.exp(theta * 0.085)
ridge = 1 + 0.045 * np.sin(theta * 8.0) ** 8 + 0.018 * np.sin(V * 11 + theta * 1.5)
r_tube = 0.42 * grow * ridge
bx = 0.92 * grow * np.cos(theta)
by = 0.92 * grow * np.sin(theta)
bz = -0.86 * (grow - 1.0) * 1.25
ring = 1 + r_tube / (0.92 * grow) * np.cos(V)
x = bx * ring
y = by * ring
z = bz + r_tube * np.sin(V) * 1.2

P = np.stack([x, y, z], 2).reshape(-1, 3)
P -= P.mean(axis=0)
P /= np.abs(P).max() / 2.6
P = P[:, [0, 2, 1]] * np.array([1, 1, -1])  # stand the shell up
P[:, 2] -= P[:, 2].min()

band = 0.5 + 0.5 * np.sin(theta * 8.0 + 1.2 * np.sin(V * 2))
fine = 0.5 + 0.5 * np.sin(V * 19 + theta * 2.3)
t = (0.55 * band + 0.45 * fine).reshape(-1)[:, None]
a, b = np.array([0.72, 0.36, 0.13]), np.array([0.97, 0.86, 0.66])
col = a + (b - a) * t ** 1.4
col *= (0.55 + 0.45 * U.reshape(-1)[:, None])

faces = []
for i in range(NU - 1):
    for j in range(NV):
        a0 = i * NV + j
        b0 = i * NV + (j + 1) % NV
        c0 = (i + 1) * NV + (j + 1) % NV
        d0 = (i + 1) * NV + j
        faces.append((a0, b0, c0))
        faces.append((a0, c0, d0))

me = bpy.data.meshes.new("geometry_0")
me.from_pydata(P.tolist(), [], faces)
me.polygons.foreach_set("use_smooth", [True] * len(me.polygons))
me.update()
attr = me.color_attributes.new("Color", "FLOAT_COLOR", "POINT")
rgba = np.ones((len(P), 4), dtype=np.float32)
rgba[:, :3] = col
attr.data.foreach_set("color", rgba.reshape(-1))
ob = bpy.data.objects.new("geometry_0", me)
bpy.context.scene.collection.objects.link(ob)

m = bpy.data.materials.new("material_0")
m.use_nodes = True
nt = m.node_tree
p = next(n for n in nt.nodes if n.bl_idname == "ShaderNodeBsdfPrincipled")
c = nt.nodes.new("ShaderNodeVertexColor")
c.layer_name = "Color"
c.location = (-300, 200)
nt.links.new(c.outputs["Color"], p.inputs["Base Color"])
p.inputs["Roughness"].default_value = 0.34
me.materials.append(m)

bpy.ops.wm.save_as_mainfile(filepath=OUT)
print("saved", OUT, len(me.polygons), "tris")
