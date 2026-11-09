# SPDX-License-Identifier: GPL-3.0-or-later
"""Second half of the choreography: turns scheduled operations into per-part
keyframes, camera paths, shot lists and a baked trajectory for every mouse."""
import math
import os
import pickle

import numpy as np

from . import raster as rs
from . import themes
from .options import items_of
from . import steps as step_log
from .options import OPTS
from .plan import CubicPath, Plan, QUALITY, ease_io, ease_out_back, fit_camera, show_schedule
from .scene import Camera
from .ui_layout import Fx, anchor_xy, canvas, is_wide, layout_at

BAKE_HZ = 120.0


def lens_tans(lens, aspect):
    """Half-field tangents of a lens on a 36 mm sensor fitted to the frame's
    long side (the same rule as Cycles' AUTO sensor fit)."""
    long_side = 18.0 / lens
    return (long_side, long_side / aspect) if aspect >= 1.0 else (long_side * aspect, long_side)

ICONS = ["arrow", "pointer", "grab", "move", "rotate", "scale", "cross", "knife",
         "brush", "eyedrop", "ibeam", "hresize", "paint"]
ICON_ID = {n: i for i, n in enumerate(ICONS)}

# Pointer styles, weighted. All are original shapes drawn in ui.py.
SKIN_NAMES = ("classic", "dart", "pixel", "hollow", "bubble", "critter")
SKIN_WEIGHTS = (0.32, 0.18, 0.14, 0.14, 0.15, 0.07)
SKIN_CRITTER = SKIN_NAMES.index("critter")
CREW = ("Vertex Vic", "Topo Tina", "Bevel Bob", "Nurbs Nora", "Quad Quinn", "UV Uma", "N-gon Nick",
        "Shader Shay", "Subdiv Sid", "Normal Norm", "Edge Loop Ed", "Retopo Rey")
KIND_SOUND = {"ui": 0, "add": 1, "pick": 0, "tweak": 0, "mod": 0, "node": 0, "assign": 1,
              "grab": 2, "rot": 2, "scale": 2, "edit": 3, "paint": 4, "tile": 5}


# ------------------------------------------------------------------ shot list
def spherical(az, el):
    az, el = math.radians(az), math.radians(el)
    return math.cos(az) * math.cos(el), math.sin(az) * math.cos(el), math.sin(el)


def part_cover(vp, cam, data, target, res=(160, 90)):
    from .raster import rasterize, to_view
    W, H = res
    Z = np.empty((H, W), np.float32)
    ID = np.empty((H, W), np.int32)
    Z.fill(0.0)
    ID.fill(-1)
    r, u, f = cam.basis()
    R = np.stack([r, u, f]).astype(np.float64)
    parts = data.parts
    tr = np.array([[p["t0"], p["t0"] + p["nt"]] for p in parts], np.int64)
    vr = np.array([[p["v0"], p["v0"] + p["nv"]] for p in parts], np.int64)
    to_view(vp, vr, vr, np.asarray(cam.eye, np.float64), R, vp_view(vp))
    tanx = cam.tany * getattr(cam, "aspect", W / H)
    rasterize(vp_view(vp), data.T, tr, W, H, tanx, cam.tany, data.near, False, Z, ID,
              data.talpha, rs.BAYER)
    t0, t1 = parts[target]["t0"], parts[target]["t0"] + parts[target]["nt"]
    cy0, cy1, cx0, cx1 = H // 4, H * 3 // 4, W // 4, W * 3 // 4
    centre = ID[cy0:cy1, cx0:cx1]
    return float(np.mean((centre >= t0) & (centre < t1)))


_vcache = {}


def vp_view(p):
    k = id(p)
    if k not in _vcache:
        _vcache[k] = np.empty_like(p)
    return _vcache[k]


def fit_tans(lens, aspect, letterbox):
    """Tangents to frame the subject with: inside the letterbox when there is one."""
    tx, ty = lens_tans(lens, aspect)
    if letterbox and aspect < letterbox:
        ty = min(ty, tx / letterbox)
    return tx, ty


