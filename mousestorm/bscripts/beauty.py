# SPDX-License-Identifier: GPL-3.0-or-later
# Runs INSIDE Blender. Renders the photo-real hero stills and the turntable
# with Cycles, using the shot list the planner wrote to shots.json.
#
#   blender -b --factory-startup --disable-autoexec scene.blend \
#           -P beauty.py -- WORK_DIR
import json
import os
import sys
import time

import bpy
from mathutils import Matrix, Vector

argv = sys.argv[sys.argv.index("--") + 1:]
WORK = argv[0]
with open(os.path.join(WORK, "shots.json"), encoding="utf-8") as f:
    S = json.load(f)

scene = bpy.context.scene
render = scene.render
render.engine = "CYCLES"
render.resolution_x, render.resolution_y = S["width"], S["height"]
render.resolution_percentage = 100
render.threads_mode = "FIXED"
render.threads = max(1, int(S["threads"]))
render.use_persistent_data = True
render.film_transparent = False
render.use_compositing = False
render.use_sequencer = False
render.use_stamp = False

cy = scene.cycles
if S.get("device", "cpu") == "gpu":
    prefs = bpy.context.preferences.addons["cycles"].preferences
    for backend in ("OPTIX", "CUDA", "HRT", "ONEAPI", "METAL"):
        try:
            prefs.compute_device_type = backend
            prefs.get_devices()
            if any(d.type == backend for d in prefs.devices):
                wanted = S.get("gpu") or ""
                for d in prefs.devices:
                    d.use = d.type == backend and (not wanted or d.id == wanted)
                cy.device = "GPU"
                break
        except Exception:
            pass
    else:
        print("[mousestorm] no usable GPU found, rendering on the CPU")
else:
    cy.device = "CPU"
cy.use_adaptive_sampling = True
cy.adaptive_threshold = 0.02
cy.use_denoising = bool(S.get("denoise", True))
cy.denoiser = "OPENIMAGEDENOISE"
try:
    cy.denoising_use_gpu = S.get("device", "cpu") == "gpu"
except Exception:
    pass
cy.max_bounces = int(S.get("bounces", 8))
cy.diffuse_bounces = 3
cy.glossy_bounces = 4
cy.transmission_bounces = 8
cy.caustics_reflective = False
cy.caustics_refractive = False
cy.blur_glossy = 1.0
cy.tile_size = 2048

transform = S.get("view_transform", "agx")
try:
    if transform == "standard":
        scene.view_settings.view_transform = "Standard"
        scene.view_settings.look = "None"
    elif transform == "filmic":
        scene.view_settings.view_transform = "Filmic"
        scene.view_settings.look = "Medium High Contrast"
    else:
        scene.view_settings.view_transform = "AgX"
        scene.view_settings.look = "AgX - Medium High Contrast"
except TypeError:
    scene.view_settings.view_transform = "Filmic"
    scene.view_settings.look = "Medium High Contrast"
scene.view_settings.exposure = float(S.get("exposure", 0.0))
scene.view_settings.gamma = 1.0


# ----------------------------------------------------------- studio lighting
def add_area(name, loc, target, power, size, color):
    light = bpy.data.lights.new(name, "AREA")
    light.shape = "RECTANGLE"
    light.size, light.size_y = size, size * 0.72
    light.energy, light.color = power, color
    ob = bpy.data.objects.new(name, light)
    scene.collection.objects.link(ob)
    ob.location = loc
    direction = Vector(target) - Vector(loc)
    ob.rotation_mode = "QUATERNION"
    ob.rotation_quaternion = direction.to_track_quat("-Z", "Y")
    return ob


rig = S["rig"]
if rig["enabled"]:
    # the studio replaces whatever incidental lights the file came with
    for ob in scene.objects:
        if ob.type == "LIGHT":
            ob.hide_render = True
    world = bpy.data.worlds.new("MousestormStudio")
    world.use_nodes = True
    bg = next(n for n in world.node_tree.nodes if n.bl_idname == "ShaderNodeBackground")
    bg.inputs["Color"].default_value = (*rig["world_color"], 1.0)
    bg.inputs["Strength"].default_value = rig["world_strength"]
    scene.world = world
    # Rays that reach the camera see a dark studio backdrop; everything else
    # still gets the (brighter) ambient that lights the subject.
    nt = world.node_tree
    out = next(n for n in nt.nodes if n.bl_idname == "ShaderNodeOutputWorld")
    lp = nt.nodes.new("ShaderNodeLightPath")
    bg2 = nt.nodes.new("ShaderNodeBackground")
    mix = nt.nodes.new("ShaderNodeMixShader")
    tc = nt.nodes.new("ShaderNodeTexCoord")
    sep = nt.nodes.new("ShaderNodeSeparateXYZ")
    ramp = nt.nodes.new("ShaderNodeValToRGB")
    ramp.color_ramp.elements[0].position = 0.0
    ramp.color_ramp.elements[1].position = 1.0
    ramp.color_ramp.elements[0].color = (*rig["backdrop_bottom"], 1.0)
    ramp.color_ramp.elements[1].color = (*rig["backdrop_top"], 1.0)
    nt.links.new(tc.outputs["Window"], sep.inputs[0])
    nt.links.new(sep.outputs[1], ramp.inputs["Fac"])
    nt.links.new(ramp.outputs["Color"], bg2.inputs["Color"])
    bg2.inputs["Strength"].default_value = 1.0
    nt.links.new(lp.outputs["Is Camera Ray"], mix.inputs[0])
    nt.links.new(bg.outputs[0], mix.inputs[1])
    nt.links.new(bg2.outputs[0], mix.inputs[2])
    nt.links.new(mix.outputs[0], out.inputs["Surface"])
    for L in rig["lights"]:
        ob = add_area(L["name"], L["loc"], L["target"], L["power"], L["size"], L["color"])
        ob.visible_camera = False   # the softboxes light the set; they must not appear in it
        if L["name"] in ("Fill Light", "Top Softbox"):
            ob.visible_glossy = False

