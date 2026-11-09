# SPDX-License-Identifier: GPL-3.0-or-later
# Runs INSIDE Blender (this file uses the bpy API, hence GPL).
#
#   blender -b --factory-startup --disable-autoexec scene.blend \
#           -P extract.py -- OUT_DIR [TRI_BUDGET]
#
# Flattens the evaluated scene (modifiers, instances, curves, text ...) into
# plain numpy arrays + one JSON document, so that everything downstream can
# run outside Blender with no GPU.
import json
import os
import sys
import traceback

import bpy
import numpy as np

argv = sys.argv[sys.argv.index("--") + 1:]
OUT = argv[0]
TRI_BUDGET = int(argv[1]) if len(argv) > 1 else 2_500_000
TEX_MAX = 1024
os.makedirs(OUT, exist_ok=True)
os.makedirs(os.path.join(OUT, "tex"), exist_ok=True)

MESHLIKE = {"MESH", "CURVE", "SURFACE", "META", "FONT"}


# --------------------------------------------------------------------- utils
def jsonable(v):
    try:
        if isinstance(v, (bool, int, str)):
            return v
        if isinstance(v, float):
            return round(v, 5)
        return [round(float(x), 5) for x in v]
    except Exception:
        return None


def mat4(m):
    return np.array(m, dtype=np.float64)


# ------------------------------------------------------------ material parse
def walk_color(socket, depth=0):
    """Best-effort: approximate whatever drives a colour socket by one RGB.
    The true look comes from the Cycles renders, this only colours the fast
    preview viewport. Returns (rgb, image_node, uses_vertex_colour)."""
    if depth > 12:
        return (0.6, 0.6, 0.6), None, False
    if not socket.is_linked:
        v = socket.default_value
        try:
            return (float(v[0]), float(v[1]), float(v[2])), None, False
        except TypeError:
            return (float(v), float(v), float(v)), None, False
    from_node = socket.links[0].from_node
    idn = from_node.bl_idname
    if idn == "ShaderNodeTexImage" and from_node.image:
        return (0.7, 0.7, 0.7), from_node, False
    if idn in {"ShaderNodeVertexColor", "ShaderNodeAttribute"}:
        return (0.7, 0.7, 0.7), None, True
    if idn == "ShaderNodeRGB":
        c = from_node.outputs[0].default_value
        return (c[0], c[1], c[2]), None, False
    if idn == "ShaderNodeValToRGB":
        els = from_node.color_ramp.elements
        c = np.mean([e.color[:3] for e in els], axis=0)
        return tuple(float(x) for x in c), None, False
    if idn in {"ShaderNodeMix", "ShaderNodeMixRGB"}:
        colour_inputs = [
            s for s in from_node.inputs
            if s.type == "RGBA" and s.enabled
        ]
        results = [walk_color(s, depth + 1) for s in colour_inputs]
        if results:
            for r in results:
                if r[1] is not None or r[2]:
                    return r
            c = np.mean([r[0] for r in results], axis=0)
            return tuple(float(x) for x in c), None, False
    for s in from_node.inputs:
        if s.type == "RGBA" and s.enabled:
            return walk_color(s, depth + 1)
    return (0.55, 0.55, 0.55), None, False


def find_surface(node_tree):
    out = None
    for n in node_tree.nodes:
        if n.bl_idname == "ShaderNodeOutputMaterial" and n.is_active_output:
            out = n
            break
    if out is None:
        return None
    s = out.inputs.get("Surface")
    if s is None or not s.is_linked:
        return None
    n = s.links[0].from_node
    seen = 0
    while n.bl_idname in {"ShaderNodeMixShader", "ShaderNodeAddShader"} and seen < 8:
        seen += 1
        shader_inputs = [i for i in n.inputs if i.type == "SHADER" and i.is_linked]
        if not shader_inputs:
            return None
        chosen = shader_inputs[0]
        for i in shader_inputs:
            if i.links[0].from_node.bl_idname == "ShaderNodeBsdfPrincipled":
                chosen = i
                break
        n = chosen.links[0].from_node
    return n


saved_images = {}