def make_shots(d, keep, lo, hi, sample, hero_az, hero_el, aspect, opt, letterbox=0.0):
    P_live = np.array(d.P, np.float32)
    shots = []
    lens_k = float(opt["camera.lens"]) / 50.0
    margin_k = float(opt["camera.margin"]) - 0.09

    def add(name, label, az, el, lens=50.0, margin=0.1, dof=None):
        lens *= lens_k
        margin = max(0.0, margin + margin_k)
        ta, ty = fit_tans(lens, aspect, letterbox)
        eye, target, dist = fit_camera(sample, math.radians(az), math.radians(el), ta, ty, margin)
        shots.append(dict(name=name, label=label, eye=eye.tolist(), target=target.tolist(),
                          lens=lens, near=max(dist * 0.002, 1e-3), far=dist * 40.0, dof=dof))

    add("hero", "HERO", hero_az, hero_el, 50.0, 0.08)
    add("front", "FRONT", hero_az - 52, 6, 70.0)
    add("right", "SIDE", hero_az + 38, 7, 70.0)
    add("back", "REAR", hero_az + 145, 12, 50.0)
    add("high", "HIGH ANGLE", hero_az + 18, 46, 35.0)
    add("top", "TOP DOWN", hero_az + 70, 78, 50.0)
    add("low", "LOW ANGLE", hero_az - 18, 2, 28.0, 0.06)

    # close-ups: pick detailed parts, and pick the angle that actually sees them
    idx = [i for i in np.where(keep)[0]]
    idx.sort(key=lambda i: -d.parts[i]["nt"] / (np.prod(
        np.maximum(np.array(d.parts[i]["bbox"][1]) - np.array(d.parts[i]["bbox"][0]), 1e-3)) ** (1 / 3) + 1e-3) ** 0.2)
    scene_diag = float(np.linalg.norm(hi - lo))
    picks, centres = [], []
    for spacing in (0.22, 0.12, 0.05):
        for i in idx:
            b = np.array(d.parts[i]["bbox"], np.float64)
            c = (b[0] + b[1]) * 0.5
            part_diag = np.linalg.norm(b[1] - b[0])
            if part_diag < scene_diag * 0.03 or part_diag > scene_diag * 0.4 or len(picks) >= 2:
                continue
            if all(np.linalg.norm(c - c2) > scene_diag * spacing for c2 in centres):
                picks.append(i)
                centres.append(c)
    if len(picks) < 2 and len(d.parts) == 1:
        picks, centres = [], []
        for c in (lo + (hi - lo) * np.array([0.5, 0.5, 0.72]), lo + (hi - lo) * np.array([0.5, 0.4, 0.3])):
            picks.append(0)
            centres.append(c)
    for n, (i, c) in enumerate(zip(picks, centres)):
        b = np.array(d.parts[i]["bbox"], np.float64)
        rad = max(float(np.linalg.norm(b[1] - b[0])) * 0.5, scene_diag * 0.075)
        if len(d.parts) == 1:
            rad = scene_diag * 0.24
        lens = 85.0
        tx, ty = lens_tans(lens, aspect)
        dist = rad / min(tx, ty) * 0.9
        best, best_score = None, -1.0
        for az in np.linspace(0, 360, 16, endpoint=False):
            for el in (14, 30):
                off = np.array(spherical(az, el))
                cam = Camera(c + off * dist, c, tany=ty)
                cam.aspect = aspect
                score = part_cover(P_live, cam, d, i)
                if score > best_score:
                    best_score, best = score, off
        off = best if best is not None else np.array(spherical(hero_az, 20))
        eye = c + off * dist
        shots.append(dict(name="detail%d" % (n + 1), label="DETAIL %02d" % (n + 1), eye=eye.tolist(),
                          target=c.tolist(), lens=lens, near=dist * 0.05, far=dist * 40, dof=2.8))
    if d.doc["camera"] is not None:
        c = d.doc["camera"]
        M = np.array(c["matrix"], np.float64)
        eye = M[:3, 3]
        forward = -M[:3, 2]
        shots.append(dict(name="scene", label="SCENE CAMERA", eye=eye.tolist(),
                          target=(eye + forward).tolist(), lens=float(c["lens"]) * 36.0 / float(c["sensor"]),
                          near=0.05, far=10000.0, dof=None, matrix=c["matrix"]))
    allowed = set(items_of(opt, "show.cameras"))
    return [s for s in shots if s["name"] == "hero" or s["name"] in allowed]


def turntable_shots(sample, hero_az, hero_el, n, aspect, lens=50.0, direction=1.0, margin=0.12, letterbox=0.0):
    shots = []
    tanx, tany = fit_tans(lens, aspect, letterbox)
    for i in range(n):
        s = i / n
        el = hero_el + 7.0 * math.sin(s * math.tau)
        eye, target, dist = fit_camera(sample, math.radians(hero_az + direction * 360.0 * s), math.radians(el), tanx, tany, margin)
        shots.append(dict(name="turn", label="TURN", eye=eye.tolist(), target=target.tolist(), lens=lens,
                          near=max(dist * 0.002, 1e-3), far=dist * 40.0, dof=None))
    return shots


def scene_view(d, center, opt):
    """The main viewing angle. Whoever made the file aimed its camera at what matters, so
    that direction is used if the camera is really pointing at the subject."""
    az, el = float(opt["camera.azimuth"]), float(opt["camera.elevation"])
    cam = d.doc["camera"]
    untouched = az == OPTS["camera.azimuth"].default and el == OPTS["camera.elevation"].default
    if not (cam and opt["camera.use_scene"] and untouched) or cam["ortho"]:
        return az, el
    M = np.array(cam["matrix"], np.float64)
    forward = -M[:3, 2] / max(float(np.linalg.norm(M[:3, 2])), 1e-12)
    to_subject = center - M[:3, 3]
    if float(forward @ to_subject) < math.cos(math.radians(40.0)) * float(np.linalg.norm(to_subject)):
        return az, el
    return math.degrees(math.atan2(-forward[1], -forward[0])), float(np.clip(math.degrees(math.asin(np.clip(-forward[2], -1.0, 1.0))), 4.0, 45.0))


def studio_rig(d, lo, hi, keep, settings):
    c = (lo + hi) * 0.5
    r = max(float(np.linalg.norm(hi - lo)) * 0.5, 1e-3)
    floor_z = float(min(p["bbox"][0][2] for p in d.parts))
    has_plate = not bool(keep.all())
    world = d.doc["world"]
    # A lone default sun is what generated scripts leave behind; it is not a
    # lighting design. Only an HDRI or a real multi-light setup is respected.
    has_own = world["textured"] or len(d.doc["lights"]) >= 3
    want = {"studio": True, "file": False}.get(settings.lighting, not has_own)
    opt = settings.opt
    style = themes.RIGS[opt["look.lights"]]
    t = themes.theme(opt["look.theme"])
    p = float(r * r) * float(opt["look.light_power"])

    def L(name, loc, power, size, part):
        multiplier, color = style[part]
        return dict(name=name, loc=(c + np.array(loc) * r).tolist(), target=(c + np.array([0, 0, -0.1]) * r).tolist(),
                    power=power * multiplier * p, size=size * r, color=color)

    def tone(key, fallback):
        return list(themes.srgb_to_linear(opt[key])) if opt[key] else list(fallback)

    lights = [
        L("Key Light", (-1.5, -1.9, 2.3), 34.0, 1.9, "key"),
        L("Fill Light", (2.3, -1.4, 1.0), 11.0, 2.8, "fill"),
        L("Rim Light", (0.6, 2.6, 1.7), 26.0, 0.8, "rim"),
        L("Top Softbox", (0.0, -0.2, 3.1), 9.0, 3.0, "top"),
    ]
    floor = {"auto": want and not has_plate, "on": True, "off": False}[opt["look.floor"]]
    return dict(enabled=want, lights=lights, world_color=list(style["world"]), world_strength=style["strength"],
                floor=floor, floor_z=floor_z, floor_size=r * 40.0,
                floor_center=[float(c[0]), float(c[1])], floor_r0=r * 1.5, floor_r1=r * 6.0,
                floor_color=tone("look.floor_color", t["floor"]), floor_rough=0.7,
                backdrop_top=tone("look.backdrop_top", t["backdrop_top"]),
                backdrop_bottom=tone("look.backdrop_bottom", t["backdrop_bottom"]))