if rig["floor"]:
    z = rig["floor_z"]
    r = rig["floor_size"]
    me = bpy.data.meshes.new("MousestormFloor")
    me.from_pydata([(-r, -r, z), (r, -r, z), (r, r, z), (-r, r, z)], [], [(0, 1, 2, 3)])
    me.update()
    floor = bpy.data.objects.new("MousestormFloor", me)
    scene.collection.objects.link(floor)
    mat = bpy.data.materials.new("MousestormFloor")
    mat.use_nodes = True
    p = mat.node_tree.nodes.get("Principled BSDF")
    if p is None:
        p = next(n for n in mat.node_tree.nodes if n.bl_idname == "ShaderNodeBsdfPrincipled")
    p.inputs["Base Color"].default_value = (*rig["floor_color"], 1.0)
    p.inputs["Roughness"].default_value = rig["floor_rough"]
    spec = p.inputs.get("Specular IOR Level")
    if spec is not None:
        spec.default_value = 0.18
    # Fade the floor out into the backdrop so no horizon line ever shows.
    nt = mat.node_tree
    output = next(n for n in nt.nodes if n.bl_idname == "ShaderNodeOutputMaterial")
    geo = nt.nodes.new("ShaderNodeNewGeometry")
    dist = nt.nodes.new("ShaderNodeVectorMath")
    dist.operation = "DISTANCE"
    dist.inputs[1].default_value = (rig["floor_center"][0], rig["floor_center"][1], z)
    remap = nt.nodes.new("ShaderNodeMapRange")
    remap.interpolation_type = "SMOOTHSTEP"
    remap.inputs["From Min"].default_value = rig["floor_r0"]
    remap.inputs["From Max"].default_value = rig["floor_r1"]
    remap.inputs["To Min"].default_value = 0.0
    remap.inputs["To Max"].default_value = 1.0
    transparent = nt.nodes.new("ShaderNodeBsdfTransparent")
    mix = nt.nodes.new("ShaderNodeMixShader")
    nt.links.new(geo.outputs["Position"], dist.inputs[0])
    nt.links.new(dist.outputs["Value"], remap.inputs["Value"])
    nt.links.new(remap.outputs["Result"], mix.inputs[0])
    nt.links.new(p.outputs[0], mix.inputs[1])
    nt.links.new(transparent.outputs[0], mix.inputs[2])
    nt.links.new(mix.outputs[0], output.inputs["Surface"])
    me.materials.append(mat)


# -------------------------------------------------------------------- camera
cam_data = bpy.data.cameras.new("MousestormCam")
# AUTO: the 36 mm sensor spans the long side, in landscape and in portrait alike
cam_data.sensor_fit = "AUTO"
cam_data.sensor_width = 36.0
cam_ob = bpy.data.objects.new("MousestormCam", cam_data)
scene.collection.objects.link(cam_ob)
scene.camera = cam_ob


def place(shot):
    eye, tgt = Vector(shot["eye"]), Vector(shot["target"])
    cam_ob.rotation_mode = "QUATERNION"
    d = tgt - eye
    cam_ob.rotation_quaternion = d.to_track_quat("-Z", "Y")
    cam_ob.location = eye
    if shot.get("matrix"):
        cam_ob.matrix_world = Matrix(shot["matrix"])
    cam_data.lens = shot["lens"]
    cam_data.clip_start = max(1e-3, shot.get("near", 0.05))
    cam_data.clip_end = shot.get("far", 10000.0)
    if shot.get("dof"):
        cam_data.dof.use_dof = True
        cam_data.dof.focus_distance = (tgt - eye).length
        cam_data.dof.aperture_fstop = shot["dof"]
    else:
        cam_data.dof.use_dof = False


def progress(msg):
    print("[mousestorm] " + msg, flush=True)


def shoot(shot, path, samples, fmt):
    place(shot)
    cy.samples = samples
    render.image_settings.file_format = fmt
    if fmt == "JPEG":
        render.image_settings.quality = 94
    else:
        render.image_settings.compression = 40
    # written beside the target and renamed, so a killed render never leaves a
    # half-written file that a resumed run would take for a finished one
    base, ext = os.path.splitext(path)
    temp = base + ".part" + ext
    render.filepath = temp
    bpy.ops.render.render(write_still=True)
    os.replace(temp, path)


OUT = S.get("out") or WORK
stills_dir = os.path.join(OUT, "stills")
turn_dir = os.path.join(OUT, "turntable")
os.makedirs(stills_dir, exist_ok=True)
os.makedirs(turn_dir, exist_ok=True)

n_total = len(S["stills"]) + len(S["turntable"])
done = 0
t_start = time.time()
for i, shot in enumerate(S["stills"]):
    path = os.path.join(stills_dir, "%02d.png" % i)
    if not os.path.exists(path):
        shoot(shot, path, S["samples"], "PNG")
    done += 1
    progress("beauty %d/%d still %s (%.0fs)" % (done, n_total, shot["name"], time.time() - t_start))

for i, shot in enumerate(S["turntable"]):
    path = os.path.join(turn_dir, "%04d.jpg" % i)
    if not os.path.exists(path):
        shoot(shot, path, S["turn_samples"], "JPEG")
    done += 1
    progress("beauty %d/%d turntable (%.0fs)" % (done, n_total, time.time() - t_start))

progress("beauty done")