def save_image(img):
    """Writes a downsized copy of the image, returns (file, mean_rgb)."""
    key = img.name
    if key in saved_images:
        return saved_images[key]
    result = (None, (0.7, 0.7, 0.7))
    try:
        w, h = img.size
        if w > 0 and h > 0:
            px = np.empty(w * h * 4, dtype=np.float32)
            img.pixels.foreach_get(px)
            px = px.reshape(h, w, 4)[::-1]
            step = max(1, max(w, h) // TEX_MAX)
            px = px[::step, ::step]
            rgb = np.clip(px[..., :3], 0, 1)
            # Byte images read back display-encoded, float images linear.
            # The preview samples everything as display-encoded.
            if img.is_float:
                rgb = np.where(rgb <= 0.0031308, rgb * 12.92, 1.055 * rgb ** (1 / 2.4) - 0.055)
            fname = "%04d.npy" % len(saved_images)
            np.save(os.path.join(OUT, "tex", fname), (rgb * 255).astype(np.uint8))
            mean = (rgb ** 2.2).mean(axis=(0, 1))
            result = (fname, tuple(float(x) for x in mean))
    except Exception:
        traceback.print_exc()
    saved_images[key] = result
    return result


def node_graph(tree, limit=36):
    nodes = list(tree.nodes)[:limit]
    index = {n: i for i, n in enumerate(nodes)}
    out_nodes, out_links = [], []
    for n in nodes:
        loc = getattr(n, "location_absolute", n.location)
        inputs = []
        for s in n.inputs:
            if not s.enabled or s.hide:
                continue
            value = None
            if hasattr(s, "default_value") and not s.is_linked:
                value = jsonable(s.default_value)
            inputs.append({"n": s.name, "t": s.type, "v": value})
        outputs = [{"n": s.name, "t": s.type} for s in n.outputs
                   if s.enabled and not s.hide]
        out_nodes.append({
            "id": n.bl_idname, "label": n.label or n.name,
            "x": float(loc[0]), "y": float(loc[1]), "w": float(n.width),
            "in": inputs[:9], "out": outputs[:5],
        })
    for l in tree.links:
        if l.from_node in index and l.to_node in index and not l.is_muted:
            out_links.append({
                "a": index[l.from_node], "as": l.from_socket.name,
                "b": index[l.to_node], "bs": l.to_socket.name})
    return {"nodes": out_nodes, "links": out_links}


def parse_material(mat):
    d = {
        "name": mat.name, "color": [0.6, 0.6, 0.6], "metal": 0.0, "rough": 0.5,
        "emit": [0.0, 0.0, 0.0], "alpha": 1.0, "tex": None, "vcol": False,
        "graph": None,
    }
    try:
        d["color"] = [float(c) for c in mat.diffuse_color[:3]]
        d["metal"] = float(mat.metallic)
        d["rough"] = float(mat.roughness)
    except Exception:
        pass
    if not (mat.use_nodes and mat.node_tree):
        return d
    try:
        d["graph"] = node_graph(mat.node_tree)
        n = find_surface(mat.node_tree)
        if n is None:
            return d
        if n.bl_idname == "ShaderNodeBsdfPrincipled":
            rgb, img, vcol = walk_color(n.inputs["Base Color"])
            d["color"], d["vcol"] = list(rgb), vcol
            if img is not None:
                d["tex"], mean = save_image(img.image)
                d["color"] = list(mean)
            for key, sock in (("metal", "Metallic"), ("rough", "Roughness")):
                s = n.inputs.get(sock)
                if s is not None:
                    d[key] = float(s.default_value) if not s.is_linked else d[key]
            a = n.inputs.get("Alpha")
            if a is not None and not a.is_linked:
                d["alpha"] = float(a.default_value)
            t = n.inputs.get("Transmission Weight") or n.inputs.get("Transmission")
            if t is not None and not t.is_linked and t.default_value > 0.05:
                d["alpha"] = min(d["alpha"], 1.0 - 0.72 * float(t.default_value))
                d["rough"] = min(d["rough"], 0.2)
            ec = n.inputs.get("Emission Color") or n.inputs.get("Emission")
            es = n.inputs.get("Emission Strength")
            if ec is not None:
                rgb2, _, _ = walk_color(ec)
                strength = float(es.default_value) if es is not None else 1.0
                d["emit"] = [float(c) * strength for c in rgb2]
        elif n.bl_idname == "ShaderNodeEmission":
            rgb, _, _ = walk_color(n.inputs["Color"])
            strength = float(n.inputs["Strength"].default_value)
            d["color"] = list(rgb)
            d["emit"] = [c * strength for c in rgb]
        elif n.bl_idname in {"ShaderNodeBsdfDiffuse", "ShaderNodeBsdfGlossy",
                             "ShaderNodeBsdfAnisotropic", "ShaderNodeBsdfGlass",
                             "ShaderNodeBsdfTranslucent"}:
            s = n.inputs.get("Color")
            if s is not None:
                rgb, img, vcol = walk_color(s)
                d["color"], d["vcol"] = list(rgb), vcol
                if img is not None:
                    d["tex"], mean = save_image(img.image)
                    d["color"] = list(mean)
            if "Glossy" in n.bl_idname or "Anisotropic" in n.bl_idname:
                d["metal"] = 1.0
            if "Glass" in n.bl_idname:
                d["alpha"] = 0.35
                d["rough"] = 0.1
    except Exception:
        traceback.print_exc()
    return d


# ------------------------------------------------------------ material table
materials, mat_index = [], {}


def material_id(mat):
    if mat is None:
        return -1
    key = mat.name
    if key not in mat_index:
        mat_index[key] = len(materials)
        materials.append(parse_material(mat))
    return mat_index[key]


# ------------------------------------------------------------ mesh → arrays
def corner_normals(me):
    n = len(me.loops)
    arr = np.empty(n * 3, dtype=np.float32)
    if hasattr(me, "corner_normals") and len(me.corner_normals) == n:
        me.corner_normals.foreach_get("vector", arr)
    else:
        me.calc_normals_split()
        me.loops.foreach_get("normal", arr)
    return arr.reshape(-1, 3)


def corner_uvs(me):
    n = len(me.loops)
    if not me.uv_layers:
        return None
    layer = me.uv_layers.active or me.uv_layers[0]
    arr = np.empty(n * 2, dtype=np.float32)
    try:
        layer.uv.foreach_get("vector", arr)
    except Exception:
        layer.data.foreach_get("uv", arr)
    return arr.reshape(-1, 2)


def corner_colors(me):
    attr = None
    try:
        attr = me.color_attributes.active_color
    except Exception:
        pass
    if attr is None:
        return None
    n = len(attr.data)
    arr = np.empty(n * 4, dtype=np.float32)
    try:
        attr.data.foreach_get("color", arr)
    except Exception:
        return None
    arr = arr.reshape(-1, 4)[:, :3]
    if attr.domain == "POINT":
        vi = np.empty(len(me.loops), dtype=np.int32)
        me.loops.foreach_get("vertex_index", vi)
        arr = arr[vi]
    elif attr.domain != "CORNER":
        return None
    return arr


def read_mesh(me):
    me.calc_loop_triangles()
    nv, nt, np_ = len(me.vertices), len(me.loop_triangles), len(me.polygons)
    if nv == 0 or nt == 0:
        return None
    co = np.empty(nv * 3, dtype=np.float64)
    me.vertices.foreach_get("co", co)
    co = co.reshape(-1, 3)

    lt = me.loop_triangles
    tv = np.empty(nt * 3, dtype=np.int32)
    lt.foreach_get("vertices", tv)
    tl = np.empty(nt * 3, dtype=np.int32)
    lt.foreach_get("loops", tl)
    tm = np.empty(nt, dtype=np.int32)
    lt.foreach_get("material_index", tm)
    tp = np.empty(nt, dtype=np.int32)
    lt.foreach_get("polygon_index", tp)
    tv, tl = tv.reshape(-1, 3), tl.reshape(-1, 3)

    pstart = np.empty(np_, dtype=np.int32)
    me.polygons.foreach_get("loop_start", pstart)
    ptotal = np.empty(np_, dtype=np.int32)
    me.polygons.foreach_get("loop_total", ptotal)
    smooth = np.empty(np_, dtype=bool)
    try:
        me.polygons.foreach_get("use_smooth", smooth)
    except Exception:
        smooth[:] = True

    # Which edges of each triangle are real polygon edges (bit i = edge i→i+1)
    tot = ptotal[tp][:, None]
    d = (tl[:, [1, 2, 0]] - tl[:, [0, 1, 2]]) % tot
    real = (d == 1) | (d == tot - 1)
    mask = (real[:, 0] * 1 + real[:, 1] * 2 + real[:, 2] * 4).astype(np.uint8)

    cn = corner_normals(me)[tl]              # (nt, 3, 3)
    uv = corner_uvs(me)
    uv = uv[tl] if uv is not None else None
    col = corner_colors(me)
    col = col[tl] if col is not None else None
    return {
        "co": co, "tv": tv, "tm": tm, "mask": mask, "cn": cn, "uv": uv,
        "col": col, "smooth": float(smooth.mean()), "npoly": np_,
    }


# --------------------------------------------------------------- walk scene
scene = bpy.context.scene
depsgraph = bpy.context.evaluated_depsgraph_get()

# 1. Is the scene too heavy for the preview rasterizer? Decimate only then.
rough_total = 0
for ob in scene.objects:
    if ob.type == "MESH" and ob.visible_get():
        rough_total += sum(max(0, len(p.vertices) - 2) for p in ob.data.polygons)
if rough_total > TRI_BUDGET:
    ratio = max(0.02, TRI_BUDGET / rough_total)
    print("[mousestorm] heavy scene (%d tris), preview decimate x%.3f" % (rough_total, ratio))
    for ob in list(scene.objects):
        if ob.type == "MESH" and len(ob.data.polygons) > 20000:
            m = ob.modifiers.new("MousestormPreview", "DECIMATE")
            m.ratio = ratio
    depsgraph = bpy.context.evaluated_depsgraph_get()
    depsgraph.update()

# 2. Collections for the outliner
coll_of = {}


def visit_coll(coll, path):
    for ob in coll.objects:
        coll_of.setdefault(ob.name, path)
    for child in coll.children:
        visit_coll(child, path + [child.name])


visit_coll(scene.collection, [])

# 3. Flatten
parts = []
all_co, all_box, all_tv, all_tn, all_uv, all_col, all_mask, all_tm = [], [], [], [], [], [], [], []
vert_ofs = tri_ofs = 0
mesh_cache = {}
any_uv = any_col = False

for inst in depsgraph.object_instances:
    ev = inst.object
    if ev.type not in MESHLIKE:
        continue
    orig = ev.original
    if not inst.is_instance and (orig.hide_render or not orig.visible_get()):
        continue
    key = ("inst", ev.data.as_pointer()) if inst.is_instance else ("ob", orig.name)
    if key not in mesh_cache:
        try:
            me = ev.to_mesh()
            data = read_mesh(me) if me is not None else None
        except Exception:
            traceback.print_exc()
            data = None
        finally:
            try:
                ev.to_mesh_clear()
            except Exception:
                pass
        if data is not None:
            slots = [material_id(s.material) for s in ev.material_slots]
            data["slots"] = slots
        mesh_cache[key] = data
    data = mesh_cache[key]
    if data is None:
        continue

    M = mat4(inst.matrix_world)
    R, t = M[:3, :3], M[:3, 3]
    co = data["co"]
    world = co @ R.T + t
    # One NaN from a generator would otherwise poison every bounding box and
    # camera fit in the film. Bad vertices collapse onto the object's centre.
    finite = np.isfinite(world).all(axis=1)
    if not finite.all():
        if not finite.any():
            print("[mousestorm] skipped %s: no finite vertices" % orig.name)
            continue
        print("[mousestorm] %s: repaired %d non-finite vertices" % (orig.name, int((~finite).sum())))
        centre = world[finite].mean(axis=0)
        world = world.copy()
        world[~finite] = centre
        co = co.copy()
        co[~finite] = co[np.isfinite(co).all(axis=1)].mean(axis=0) if np.isfinite(co).any() else 0.0

    # Primitive-looking starting shape: project onto the object's own bbox.
    lo, hi = co.min(axis=0), co.max(axis=0)
    c, h = (lo + hi) * 0.5, np.maximum((hi - lo) * 0.5, 1e-6)
    q = (co - c) / h
    m = np.maximum(np.abs(q).max(axis=1, keepdims=True), 1e-6)
    box_local = c + (q / m) * h
    box_world = box_local @ R.T + t

    try:
        nmat = np.linalg.inv(R).T
    except np.linalg.LinAlgError:
        nmat = R
    tn = data["cn"].astype(np.float64) @ nmat.T
    tn = np.nan_to_num(tn, nan=0.0, posinf=0.0, neginf=0.0)
    tn /= np.maximum(np.linalg.norm(tn, axis=2, keepdims=True), 1e-12)

    slots = data["slots"]
    if slots:
        lut = np.array(slots, dtype=np.int32)
        tm = lut[np.clip(data["tm"], 0, len(lut) - 1)]
    else:
        tm = np.full_like(data["tm"], -1)

    nv, nt = len(co), len(data["tv"])
    all_co.append(world.astype(np.float32))
    all_box.append(box_world.astype(np.float32))
    all_tv.append(data["tv"] + vert_ofs)
    all_tn.append(tn.astype(np.float32))
    all_mask.append(data["mask"])
    all_tm.append(tm)
    if data["uv"] is not None:
        any_uv = True
    if data["col"] is not None:
        any_col = True
    all_uv.append(data["uv"] if data["uv"] is not None else np.zeros((nt, 3, 2), np.float32))
    all_col.append(data["col"] if data["col"] is not None else np.ones((nt, 3, 3), np.float32) * -1)

    mods = []
    if not inst.is_instance:
        for mod in orig.modifiers:
            if mod.name == "MousestormPreview":
                continue
            mods.append({"name": mod.name, "type": mod.type})

    wmin, wmax = world.min(axis=0), world.max(axis=0)
    parts.append({
        "name": orig.name if not inst.is_instance else "%s[%d]" % (orig.name, len(parts)),
        "type": orig.type,
        "data_name": getattr(ev.data if inst.is_instance else orig.data, "name", ""),
        "instance": bool(inst.is_instance),
        "collection": coll_of.get(orig.name, []),
        "parent": orig.parent.name if orig.parent and not inst.is_instance else None,
        "v0": vert_ofs, "nv": nv, "t0": tri_ofs, "nt": nt,
        "npoly": data["npoly"], "smooth": data["smooth"],
        "bbox": [[float(x) for x in wmin], [float(x) for x in wmax]],
        "loc": [float(x) for x in t],
        "matrix": M.tolist(),
        "mods": mods,
        "mats": sorted(set(int(x) for x in np.unique(tm))),
    })
    vert_ofs += nv
    tri_ofs += nt

if not parts:
    print("[mousestorm] ERROR: no renderable geometry in this file")
    sys.exit(3)

np.save(os.path.join(OUT, "P.npy"), np.concatenate(all_co))
np.save(os.path.join(OUT, "BOX.npy"), np.concatenate(all_box))
np.save(os.path.join(OUT, "T.npy"), np.concatenate(all_tv).astype(np.int32))
np.save(os.path.join(OUT, "TN.npy"), np.concatenate(all_tn))
np.save(os.path.join(OUT, "TMASK.npy"), np.concatenate(all_mask))
np.save(os.path.join(OUT, "TMAT.npy"), np.concatenate(all_tm).astype(np.int32))
if any_uv:
    np.save(os.path.join(OUT, "TUV.npy"), np.concatenate(all_uv))
if any_col:
    np.save(os.path.join(OUT, "TCOL.npy"), np.concatenate(all_col))

# --------------------------------------------------------------------- rest
lights = []
for ob in scene.objects:
    if ob.type == "LIGHT" and ob.visible_get() and not ob.hide_render:
        M = mat4(ob.matrix_world)
        lights.append({
            "name": ob.name, "kind": ob.data.type, "loc": M[:3, 3].tolist(),
            "energy": float(ob.data.energy), "color": list(ob.data.color),
        })

camera = None
if scene.camera is not None and scene.camera.type == "CAMERA":
    cam = scene.camera
    camera = {
        "name": cam.name, "matrix": mat4(cam.matrix_world).tolist(),
        "lens": float(cam.data.lens), "sensor": float(cam.data.sensor_width),
        "ortho": cam.data.type == "ORTHO",
    }

world_strength = 0.0
world_textured = False
if scene.world is not None:
    w = scene.world
    if w.use_nodes and w.node_tree:
        for n in w.node_tree.nodes:
            if n.bl_idname == "ShaderNodeBackground":
                s = n.inputs["Strength"]
                c = n.inputs["Color"]
                strength = float(s.default_value)
                if c.is_linked:
                    world_textured = True
                    world_strength = max(world_strength, strength)
                else:
                    world_strength = max(
                        world_strength, strength * float(np.mean(c.default_value[:3])))
    else:
        world_strength = float(np.mean(w.color[:3]))

doc = {
    "source": os.path.basename(bpy.data.filepath),
    "blender": bpy.app.version_string,
    "fps": scene.render.fps,
    "parts": parts, "materials": materials, "lights": lights, "camera": camera,
    "world": {"strength": world_strength, "textured": world_textured},
    "empties": [o.name for o in scene.objects if o.type == "EMPTY"][:200],
    "collections": [c.name for c in bpy.data.collections][:200],
    "totals": {"verts": vert_ofs, "tris": tri_ofs,
               "polys": int(sum(p["npoly"] for p in parts))},
    "has_uv": any_uv, "has_col": any_col,
}
with open(os.path.join(OUT, "scene.json"), "w", encoding="utf-8") as f:
    json.dump(doc, f, ensure_ascii=False)
print("[mousestorm] extracted %d parts, %d verts, %d tris, %d materials"
      % (len(parts), vert_ofs, tri_ofs, len(materials)))