# ------------------------------------------------------------------ assemble
def finish(b):
    d, s, rng = b.d, b.s, b.rng
    speed = max(s.speed, 0.05)
    pl = Plan(settings=s)
    pl.source = d.doc["source"]
    pl.n_mice = b.n_mice
    pl.ui_w, pl.ui_h = canvas(s.width, s.height)
    aspect = s.width / s.height
    opt = s.opt
    pl.ui_mode = opt["ui.layout"]
    pl.shading = bool(opt["pace.shading"])

    keep, lo, hi, sample = b.frame
    center = (lo + hi) * 0.5
    radius = max(float(np.linalg.norm(hi - lo)) * 0.5, 1e-3)
    pl.center, pl.radius = center, radius
    pl.ground = float(lo[2]) - radius * 0.01
    step = 10.0 ** math.floor(math.log10(max(radius / 2.5, 1e-6)))
    pl.grid = step
    pl.fade = radius * 5.5

    # ------------------------------------------------ cameras
    hero_az, hero_el = scene_view(d, center, opt)
    lens0 = float(opt["camera.lens"])
    margin0 = float(opt["camera.margin"])
    swing = float(opt["camera.swing"]) / 74.0
    lb = 0.0 if opt["look.letterbox"] == "off" else float(opt["look.letterbox"])
    tanx, tany = lens_tans(lens0, aspect)
    fx_, fy_ = fit_tans(lens0, aspect, lb)
    eye, target, dist = fit_camera(sample, math.radians(hero_az), math.radians(hero_el), fx_, fy_, margin0)
    T0, T1, T2, T3 = 0.0, b.model_end, b.shade_end, b.t_light1
    keys_t, keys_v = [], []

    def key(t, az, el, dd, tgt, ty):
        keys_t.append(t)
        keys_v.append([az, el, dd, tgt[0], tgt[1], tgt[2], ty])

    # While the workspace is up the camera has to fit the viewport panel, whose
    # shape in a stacked layout is nothing like the shape of the final frame.
    D = dist
    if is_wide(pl.ui_w):
        Db, tb, dm = D, tany, 1.16
    else:
        v = layout_at(Fx(0.0, 0.0), pl.ui_w, pl.ui_h, pl.ui_mode)["view"]
        tvx, tvy = lens_tans(lens0, v[2] / max(v[3], 1.0))
        _, _, Db = fit_camera(sample, math.radians(hero_az), math.radians(hero_el), tvx, tvy, margin0)
        tb, dm = tvy, 1.1
    key(T0 - 0.001, hero_az - 74 * swing, 30, Db * dm * 1.08, target, tb)
    key(T1 * 0.4, hero_az - 44 * swing, 24, Db * dm * 1.04, target, tb)
    key(T1, hero_az - 12 * swing, 21, Db * dm, target, tb)
    key(T2, hero_az - 26 * swing, 18, Db * dm * 0.96, target, tb)
    key(T3, hero_az, hero_el, D, target, tany)
    key(T3 + 60.0, hero_az, hero_el, D, target, tany)
    pl.cam_path = follow_action(CubicPath(keys_t, keys_v), b, target, radius, T1, T2, T3, float(opt["camera.follow"]))

    # ortho views frame the subject
    half = radius * 1.12
    pl.ortho_cams = [
        ("Top Orthographic", Camera(center + np.array([0, 0, radius * 6]), center, tany=half, ortho=True, up=(0, 1, 0))),
        ("Front Orthographic", Camera(center + np.array([0, -radius * 6, 0]), center, tany=half, ortho=True)),
        ("Right Orthographic", Camera(center + np.array([radius * 6, 0, 0]), center, tany=half, ortho=True)),
    ]

    # ------------------------------------------------ shots for Cycles
    q = QUALITY[s.quality]
    q = dict(q)
    q["stills"] = int(opt["render.samples"]) or q["stills"]
    q["turn"] = int(opt["render.turn_samples"]) or q["turn"]
    q["frames"] = int(opt["show.turn_frames"]) or q["frames"]
    pl.stills = make_shots(d, keep, lo, hi, sample, hero_az, hero_el, aspect, opt, lb)
    pl.turn_n = q["frames"]
    pl.turntable = turntable_shots(sample, hero_az, hero_el, q["frames"], aspect, lens0,
                                   1.0 if opt["show.direction"] == "ccw" else -1.0, margin0 + 0.03, lb)
    pl.rig = studio_rig(d, lo, hi, keep, s)
    pl.q = q

    # ------------------------------------------------ per-part keyframes
    n = d.npart
    add0, add1, g0, g1, r0, r1, s0, s1, settle = (np.full(n, np.inf) for _ in range(9))
    mod0, mod1 = np.full(n, np.inf), np.zeros(n)
    paint0, paint1 = np.full(n, np.inf), np.full(n, np.inf)
    wire0, wire1 = np.full(n, np.inf), np.zeros(n)
    meta = b.meta
    a_ofs = np.zeros(n + 1, np.int64)
    b_ofs = np.zeros(n + 1, np.int64)
    for i in range(n):
        a_ofs[i + 1] = a_ofs[i] + meta[i]["ka"]
        b_ofs[i + 1] = b_ofs[i] + max(meta[i]["kb"], 0)
    a_t0 = np.zeros(int(a_ofs[-1]))
    a_t1 = np.zeros(int(a_ofs[-1]))
    b_t0 = np.zeros(max(int(b_ofs[-1]), 1))
    b_t1 = np.zeros(max(int(b_ofs[-1]), 1))
    a_t0[:] = b_t0[:] = 0.0

    for o in b.ops:
        i = o["part"]
        if i < 0:
            continue
        k = o["kind"]
        if k == "add":
            add0[i], add1[i] = o["t0"], o["t1"]
        elif k == "mod":
            mod0[i], mod1[i] = min(mod0[i], o["t0"]), max(mod1[i], o["t1"])
        elif k == "grab":
            g0[i], g1[i] = o["t0"], o["t1"]
        elif k == "rot":
            r0[i], r1[i] = o["t0"], o["t1"]
        elif k == "scale":
            s0[i], s1[i] = o["t0"], o["t1"]
        elif k == "edit":
            base = a_ofs[i] if o["stage"] == "A" else b_ofs[i]
            arr0, arr1 = (a_t0, a_t1) if o["stage"] == "A" else (b_t0, b_t1)
            arr0[base + o["patch"]] = o["t0"]
            arr1[base + o["patch"]] = o["t1"]
            wire0[i] = min(wire0[i], o["t0"])
            wire1[i] = max(wire1[i], o["t1"])
        elif k in ("assign", "paint"):
            paint0[i] = min(paint0[i], o["t0"])
            paint1[i] = max(paint1[i], o["t1"]) if np.isfinite(paint1[i]) else o["t1"]
    if not opt["pace.shading"]:
        for i, p in enumerate(d.parts):
            if p["mats"] and p["mats"][0] >= 0:
                paint0[i], paint1[i] = b.model_end, b.shade_end
    r0[~np.isfinite(r0)] = -1.0
    r1[~np.isfinite(r1)] = -1.0
    # an object that was never moved or resized simply arrives where it belongs
    never_moved = ~np.isfinite(g1)
    g0[never_moved], g1[never_moved] = add1[never_moved], add1[never_moved]
    never_scaled = ~np.isfinite(s1)
    s0[never_scaled], s1[never_scaled] = g1[never_scaled], g1[never_scaled]

    # one patch only: the change happens with the modifier that makes it, else with the placement
    for i in range(n):
        if meta[i]["tier"] == 0:
            has_mod = np.isfinite(mod0[i])
            a_t0[a_ofs[i]] = mod0[i] if has_mod else s0[i]
            a_t1[a_ofs[i]] = mod1[i] if has_mod else s1[i]
    settle = np.maximum.reduce([g1, s1, wire1, r1, mod1])

    pl.part_add, pl.part_g0, pl.part_g1 = add0, g0, g1
    pl.part_r0, pl.part_r1, pl.part_s0, pl.part_s1 = r0, r1, s0, s1
    pl.part_settle = settle
    pl.wire0, pl.wire1 = wire0, wire1
    pl.paint0, pl.paint1 = paint0, paint1
    pl.a_ofs, pl.b_ofs, pl.a_t0, pl.a_t1, pl.b_t0, pl.b_t1 = a_ofs, b_ofs, a_t0, a_t1, b_t0, b_t1
    pl.has_b = np.array([m["kb"] > 0 for m in meta])
    pl.smooth = np.array([m["smooth"] for m in meta])
    pl.has_any_b = bool(b_ofs[-1] > 0)

    # spawn points: near the 3D cursor, drawn towards the middle of the world
    pl.pivot_final = np.array([m["center"] for m in meta], np.float64)
    spawn = center + (pl.pivot_final - center) * 0.3
    spawn[:, 2] = np.maximum(spawn[:, 2], pl.ground + radius * 0.05)
    spawn += rng.normal(0, radius * 0.05, spawn.shape)
    spawn[never_moved] = pl.pivot_final[never_moved]
    pl.pivot_spawn = spawn
    # placement replays the object's real transform: from unit scale and no rotation to what it has
    pl.scale_from = np.clip(np.array([1.0 / max(float(np.prod(m["scale"])) ** (1.0 / 3.0), 1e-6) for m in meta]), 0.1, 3.0)
    pl.scale_from[never_scaled] = 1.0
    pl.spin = np.array([m["angle"] for m in meta], np.float64)
    pl.spin_axis = np.array([m["axis"] for m in meta], np.float64)

    # ------------------------------------------------ timeline bounds
    pl.T = dict(model=(0.0, b.model_end), shade=(b.model_end, b.shade_end),
                light=(b.shade_end, b.t_light1), render=(b.t_render0, b.t_render1))
    pl.show0 = b.t_render1
    pl.mat_windows = b.mat_windows
    pl.show_D = float(s.showcase)
    pl.schedule = show_schedule(pl.show_D, items_of(opt, "show.segments"))
    pl.duration = pl.show0 + pl.show_D

    # ------------------------------------------------ render tiles
    TW, TH = (16, 9) if aspect >= 1.2 else ((9, 16) if aspect <= 0.8 else (12, 12))
    tiles = []
    cx, cy = (TW - 1) / 2, (TH - 1) / 2
    for y in range(TH if opt["pace.render"] else 0):
        for x in range(TW):
            tiles.append((math.hypot((x - cx) / TW, (y - cy) / TH) + float(rng.uniform(0, 0.08)), x, y))
    tiles.sort()
    t_r0, t_r1 = b.t_render0 + 0.15, b.t_render1 - 0.3
    span = t_r1 - t_r0
    n_t = max(1, len(tiles))
    tile_ops = []
    free = np.full(b.n_mice, t_r0)
    dur = span * min(0.7, b.n_mice / n_t * 0.92)
    for rank, (_, x, y) in enumerate(tiles):
        c = int(np.argmin(free))
        t0 = free[c] + float(rng.uniform(0, 0.03))
        t0 = max(t0, t_r0 + rank / n_t * span * 0.6)
        t1 = min(t0 + dur, t_r1)
        free[c] = t1
        tile_ops.append((x, y, t0, t1, c))
    pl.tiles = tile_ops
    pl.tile_grid = (TW, TH)

    # ------------------------------------------------ light gizmos
    lights = []
    t_l0 = b.t_light0
    for i, L in enumerate([] if not opt["pace.lighting"] else pl.rig["lights"] if pl.rig["enabled"] else
                          [dict(name=l["name"], loc=l["loc"], target=None) for l in d.doc["lights"]]):
        lights.append(dict(name=L["name"], loc=np.array(L["loc"], np.float64),
                           t0=t_l0 + i * 0.15 / speed, t1=t_l0 + (i + 3.5) * 0.18 / speed))
    pl.lights = lights

    # ------------------------------------------------ sort ops
    b.ops.sort(key=lambda o: o["t0"])
    pl.op_t0 = np.array([o["t0"] for o in b.ops])
    pl.op_t1 = np.array([o["t1"] for o in b.ops])
    pl.op_part = np.array([o["part"] for o in b.ops], np.int32)
    pl.op_cursor = np.array([o["cursor"] for o in b.ops], np.int32)
    pl.op_kind = [o["kind"] for o in b.ops]
    pl.op_label = [o["label"] for o in b.ops]
    pl.op_api = [o["api"] for o in b.ops]
    pl.op_chip = [o["chip"] for o in b.ops]
    pl.op_stage = [o["stage"] for o in b.ops]
    pl.op_patch = [o["patch"] for o in b.ops]
    pl.op_boost = np.array([o["boost"] for o in b.ops], np.float64)
    # a pop-up is only worth drawing if it can be read: the big work shows nearly always, the blur rarely
    pl.op_show = rng.random(len(b.ops)) < np.where(pl.op_boost < 1.5, 0.85, 0.12)
    logged = step_log.by_object(s.step_log)
    names = {p["name"] for p in d.parts}
    pl.step_report = dict(objects=sorted(n_ for n_ in logged if n_ in names), unknown=sorted(n_ for n_ in logged if n_ not in names),
                          steps=sum(len(v) for v in logged.values())) if s.step_log else None
    pl.n_ops = len(b.ops)

    # outliner: rows appear in the order objects were added
    pl.row_of = {}
    order = np.argsort(add0)
    pl.row_order = order
    pl.row_t = add0[order]
    rank = np.empty(n, np.int32)
    rank[order] = np.arange(n)
    pl.part_rank = rank

    # timeline histogram
    bins = 300
    edges = np.linspace(0, pl.show0, bins + 1)
    pl.hist, _ = np.histogram(pl.op_t0, bins=edges)

    # ------------------------------------------------ bake mouse trajectories
    bake_cursors(pl, b, speed)
    return pl


def fx_at(pl, t):
    if pl.ui_mode == "clean":
        return Fx(0.0, 1.0)
    sh0, sh1 = pl.T["shade"]
    l0, l1 = pl.T["light"]
    r0, r1 = pl.T["render"]
    rise = min(0.42, (sh1 - sh0) * 0.2)
    if t < sh0 - rise:
        nodes = 0.0
    elif t < sh1:
        nodes = float(ease_io(min(1.0, (t - (sh0 - rise)) / rise)))
    else:
        nodes = float(1.0 - ease_io(min(1.0, (t - sh1) / max(0.4, (l1 - l0) * 0.3))))
    full = float(ease_io(np.clip((t - (r0 - 0.42)) / 0.42, 0.0, 1.0)))
    if not pl.shading or pl.ui_mode != "full":
        nodes = 0.0
    return Fx(nodes, full)


def cam_at(pl, t):
    az, el, dd, tx, ty, tz, tany = pl.cam_path(t)
    off = np.array(spherical(az, el)) * dd
    target = np.array([tx, ty, tz])
    return Camera(target + off, target, tany=float(tany))


def project_many(world, eyes, Rs, tany, rect_x, rect_y, rect_w, rect_h, ortho):
    q = world - eyes
    x = np.einsum("ij,ij->i", q, Rs[:, 0])
    y = np.einsum("ij,ij->i", q, Rs[:, 1])
    z = np.maximum(np.einsum("ij,ij->i", q, Rs[:, 2]), 1e-5)
    tanx = tany * rect_w / np.maximum(rect_h, 1.0)
    if ortho:
        sx, sy = x / tanx, y / tany
    else:
        sx, sy = x / (z * tanx), y / (z * tany)
    return rect_x + rect_w * (0.5 + 0.5 * sx), rect_y + rect_h * (0.5 - 0.5 * sy)


def bake_cursors(pl, b, speed):
    rng = b.rng
    n = int(pl.duration * BAKE_HZ) + 2
    N = b.n_mice
    pos = np.zeros((N, n, 2), np.float32)
    icon = np.zeros((N, n), np.uint8)
    down = np.zeros((N, n), np.uint8)

    times = np.arange(n) / BAKE_HZ
    stop = int(min(pl.show0 + 0.6, pl.duration) * BAKE_HZ)
    fxs = [fx_at(pl, float(t)) for t in times[:stop]]
    rects = [layout_at(fx, pl.ui_w, pl.ui_h, pl.ui_mode) for fx in fxs]
    eyes = np.empty((stop, 3))
    Rs = np.empty((stop, 3, 3))
    tanys = np.empty(stop)
    for i, t in enumerate(times[:stop]):
        c = cam_at(pl, float(t))
        eyes[i] = c.eye
        Rs[i] = np.stack(c.basis())
        tanys[i] = c.tany
    view_r = np.array([r["view"] for r in rects])
    ortho_r = [np.array([r["ortho%d" % k] for r in rects]) for k in range(3)]

    # ------ collect each mouse's agenda
    agenda = [[] for _ in range(N)]
    for o in b.ops:
        agenda[o["cursor"]].append(o)
    for c in range(N):
        agenda[c].sort(key=lambda o: o["t0"])
    # filler: idle mice fidget with the interface rather than freezing
    fidget = bool(pl.settings.opt["swarm.fidget"])
    widgets = ["outliner"] * 4 + ["timeline"] * 2 + ["info"] * 3 + ["gizmo"] + ["props.tab"] +               ["props.field.%d" % k for k in range(9)] + ["tool.%d" % k for k in range(12)]
    t_end = pl.T["render"][0] - 0.1
    for c in range(N):
        filled = []
        prev_end = 0.05
        for o in agenda[c] + [dict(t0=t_end, t1=t_end, kind="_end", part=-1)]:
            gap = o["t0"] - prev_end
            while fidget and gap > 0.7 / speed:
                dur = float(rng.uniform(0.18, 0.4)) / speed
                t0 = prev_end + float(rng.uniform(0.2, 0.34)) / speed
                if t0 + dur + 0.1 > o["t0"]:
                    break
                w = widgets[int(rng.integers(len(widgets)))]
                filled.append(dict(t0=t0, t1=t0 + dur, kind="ui", part=-1, icon="pointer", stage=None, patch=None,
                                   anchor=("ui", w), chip=None, api=None))
                prev_end = t0 + dur
                gap = o["t0"] - prev_end
            if o["kind"] != "_end":
                filled.append(o)
                prev_end = o["t1"]
        agenda[c] = filled

    # ------ every mouse moves in its own way
    human = float(pl.settings.opt["swarm.humanness"])
    p_over = rng.uniform(0.0, 0.11, N) * human
    p_bow = rng.uniform(0.05, 0.26, N) * human
    p_tremor = rng.uniform(0.15, 1.0, N) * human

    def gesture(o, xs, ys, tag):
        gx, gy = gesture_raw(o, xs, ys, tag)
        return xs + (gx - xs) * human, ys + (gy - ys) * human

    def gesture_raw(o, xs, ys, tag):
        """What the wrist does while the button is down."""
        m = len(xs)
        if m < 4 or o["anchor"][0] == "tile":
            return xs, ys
        u = np.linspace(0.0, 1.0, m)
        env = np.sin(u * math.pi) ** 0.6
        label, kind = o.get("label") or "", o["kind"]
        a = float(rng.uniform(0, math.tau))
        ca, sa = math.cos(a), math.sin(a)
        if kind == "paint":
            amp = float(rng.uniform(26, 60))
            return xs + amp * np.sin(u * math.tau * 3.5) * env, ys + amp * 0.35 * (u - 0.5) * 2 * env
        if kind == "cut":                         # sliding along an edge
            amp = float(rng.uniform(10, 26))
            return xs + ca * amp * np.sin(u * math.tau), ys + sa * amp * np.sin(u * math.tau)
        if kind != "edit":
            return xs, ys
        if o.get("stage") == "B":                 # sculpt: scribbled strokes
            ar, br = float(rng.uniform(8, 26)), float(rng.uniform(6, 20))
            f1, f2 = float(rng.uniform(1.2, 2.6)), float(rng.uniform(1.6, 3.4))
            return xs + ar * np.sin(u * math.tau * f1 + a) * env, ys + br * np.sin(u * math.tau * f2) * env
        if label.startswith(("Extrude", "Proportional", "Move", "Shrink")):  # pull out, and hold
            ln_ = float(rng.uniform(16, 44))
            k = np.asarray(ease_io(np.minimum(1.0, u * 1.5)))
            return xs + ca * ln_ * k, ys - abs(sa) * ln_ * k
        if label.startswith(("Bevel", "Inset", "Resize")):  # drag away from the pivot, ease back
            ln_ = float(rng.uniform(14, 32))
            return xs + ca * ln_ * env, ys + sa * ln_ * env
        if label.startswith("Rotate"):             # an arc around the pivot
            ln_ = float(rng.uniform(14, 30))
            return xs + ln_ * np.cos(a + u * math.pi), ys + ln_ * np.sin(a + u * math.pi)
        return xs, ys

    # ------ anchor evaluators
    meta = b.meta
    edit_view = rng.integers(0, 6, size=len(b.ops) + 10)
    a_ofs = pl.a_ofs

    def part_track(i, idx):
        """World-space translation offset and scale of part i at sample idx."""
        t = times[idx]
        ug = ease_io((t - pl.part_g0[i]) / max(pl.part_g1[i] - pl.part_g0[i], 1e-5))
        return pl.pivot_spawn[i] + (pl.pivot_final[i] - pl.pivot_spawn[i]) * ug[:, None]

    def world_anchor(anc, idx):
        i = anc[1]
        kind = anc[0]
        if kind == "pivot":
            return part_track(i, idx)
        if kind == "seed":
            return np.repeat(meta[i]["seeds"][anc[2]][None], len(idx), axis=0)
        # patch
        stage, k = anc[2], anc[3]
        if stage == "A":
            u = ease_out_back((times[idx] - pl.a_t0[a_ofs[i] + k]) / max(pl.a_t1[a_ofs[i] + k] - pl.a_t0[a_ofs[i] + k], 1e-5))
            a, p = meta[i]["box_c"][k], meta[i]["pa_centers"][k]
            return a + (p - a) * u[:, None]
        return np.repeat(meta[i]["pb_centers"][k][None], len(idx), axis=0)

    tile_w, tile_h = pl.ui_w / pl.tile_grid[0], pl.ui_h / pl.tile_grid[1]

    def eval_event(o, n0, n1, tag):
        xs, ys = anchor_path(o, n0, n1, tag)
        return gesture(o, np.asarray(xs, np.float64), np.asarray(ys, np.float64), tag)

    def anchor_path(o, n0, n1, tag):
        idx = np.arange(n0, n1)
        if len(idx) == 0:
            idx = np.array([n0])
        anc = o["anchor"]
        s_ = tag % 997
        if anc[0] == "ui":
            pts = np.array([anchor_xy(anc[1], rects[min(j, stop - 1)], times[j], seed=s_) for j in idx], np.float64)
            xs, ys = pts[:, 0], pts[:, 1]
            if o["kind"] == "tweak":
                u = (times[idx] - times[n0]) / max(times[idx[-1]] - times[n0], 1e-5)
                xs = xs + np.sin(u * math.pi * 2) * 34
            elif anc[1] == "menu.add":
                ys = ys + np.minimum(1.0, (times[idx] - times[n0]) / max(times[idx[-1]] - times[n0] + 1e-5, 1e-5)) * 44
            return xs, ys
        if anc[0] == "tile":
            return np.full(len(idx), anc[1] * tile_w + tile_w * 0.5), np.full(len(idx), anc[2] * tile_h + tile_h * 0.5)
        w = world_anchor(anc, idx)
        cl = np.minimum(idx, stop - 1)
        which = edit_view[tag % len(edit_view)]
        ortho_ok = which < 3 and ortho_r[int(which)][0, 3] > 10
        if anc[0] in ("patch", "pivot") and ortho_ok and pl.T["model"][1] > times[n0]:
            rr = ortho_r[int(which)][cl]
            cam = pl.ortho_cams[int(which)][1]
            r_, u_, f_ = cam.basis()
            xs, ys = project_many(w, np.repeat(np.asarray(cam.eye, np.float64)[None], len(idx), 0),
                                  np.repeat(np.stack([r_, u_, f_])[None], len(idx), 0), cam.tany,
                                  rr[:, 0], rr[:, 1], rr[:, 2], rr[:, 3], True)
            return xs, ys
        vr = view_r[cl]
        return project_many(w, eyes[cl], Rs[cl], tanys[cl], vr[:, 0], vr[:, 1], vr[:, 2], vr[:, 3], False)

    # ------ tiles join the agenda
    for x, y, t0, t1, c in pl.tiles:
        agenda[c].append(dict(t0=t0, t1=t1, kind="tile", part=-1, icon="cross", stage=None, patch=None,
                              anchor=("tile", x, y), chip=None, api=None))
    for c in range(N):
        agenda[c].sort(key=lambda o: o["t0"])

    # ------ rasterize agenda into the bake
    W, H = pl.ui_w, pl.ui_h
    for c in range(N):
        a = float(rng.uniform(0, math.tau))
        ex0, ey0 = W / 2 + math.cos(a) * W * 0.75, H / 2 + math.sin(a) * H * 0.85
        prev_n, prev_x, prev_y = 0, ex0, ey0
        ev = agenda[c]
        for k, o in enumerate(ev):
            n0 = max(int(o["t0"] * BAKE_HZ), prev_n)
            n1 = min(max(int(o["t1"] * BAKE_HZ), n0 + 1), stop)
            if n0 >= stop:
                break
            xs, ys = eval_event(o, n0, n1, k * 13 + c * 101)
            ln = n1 - n0
            pos[c, n0:n1, 0], pos[c, n0:n1, 1] = xs[:ln], ys[:ln]
            icon[c, n0:n1] = ICON_ID.get(o["icon"], 0)
            down[c, n0:n1] = 1
            # travel in
            if n0 > prev_n:
                m = n0 - prev_n
                s_ = (np.arange(m) + 1.0) / (m + 1.0)
                e = ease_io(s_)
                tx, ty = float(xs[0]), float(ys[0])
                dx, dy = tx - prev_x, ty - prev_y
                ln2 = math.hypot(dx, dy) + 1e-6
                nx, ny = -dy / ln2, dx / ln2
                bow = float(rng.uniform(-1.0, 1.0)) * p_bow[c] * min(ln2, 420)
                # a real hand arrives fast, slips a little past, and corrects
                slip = p_over[c] * min(1.0, ln2 / 320.0) * np.sin(np.clip((s_ - 0.62) / 0.38, 0.0, 1.0) * math.pi)
                pos[c, prev_n:n0, 0] = prev_x + dx * (e + slip) + nx * bow * np.sin(e * math.pi)
                pos[c, prev_n:n0, 1] = prev_y + dy * (e + slip) + ny * bow * np.sin(e * math.pi)
                icon[c, prev_n:n0] = ICON_ID["arrow"]
            prev_n, prev_x, prev_y = n1, float(xs[-1]), float(ys[-1])
        # afterwards: leave the frame
        if prev_n < stop:
            a = float(rng.uniform(0, math.tau))
            ex, ey = W / 2 + math.cos(a) * W * 0.75, H / 2 + math.sin(a) * H * 0.85
            m = stop - prev_n
            s_ = ease_io((np.arange(m) + 1.0) / m)
            pos[c, prev_n:stop, 0] = prev_x + (ex - prev_x) * s_
            pos[c, prev_n:stop, 1] = prev_y + (ey - prev_y) * s_
        # the slight tremor of a hand on a mouse (roughly 10 Hz, below a pixel or two)
        knots = np.arange(0, stop + 12, 12)
        tx_ = rng.normal(0.0, p_tremor[c], len(knots))
        ty_ = rng.normal(0.0, p_tremor[c], len(knots))
        sample = np.arange(stop)
        pos[c, :stop, 0] += np.interp(sample, knots, tx_).astype(np.float32)
        pos[c, :stop, 1] += np.interp(sample, knots, ty_).astype(np.float32)
        pos[c, stop:, 0], pos[c, stop:, 1] = -400.0, -400.0
    pl.cpos, pl.cicon, pl.cdown = pos, icon, down
    pl.bake_n = n
    pl.agenda_ripples = np.array(sorted(
        (o["t0"], c, KIND_SOUND.get(o["kind"], 0)) for c in range(N) for o in agenda[c] if o["kind"] != "_end"),
        np.float64).reshape(-1, 3)

    # ------ each mouse has a look of its own, and a few of them have names
    opt = pl.settings.opt
    allowed = set(items_of(opt, "swarm.skins")) or {"classic"}
    weights = np.array([w if n_ in allowed else 0.0 for n_, w in zip(SKIN_NAMES, SKIN_WEIGHTS)], np.float64)
    pl.cskin = rng.choice(len(SKIN_WEIGHTS), size=N, p=weights / weights.sum())
    names = [x.strip() for x in opt["swarm.crew"].split(",") if x.strip()] or list(CREW)
    rng.shuffle(names)
    tags = int(opt["swarm.names"]) if int(opt["swarm.names"]) >= 0 else max(3, min(len(names), N // 8))
    tags = min(N, tags)
    pl.crew = {int(k): names[j % len(names)] for j, k in enumerate(rng.choice(N, size=tags, replace=False))}
    pl.cheading = {int(k): bake_heading(pos[k], stop) for k in np.flatnonzero(pl.cskin == SKIN_CRITTER)}


def follow_action(path, b, target, radius, T1, T2, T3, amount):
    """Drifts the camera toward wherever the swarm is busiest, so that the eye
    has somewhere to go. Smoothed heavily: it should feel like an operator."""
    if amount <= 0 or T2 < 1.0:
        return path
    ops = [(o["t0"], o["t1"], o["part"]) for o in b.ops if o["part"] >= 0]
    if not ops:
        return path
    t0s, t1s, parts = (np.array(x) for x in zip(*ops))
    centers = np.array([m["center"] for m in b.meta], np.float64)
    ts = np.arange(0.0, T2, max(0.25, T2 / 160.0))
    raw = np.zeros((len(ts), 3))
    for i, t in enumerate(ts):
        mask = (t0s < t + 0.8) & (t1s > t - 0.8)
        raw[i] = centers[parts[mask]].mean(0) - target if mask.any() else raw[i - 1] if i else 0.0
    width = max(1, int(2.0 / (ts[1] - ts[0]))) if len(ts) > 1 else 1
    kernel = np.hanning(width * 2 + 3)
    kernel /= kernel.sum()
    pad = np.pad(raw, ((width + 1, width + 1), (0, 0)), mode="edge")
    smooth = np.stack([np.convolve(pad[:, c], kernel, mode="valid") for c in range(3)], 1)
    fade = np.array([1.0 - float(ease_io((t - T1 * 0.85) / max(T2 - T1 * 0.85, 1e-3))) for t in ts])
    keys_t, keys_v = [], []
    for i, t in enumerate(ts):
        v = np.array(path(float(t)), np.float64)
        off = smooth[i] * amount * 0.42
        ln = float(np.linalg.norm(off))
        if ln > radius * 0.45:
            off *= radius * 0.45 / ln
        v[3:6] += off * fade[i]
        v[2] *= 1.0 - 0.09 * amount * fade[i]
        keys_t.append(float(t))
        keys_v.append(v.tolist())
    for t in (T2, T3, T3 + 60.0):
        if t > keys_t[-1] + 1e-6:
            keys_t.append(t)
            keys_v.append(path(t).tolist())
    return CubicPath(keys_t, keys_v)


def bake_heading(p, stop, half=6):
    """Direction of travel for a mouse drawn as an animal: smoothed, and held
    while it stands still so that it never jitters on the spot."""
    n = len(p)
    v = np.zeros((n, 2), np.float32)
    v[half:n - half] = p[2 * half:] - p[:-2 * half]
    out = np.full(n, -2.2, np.float32)
    last = -2.2
    for i in range(min(stop, n)):
        if v[i, 0] * v[i, 0] + v[i, 1] * v[i, 1] > 36.0:
            last = float(math.atan2(v[i, 1], v[i, 0]))
        out[i] = last
    return out


def save(pl, b_arrays, work):
    np.save(os.path.join(work, "PS.npy"), b_arrays[0])
    np.save(os.path.join(work, "PA.npy"), b_arrays[1])
    np.save(os.path.join(work, "PB.npy"), b_arrays[2])
    np.save(os.path.join(work, "VPAINT.npy"), b_arrays[3])
    np.save(os.path.join(work, "SRC.npy"), b_arrays[4])
    with open(os.path.join(work, "plan.pkl"), "wb") as f:
        pickle.dump(pl, f, protocol=4)
