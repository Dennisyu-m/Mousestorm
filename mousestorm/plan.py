# SPDX-License-Identifier: GPL-3.0-or-later
"""The choreographer. Reverse-engineers a plausible modelling session from a
finished scene and schedules it across a swarm of mice.

Everything produced here is deterministic data. A frame is then a pure function
of (plan, t), which is what allows frames to be rendered out of order across
many processes.
"""
import heapq
import json
import math
from dataclasses import dataclass, field

import numpy as np

from . import forensics, options
from . import steps as step_log
from .scene import SceneData


@dataclass
class Settings:
    width: int = 1920
    height: int = 1080
    fps: int = 60
    speed: float = 1.0
    mice: int = 0                 # 0 = auto
    fmt: str = "mp4"
    quality: str = "standard"
    showcase: float = 20.0
    duration: float = 0.0         # > 0: pick the speed that gives this film length
    soundtrack: str = "score"     # "score", "clicks" or "off"
    ss: int = 2
    seed: int = 7
    sound: bool = True
    lighting: str = "auto"
    device: str = "auto"          # "auto", "cpu" or "gpu"
    gpu: str = ""                 # one Cycles device id; empty = all of them
    workers: int = 0
    threads: int = 0
    opt: dict = field(default_factory=dict)      # advanced options, see options.py
    steps: str = ""                              # optional .msteps file
    step_log: dict = None                        # its parsed contents


SPEED_RANGE = (0.05, 40.0)
SOUNDTRACKS = ("score", "clicks", "off")
LIGHTINGS = ("auto", "studio", "file")
DEVICES = ("auto", "cpu", "gpu")


def normalized(s: "Settings"):
    """One validation point for every front end. Raises ValueError with a
    message fit for a user; otherwise returns s with sane, encodable values."""
    for name in ("speed", "showcase", "duration"):
        v = float(getattr(s, name))
        if not math.isfinite(v):
            raise ValueError("%s must be a finite number" % name)
        setattr(s, name, v)
    if s.width < 64 or s.height < 64:
        raise ValueError("Resolution is too small (minimum 64x64)")
    if s.width > 8192 or s.height > 8192:
        raise ValueError("Resolution is too large (maximum 8192x8192)")
    # 4:2:0 video needs even dimensions; the odd pixel is not worth an error
    s.width, s.height = int(s.width) & ~1, int(s.height) & ~1
    s.fps = int(np.clip(int(s.fps), 6, 240))
    s.mice = int(np.clip(int(s.mice), 0, 2048))
    s.speed = float(np.clip(s.speed, *SPEED_RANGE))
    s.showcase = float(np.clip(s.showcase, 6.0, 300.0))
    s.duration = max(0.0, s.duration)
    if s.quality not in QUALITY:
        raise ValueError("quality must be one of %s" % ", ".join(sorted(QUALITY)))
    if s.lighting not in LIGHTINGS:
        raise ValueError("lighting must be one of %s" % ", ".join(LIGHTINGS))
    if s.device not in DEVICES:
        raise ValueError("device must be one of %s" % ", ".join(DEVICES))
    if s.soundtrack not in SOUNDTRACKS:
        raise ValueError("soundtrack must be one of %s" % ", ".join(SOUNDTRACKS))
    s.opt = options.resolve(s.opt)
    if s.steps and s.step_log is None:
        s.step_log = step_log.load(s.steps)
    if not s.sound:
        s.soundtrack = "off"
    s.sound = s.soundtrack != "off"
    return s


SEG_WEIGHT = {"hero": 0.14, "turntable": 0.34, "shoot": 0.34, "poster": 0.13, "sheet": 0.18}


def show_schedule(D, segments):
    """How the finale divides its time: [(name, start, length)]. The soundtrack
    and the picture both read this, so they stay in step."""
    segs = [s for s in segments if s in SEG_WEIGHT] or ["hero"]
    total = sum(SEG_WEIGHT[s] for s in segs)
    out, t = [], 0.0
    for s in segs:
        d = D * SEG_WEIGHT[s] / total
        out.append((s, t, d))
        t += d
    return out


def segment(schedule, name):
    return next(((t, d) for s, t, d in schedule if s == name), None)


def light_time(speed, lighting=True):
    return (3.4 if lighting else 0.3) / speed


def render_time(speed, tiles=True):
    return max(5.0 / max(speed, 1.0) ** 0.7, 2.6) if tiles else 0.6


def op_boost(impact, major, top):
    """How much faster than the film's speed an operation plays. impact is the size of
    the change as a share of the model: at `major` and above it plays at the film's
    speed; at an eighth of that and below, `top` times faster; geometric in between."""
    impact = np.asarray(impact, np.float64)
    if top <= 1.0:
        return np.ones_like(impact)
    minor = major / 8.0
    t = np.clip(np.log(np.maximum(impact, 1e-12) / minor) / math.log(8.0), 0.0, 1.0)
    return top ** (1.0 - t)


def film_time(speed, model, shade, lighting=True, tiles=True):
    """Seconds from the first click to the end of the render, for work measured at speed 1."""
    return (model + shade) / speed + light_time(speed, lighting) + render_time(speed, tiles)


def solve_speed(model, shade, target, lighting=True, tiles=True):
    """The speed whose build lasts `target` seconds (clamped to the legal range)."""
    lo, hi = SPEED_RANGE
    if film_time(hi, model, shade, lighting, tiles) >= target:
        return hi
    if film_time(lo, model, shade, lighting, tiles) <= target:
        return lo
    for _ in range(60):
        mid = math.sqrt(lo * hi)
        if film_time(mid, model, shade, lighting, tiles) > target:
            lo = mid
        else:
            hi = mid
    return math.sqrt(lo * hi)


QUALITY = {
    "draft": dict(stills=24, turn=8, frames=48),
    "standard": dict(stills=96, turn=24, frames=96),
    "high": dict(stills=320, turn=64, frames=144),
}

KIND_ICON = {
    "add": "arrow", "grab": "move", "rot": "rotate", "scale": "scale",
    "mod": "pointer", "assign": "paint", "tweak": "hresize",
    "node": "grab", "paint": "brush", "light": "move", "cam": "cross",
    "tile": "cross", "ui": "pointer", "pick": "eyedrop", "cut": "cross",
}


def ease_out_back(x):
    x = np.clip(x, 0.0, 1.0)
    c = 1.70158 * 0.75
    return 1.0 + (c + 1.0) * (x - 1.0) ** 3 + c * (x - 1.0) ** 2


def ease_io(x):
    x = np.clip(x, 0.0, 1.0)
    return x * x * x * (x * (6.0 * x - 15.0) + 10.0)


def ease_out(x):
    x = np.clip(x, 0.0, 1.0)
    return 1.0 - (1.0 - x) ** 3


def rot_z(a):
    c, s = math.cos(a), math.sin(a)
    return np.array([[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]])


# ------------------------------------------------------------- geometry prep
def rot_axis(axis, angle):
    """Rotation matrix about a unit axis."""
    x, y, z = axis
    c, si, v = math.cos(angle), math.sin(angle), 1.0 - math.cos(angle)
    return np.array([[c + x * x * v, x * y * v - z * si, x * z * v + y * si],
                     [y * x * v + z * si, c + y * y * v, y * z * v - x * si],
                     [z * x * v - y * si, z * y * v + x * si, c + z * z * v]])


def axis_angle(Rm):
    """The single rotation equivalent to a (nearly) orthonormal matrix."""
    U, _, Vt = np.linalg.svd(Rm)
    R_ = U @ Vt
    angle = math.acos(float(np.clip((np.trace(R_) - 1.0) * 0.5, -1.0, 1.0)))
    axis = np.array([R_[2, 1] - R_[1, 2], R_[0, 2] - R_[2, 0], R_[1, 0] - R_[0, 1]], np.float64)
    ln = float(np.linalg.norm(axis))
    if ln < 1e-8:                     # 0 or 180 degrees: any eigenvector of +1 serves
        w, v = np.linalg.eigh((R_ + R_.T) * 0.5)
        return np.real(v[:, int(np.argmax(w))]), angle
    return axis / ln, angle


def kd_labels(pts, k):
    """Spatially coherent equal-size patches; label order is a DFS walk, so
    consecutive patches are neighbours."""
    labels = np.zeros(len(pts), np.int32)
    if k <= 1 or len(pts) == 0:
        return labels
    next_label = [0]

    def split(idx, kk):
        if kk <= 1 or len(idx) <= 1:
            labels[idx] = next_label[0]
            next_label[0] += 1
            return
        p = pts[idx]
        axis = int(np.argmax(p.max(0) - p.min(0)))
        k1 = kk // 2
        cut = max(1, len(idx) * k1 // kk)
        order = np.argpartition(p[:, axis], min(cut, len(idx) - 1))
        split(idx[order[:cut]], k1)
        split(idx[order[cut:]], kk - k1)

    split(np.arange(len(pts)), int(min(k, len(pts))))
    return labels


def weld(p, eps):
    key = np.round(p / eps).astype(np.int64)
    _, inv = np.unique(key, axis=0, return_inverse=True)
    return inv.reshape(-1), int(inv.max()) + 1


def laplace(p, tris, iters, lam=0.62):
    """Heavy smoothing: gives the blockout shape that detail emerges from."""
    n = len(p)
    e = np.concatenate([tris[:, [0, 1]], tris[:, [1, 2]], tris[:, [2, 0]]])
    e = np.concatenate([e, e[:, ::-1]])
    deg = np.bincount(e[:, 0], minlength=n).astype(np.float64)
    deg[deg == 0] = 1.0
    x = p.astype(np.float64).copy()
    for _ in range(iters):
        avg = np.stack([np.bincount(e[:, 0], weights=x[e[:, 1], c], minlength=n) for c in range(3)], 1)
        avg /= deg[:, None]
        x += (avg - x) * lam
    return x


def _weighted_quantile(x, w, q):
    order = np.argsort(x)
    cum = np.cumsum(w[order])
    return float(x[order][min(int(np.searchsorted(cum, q * cum[-1])), len(x) - 1)])


def flat_slabs(boxes):
    """Thin in one direction only: a slab. Thin in two is a post or a tower, and belongs to the subject."""
    ordered = np.sort(boxes[:, 1] - boxes[:, 0], axis=1)
    return (ordered[:, 0] < 0.08 * ordered[:, 2]) & (ordered[:, 1] > 0.3 * ordered[:, 2])


def find_subject(boxes, area, camera=None, verts=None):
    """Which objects are the thing being shown, as opposed to what surrounds it.

    Generated scenes often sit on kilometre-wide ground slabs, with a band of trees
    and distant terrain around them; framing all that leaves the subject a speck.
    boxes: (n, 2, 3) world bounds; area: surface area of each object."""
    n = len(boxes)
    keep = np.ones(n, bool)
    size = boxes[:, 1] - boxes[:, 0]
    diag = np.linalg.norm(size, axis=1)
    centre = boxes.mean(1)
    flat = flat_slabs(boxes)

    # 1. slabs and domes far larger than the region where objects actually are
    if n >= 8:
        spread = float(np.linalg.norm(np.quantile(centre, 0.98, axis=0) - np.quantile(centre, 0.02, axis=0)))
        spread = max(spread, float(np.median(diag)) * 2.0, 1e-9)
        giant = (diag > 2.5 * spread) & flat | (diag > 6.0 * spread)
        if not giant.all():
            keep &= ~giant

    # 2. a lone slab or backdrop among a few objects
    for _ in range(4):
        if keep.sum() < 3:
            break
        idx = np.where(keep)[0]
        big = idx[int(np.argmax(diag[idx]))]
        rest = keep.copy()
        rest[big] = False
        others = float(np.linalg.norm(boxes[rest, 1].max(0) - boxes[rest, 0].min(0)))
        if (flat[big] and diag[big] > 1.8 * others) or diag[big] > 6.0 * others:     # a floor, or a dome around it all
            keep[big] = False
        else:
            break

    # 3. what the file's own camera is composed around, if it has one worth trusting
    if camera is not None and keep.sum() >= 8:
        seen = framed_by(camera, centre, area, keep)
        if seen is not None:
            keep &= seen

    # 4. the built mass: large plain surfaces (a slab, a wall) outweigh dense organic detail
    # (leaves, grass), which is what separates a building from the garden around it. Only
    # the ground plan is narrowed, so that towers and chimneys are never cut off.
    if verts is not None and keep.sum() >= 40:
        idx = np.where(keep)[0]
        w = np.maximum(area[idx] ** 2 / np.maximum(verts[idx], 1.0), 1e-30)
        lo = np.array([_weighted_quantile(boxes[idx, 0, c], w, 0.03) for c in range(2)])
        hi = np.array([_weighted_quantile(boxes[idx, 1, c], w, 0.97) for c in range(2)])
        reach = (hi - lo) * 0.12
        inside = ((centre[idx, :2] >= lo - reach) & (centre[idx, :2] <= hi + reach)).all(1)
        if inside.any():
            keep[idx[~inside]] = False
    return keep


def framed_by(camera, centre, area, keep):
    """The objects inside a scene camera's view and not far behind its focus, or None when
    the camera sees too little to say what the subject is (a close-up, an interior)."""
    if camera.get("ortho"):
        return None
    M = np.array(camera["matrix"], np.float64)
    axes = M[:3, :3] / np.maximum(np.linalg.norm(M[:3, :3], axis=0), 1e-12)
    rel = centre - M[:3, 3]
    x, y, z = rel @ axes[:, 0], rel @ axes[:, 1], rel @ -axes[:, 2]
    tanx = float(camera["sensor"]) * 0.5 / max(float(camera["lens"]), 1e-6)
    view = (z > 0) & (np.abs(x) <= 1.3 * tanx * z) & (np.abs(y) <= 1.3 * tanx * z)
    count = view & keep
    if not count.any() or area[count].sum() < 0.35 * area[keep].sum():
        return None
    # a composed shot has its subject in the middle, so that is where the focus is measured
    middle = count & (np.abs(x) <= 0.45 * tanx * z) & (np.abs(y) <= 0.45 * tanx * z)
    middle = middle if middle.any() else count
    # weighted by how large things look from the camera: the near subject, not the forest behind it
    apparent = np.maximum(area[middle], 1e-12) / np.maximum(z[middle], 1e-9) ** 2
    return view & (z <= 1.4 * _weighted_quantile(z[middle], apparent, 0.5))


def framing(d: SceneData):
    """The subject's objects and bounds, and a sample of its vertices to fit cameras to."""
    boxes = np.array([p["bbox"] for p in d.parts], np.float64)
    P, T = np.asarray(d.P, np.float64), np.asarray(d.T)
    tri = 0.5 * np.linalg.norm(np.cross(P[T[:, 1]] - P[T[:, 0]], P[T[:, 2]] - P[T[:, 0]]), axis=1)
    verts = np.array([p["nv"] for p in d.parts], np.float64)
    keep = find_subject(boxes, np.bincount(d.tpart, weights=tri, minlength=d.npart), d.doc["camera"], verts)
    lo, hi = boxes[keep, 0].min(0), boxes[keep, 1].max(0)
    rng = np.random.default_rng(1)
    sample = []
    for i in np.where(keep)[0]:
        p = d.parts[i]
        n = min(p["nv"], 800)
        sel = rng.choice(p["nv"], n, replace=False) + p["v0"]
        sample.append(np.asarray(d.P[np.sort(sel)], np.float64))
    return keep, lo, hi, np.concatenate(sample)


def fit_camera(sample, az, el, tanx, tany, margin=0.1):
    """Exact fit: smallest distance at which every sample point is on-frame."""
    ca, sa, ce, se = math.cos(az), math.sin(az), math.cos(el), math.sin(el)
    back = np.array([ca * ce, sa * ce, se])
    f = -back
    r = np.cross(f, [0, 0, 1.0])
    r /= np.linalg.norm(r)
    u = np.cross(r, f)
    target = (sample.min(0) + sample.max(0)) * 0.5
    for _ in range(4):
        q = sample - target
        cx, cy, cz = q @ r, q @ u, q @ f
        tx, ty = tanx * (1 - margin), tany * (1 - margin)
        dist = max(float(np.max(np.abs(cx) / tx + cz)), float(np.max(np.abs(cy) / ty + cz)))
        pz = cz + dist
        px, py = cx / np.maximum(pz, 1e-6), cy / np.maximum(pz, 1e-6)
        target = target + r * ((px.max() + px.min()) * 0.5 * dist) + u * ((py.max() + py.min()) * 0.5 * dist)
    return target + back * dist, target, dist


class CubicPath:
    """Catmull-Rom through keyed values; flat tangents at both ends."""

    def __init__(self, times, values):
        self.t = np.asarray(times, np.float64)
        self.v = np.asarray(values, np.float64)
        n = len(self.t)
        self.m = np.zeros_like(self.v)
        for i in range(1, n - 1):
            self.m[i] = (self.v[i + 1] - self.v[i - 1]) / max(self.t[i + 1] - self.t[i - 1], 1e-6)

    def __call__(self, t):
        t = float(np.clip(t, self.t[0], self.t[-1]))
        i = int(np.clip(np.searchsorted(self.t, t) - 1, 0, len(self.t) - 2))
        h = self.t[i + 1] - self.t[i]
        s = (t - self.t[i]) / max(h, 1e-6)
        s2, s3 = s * s, s * s * s
        return ((2 * s3 - 3 * s2 + 1) * self.v[i] + (s3 - 2 * s2 + s) * h * self.m[i]
                + (-2 * s3 + 3 * s2) * self.v[i + 1] + (s3 - s2) * h * self.m[i + 1])


@dataclass
class Plan:
    settings: Settings = None
    T: dict = field(default_factory=dict)
    duration: float = 0.0


def fmt_num(x):
    return "%.3f" % x


class Builder:
    def __init__(self, d: SceneData, s: Settings):
        self.d, self.s = d, s
        self.rng = np.random.default_rng(s.seed)
        self.ops = []
        self.events = None

    # ------------------------------------------------------------------ parts
    def prep_parts(self):
        """Measures every object: what it started as, and what each patch of it undergoes."""
        d, rng = self.d, self.rng
        P = np.asarray(d.P)
        T = np.asarray(d.T)
        PS = P.copy()
        SRC = P.copy()
        PA = np.zeros(d.nv, np.int32)
        PB = np.zeros(d.nv, np.int32)
        VPAINT = np.zeros(d.nv, np.float32)
        # sizes are relative to the subject: a kilometre of lawn must not make the house "small"
        self.frame = framing(d)
        self.D = max(float(np.linalg.norm(self.frame[2] - self.frame[1])), 1e-9)
        self.step_log = step_log.by_object(self.s.step_log)

        def volume(i):
            p = d.parts[i]
            s_ = np.maximum(np.array(p["bbox"][1]) - np.array(p["bbox"][0]), 1e-6)
            return float(np.prod(s_ + 0.05 * np.linalg.norm(s_)))

        # the ground goes down first, then the subject, and what surrounds it last
        keep = self.frame[0]
        slabs = flat_slabs(np.array([p["bbox"] for p in d.parts], np.float64))
        by_size = sorted(range(d.npart), key=lambda i: (0 if slabs[i] and not keep[i] else 1 if keep[i] else 2, -volume(i)))
        # a log sets the order objects start in; what it does not mention follows by size
        first_seen = {name: n for n, name in enumerate(self.step_log)}
        self.order = sorted(by_size, key=lambda i: first_seen.get(d.parts[i]["name"], len(first_seen)))
        meta = [None] * d.npart
        originals = {}
        for i in self.order:
            p = d.parts[i]
            v0, nv = p["v0"], p["nv"]
            pf = P[v0:v0 + nv]
            size = np.maximum(pf.max(0) - pf.min(0), 1e-6)
            diag = float(np.linalg.norm(size))

            M = np.array(p["matrix"], np.float64)
            scale = np.linalg.norm(M[:3, :3], axis=0)
            Rm = M[:3, :3] / np.maximum(scale, 1e-9)
            euler = (math.atan2(Rm[2, 1], Rm[2, 2]),
                     math.atan2(-Rm[2, 0], math.hypot(Rm[0, 0], Rm[1, 0])),
                     math.atan2(Rm[1, 0], Rm[0, 0]))
            axis, angle = axis_angle(Rm)

            inv, nw = weld(pf, max(diag * 1e-5, 1e-7))
            wc = np.zeros((nw, 3), np.float64)
            for c in range(3):
                wc[:, c] = np.bincount(inv, weights=pf[:, c], minlength=nw) / np.maximum(np.bincount(inv, minlength=nw), 1)
            wt = inv[np.asarray(T[p["t0"]:p["t0"] + p["nt"]]) - v0]
            to_local = np.linalg.pinv(M[:3, :3])
            local = (wc - M[:3, 3]) @ to_local.T

            key = forensics.shape_key(local, wt)
            dup = originals.setdefault(key, i)
            dup = None if dup == i else dup
            fit = forensics.fit_primitive(local, p["npoly"], p["type"], (p.get("data_name"), p["name"]), p["nt"])
            src_w = fit.source @ M[:3, :3].T + M[:3, 3]

            frozen = fit.exact or dup is not None
            has_mods = bool(p["mods"])
            if frozen or has_mods:
                tier, ka, kb = 0, 1, 0
            elif nv <= 3000:
                tier, ka, kb = 1, int(np.clip(nv / 7, 3, 110)), 0
            else:
                tier, ka, kb = 2, int(np.clip(math.sqrt(nv) / 3.2, 16, 90)), int(np.clip(nv / 55, 40, 4800))
            if not frozen:
                SRC[v0:v0 + nv] = src_w[inv].astype(np.float32)

            la = kd_labels(wc, ka)
            PA[v0:v0 + nv] = la[inv]
            ka = int(la.max()) + 1
            pa_centers = np.stack([np.bincount(la, weights=wc[:, c]) for c in range(3)], 1)
            pa_centers /= np.bincount(la)[:, None]
            ps = SRC[v0:v0 + nv]
            box_c = np.stack([np.bincount(PA[v0:v0 + nv], weights=ps[:, c], minlength=ka) for c in range(3)], 1)
            box_c /= np.maximum(np.bincount(PA[v0:v0 + nv], minlength=ka), 1)[:, None]

            pb_centers = None
            mid = wc
            lb = None
            if kb:
                mid = laplace(wc, wt, 46)
                PS[v0:v0 + nv] = mid[inv].astype(np.float32)
                lb = kd_labels(wc, kb)
                PB[v0:v0 + nv] = lb[inv]
                kb = int(lb.max()) + 1
                pb_centers = np.stack([np.bincount(lb, weights=wc[:, c]) for c in range(3)], 1)
                pb_centers /= np.bincount(lb)[:, None]

            # the names and the sizes of the work, from the motion the film really plays
            ops_a, ops_b, L_a, L_b, L_mod = [], [], np.zeros(ka), np.zeros(max(kb, 1)), 0.0
            if tier:
                fa = forensics.measure(src_w, mid, wt, la, ka)
                ops_a = [forensics.name_edit(fa, k, diag) for k in range(ka)]
                # blocking out a form is one large change spread over many patches, so its
                # size is that of the whole; the patches differ only by their share of it
                L_a = float(np.cbrt(fa["swept"].sum())) * np.cbrt(fa["swept"] / max(float(fa["swept"].max()), 1e-30)) / self.D
                # a chamfer can sit exactly on the starting shape, so no motion reveals it
                whole = forensics.measure(src_w, wc, wt, np.zeros(nw, np.int64), 1)
                if whole["bevel"][0] > 0.35 and not any(e and e["label"] == "Bevel" for e in ops_a):
                    width = float(whole["width"][0])
                    fit.extras.append(("Bevel", "mesh.bevel(offset=%.4g)" % width, "Width", width))
                if kb:
                    fb = forensics.measure(mid, wc, wt, lb, kb, shrink_free=True)
                    ops_b = [forensics.name_sculpt(fb, k, diag) for k in range(kb)]
                    L_b = np.cbrt(fb["swept"]) / self.D
            elif has_mods and not frozen:
                L_mod = float(np.cbrt(forensics.measure(src_w, wc, wt, np.zeros(nw, np.int64), 1)["swept"][0])) / self.D

            nseeds = 1 if tier == 0 else (2 if tier == 1 else int(np.clip(math.sqrt(nv) / 40, 4, 12)))
            seeds = wc[rng.choice(nw, min(nseeds, nw), replace=False)]
            dmin = np.min(np.linalg.norm(wc[:, None, :] - seeds[None, :, :], axis=2), axis=1)
            VPAINT[v0:v0 + nv] = (dmin / max(float(dmin.max()), 1e-9) * 0.94)[inv].astype(np.float32)

            center = (pf.min(0) + pf.max(0)) * 0.5
            meta[i] = dict(
                i=i, tier=tier, ka=ka, kb=kb, diag=diag, size=size, center=center,
                pa_centers=pa_centers, box_c=box_c, pb_centers=pb_centers,
                seeds=seeds, nseeds=len(seeds), spread=float(dmin.max()), loc=np.array(p["loc"]),
                euler=np.degrees(euler), scale=scale, axis=axis, angle=angle, smooth=p["smooth"] > 0.5,
                fit=fit, dup=dup, ops_a=ops_a, ops_b=ops_b, L_a=L_a, L_b=L_b, L_mod=L_mod,
                L=float(np.cbrt(np.prod(size + 0.05 * diag))) / self.D,
            )
        self.PS, self.PA, self.PB, self.VPAINT, self.SRC = PS.astype(np.float32), PA, PB, VPAINT, SRC.astype(np.float32)
        self.meta = meta

    # ------------------------------------------------------------- scheduling
    def op(self, kind, part, dur, label, api, icon=None, stage=None, patch=None, anchor=None, chip=None, impact=0.0):
        o = dict(kind=kind, part=part, dur=float(dur), label=label, api=api,
                 icon=icon or KIND_ICON.get(kind, "arrow"), stage=stage, patch=patch,
                 anchor=anchor, chip=chip, t0=0.0, t1=0.0, cursor=-1, impact=float(impact), boost=1.0)
        self.ops.append(o)
        return o

    def schedule(self, seq_groups, n_mice):
        """seq_groups: list of (op-list, parallel?) chains per part. Greedy
        list scheduling onto the earliest-free mouse."""
        rng = self.rng
        heap = [(float(rng.uniform(0, 0.3)), c) for c in range(n_mice)]
        heapq.heapify(heap)
        makespan = 0.0

        def assign(op, earliest):
            nonlocal makespan
            free, c = heapq.heappop(heap)
            start = max(free + float(rng.uniform(0.12, 0.26)) / op["boost"], earliest)
            op["t0"], op["t1"], op["cursor"] = start, start + op["dur"] / op["boost"], c
            makespan = max(makespan, op["t1"])
            heapq.heappush(heap, (op["t1"], c))
            return op["t1"]

        for chain in seq_groups:
            earliest = 0.0
            for step in chain:
                if isinstance(step, list):
                    finish = earliest
                    for op in step:
                        finish = max(finish, assign(op, earliest))
                    earliest = finish
                else:
                    earliest = assign(step, earliest)
        return makespan

    def waves(self, group, n_parts):
        """Caps how many mice one object can hold at once, so that the swarm
        spreads over the scene instead of dog-piling the largest part."""
        per = max(6, int(math.ceil(self.mice_hint / max(1, min(n_parts, 8)))))
        return [group[i:i + per] for i in range(0, len(group), per)]

    # ---- one object's work: pieces shared by inferred and logged sequences
    def add_op(self, i, step=None):
        m, p = self.meta[i], self.d.parts[i]
        if (step and step["op"] == "duplicate") or (not step and m["dup"] is not None):
            original = m["dup"]
            linked = p["instance"] or (original is not None and p.get("data_name")
                                       and self.d.parts[original].get("data_name") == p.get("data_name"))
            label, api = ("Duplicate Linked", "object.duplicate_move_linked()") if linked else ("Duplicate Objects", "object.duplicate_move()")
            anchor = ("ui", "outliner")
        else:
            label, api, anchor = m["fit"].label, m["fit"].api, ("ui", "menu.add")
            if step and step.get("primitive"):
                label = step_log.PRIMITIVES[step["primitive"]]
        if step:
            label, api = step.get("label", label), step.get("call", api)
        return self.op("add", i, 0.26, label, api, anchor=anchor, impact=m["L"])

    def needs_move(self, m):
        return float(np.linalg.norm(m["loc"])) > 1e-4 * self.D

    def move_op(self, i, step=None):
        m = self.meta[i]
        api = "transform.translate(value=(%.3f, %.3f, %.3f))" % tuple(m["loc"])
        return self.op("grab", i, float(self.rng.uniform(0.28, 0.42)), (step or {}).get("label", "Move"), (step or {}).get("call", api),
                       anchor=("pivot", i), chip=("Location", m["loc"]), impact=m["L"])

    def needs_rotate(self, m):
        return m["angle"] > math.radians(0.5)

    def rotate_op(self, i, step=None):
        m = self.meta[i]
        a = int(np.argmax(np.abs(m["euler"])))
        api = "transform.rotate(value=%.3f, orient_axis='%s')" % (math.radians(m["euler"][a]), "XYZ"[a])
        return self.op("rot", i, float(self.rng.uniform(0.2, 0.3)), (step or {}).get("label", "Rotate"), (step or {}).get("call", api),
                       anchor=("pivot", i), chip=("Rotation", m["euler"]), impact=m["L"])

    def needs_scale(self, m):
        return float(np.abs(m["scale"] - 1.0).max()) > 1e-3

    def scale_op(self, i, step=None):
        m = self.meta[i]
        api = "transform.resize(value=(%.3f, %.3f, %.3f))" % tuple(m["scale"])
        return self.op("scale", i, float(self.rng.uniform(0.2, 0.3)), (step or {}).get("label", "Resize"), (step or {}).get("call", api),
                       anchor=("pivot", i), chip=("Scale", m["scale"]), impact=m["L"])

    def mod_op(self, i, mod, step=None):
        api = "object.modifier_add(type='%s')" % mod["type"]
        return self.op("mod", i, 0.3, (step or {}).get("label", "Add Modifier: %s" % mod["name"]), (step or {}).get("call", api),
                       anchor=("ui", "props.mods"), chip=("Modifier", mod["name"]), impact=self.meta[i]["L_mod"])

    def bare_op(self, i, step, label, api, icon, value_name):
        """A recorded step with nothing to play on the mesh: a cut, or one the geometry does not show."""
        chip = (step.get("value_name", value_name), step["value"]) if "value" in step else None
        return self.op("cut", i, float(self.rng.uniform(0.18, 0.3)), label, step.get("call", api), icon, anchor=("pivot", i), chip=chip)

    def patches(self, i):
        """Every patch of the object that really changes shape: (stage, index, inferred op, size, centre)."""
        m = self.meta[i]
        out = [("A", k, e, float(m["L_a"][k]), m["pa_centers"][k]) for k, e in enumerate(m["ops_a"]) if e]
        out += [("B", k, e, float(m["L_b"][k]), m["pb_centers"][k]) for k, e in enumerate(m["ops_b"]) if e]
        return out

    def patch_op(self, i, patch, step=None):
        stage, k, e, impact, _ = patch
        label, api, icon, chip = e["label"], e["api"], e["icon"], e["chip"]
        if step:
            op = step["op"]
            if op == step_log.SCULPT:
                label = step.get("label", step.get("brush", label))
                icon = "brush"
            else:
                default, call, icon, name = step_log.EDIT_OPS[op]
                label, api = step.get("label", default), call or api
                chip = (step.get("value_name", name), chip[1])
            api = step.get("call", api)
            if "value" in step:
                chip = (step.get("value_name", chip[0]), step["value"])
        lo, hi = (0.22, 0.42) if stage == "A" else (0.2, 0.34)
        return self.op("edit", i, float(self.rng.uniform(lo, hi)), label, api, icon, stage=stage, patch=k,
                       anchor=("patch", i, stage, k), chip=chip, impact=impact)

    def inferred_chain(self, i):
        m, p = self.meta[i], self.d.parts[i]
        chain = [self.add_op(i)]
        if m["dup"] is None:
            for label, api, name, count in m["fit"].extras:
                chain.append(self.op("cut", i, float(self.rng.uniform(0.18, 0.3)), label, api, anchor=("pivot", i), chip=(name, count)))
        if self.needs_move(m):
            chain.append(self.move_op(i))
        if self.needs_rotate(m):
            chain.append(self.rotate_op(i))
        if self.needs_scale(m):
            chain.append(self.scale_op(i))
        ps = self.patches(i)
        for stage in "AB":
            chain.extend(self.waves([self.patch_op(i, q) for q in ps if q[0] == stage], self.d.npart))
        chain.extend(self.mod_op(i, mod) for mod in p["mods"])
        return chain

    def logged_chain(self, i, steps):
        """The sequence the step log records. Whatever it leaves out still comes from the geometry."""
        m, p = self.meta[i], self.d.parts[i]
        edit_steps = [st for st in steps if st["op"] in step_log.EDIT_OPS or st["op"] == step_log.SCULPT]
        ps = self.patches(i)
        points = [("sculpt" if q[0] == "B" else step_log.FAMILY.get(q[2]["label"], "edit"), [float(x) for x in q[4]]) for q in ps]
        buckets, left = step_log.split_patches(points, edit_steps)
        by_step = {id(st): b for st, b in zip(edit_steps, buckets)}

        creation = next((st for st in steps if st["op"] in step_log.CREATION), None)
        chain = [self.add_op(i, creation) if creation else self.add_op(i)]
        done, mods = set(), list(p["mods"])
        for st in steps:
            op = st["op"]
            if op in step_log.CREATION:
                continue
            if op in step_log.TRANSFORMS:
                done.add(op)
                real = {"move": self.needs_move, "rotate": self.needs_rotate, "scale": self.needs_scale}[op](m)
                make = {"move": self.move_op, "rotate": self.rotate_op, "scale": self.scale_op}[op]
                if real:
                    chain.append(make(i, st))
                else:
                    label, api = step_log.TRANSFORMS[op]
                    chain.append(self.bare_op(i, st, st.get("label", label), api, KIND_ICON["grab"], "Value"))
            elif op == "modifier":
                name = st.get("modifier", "")
                mod = next((x for x in mods if name.lower() in (x["name"].lower(), x["type"].lower())), None)
                if mod is not None:
                    mods.remove(mod)
                    chain.append(self.mod_op(i, mod, st))
                else:
                    chain.append(self.bare_op(i, st, st.get("label", "Add Modifier: %s" % (name or "?")), "object.modifier_add()", "pointer", "Value"))
            else:
                group = [self.patch_op(i, ps[n], st) for n in by_step[id(st)]]
                if group:
                    chain.extend(self.waves(group, self.d.npart))
                elif op == step_log.SCULPT:
                    chain.append(self.bare_op(i, st, st.get("label", st.get("brush", "Sculpt")), "sculpt.brush_stroke()", "brush", "Strength"))
                else:
                    label, call, icon, name = step_log.EDIT_OPS[op]
                    chain.append(self.bare_op(i, st, st.get("label", label), call, icon, name))

        # the file says something the log does not mention: it still has to happen on screen
        missing = [(self.needs_move, self.move_op, "move"), (self.needs_rotate, self.rotate_op, "rotate"), (self.needs_scale, self.scale_op, "scale")]
        fill = [make(i) for need, make, name in missing if name not in done and need(m)]
        chain[1:1] = fill
        for stage in "AB":
            chain.extend(self.waves([self.patch_op(i, ps[n]) for n in left if ps[n][0] == stage], self.d.npart))
        chain.extend(self.mod_op(i, mod) for mod in mods)
        return chain

    def model_ops(self):
        return [self.logged_chain(i, self.step_log[self.d.parts[i]["name"]]) if self.d.parts[i]["name"] in self.step_log
                else self.inferred_chain(i) for i in self.order]

    def accelerate(self, ops):
        """Applies the timing rule: only work that visibly reshapes the model plays at the film's speed."""
        boosts = op_boost([o["impact"] for o in ops], self.major, float(self.s.opt["pace.tweak_speed"]))
        for o, b in zip(ops, boosts):
            o["boost"] = float(b)

    def run(self):
        s = self.s
        self.prep_parts()
        state = self.rng.bit_generator.state
        if s.duration > 0:
            # measure the work at speed 1, then replan at the speed that fits
            self.choreograph(1.0)
            model, shade = self.model_end, self.shade_end - self.model_end
            s.speed = solve_speed(model, shade, max(s.duration - s.showcase, 1.0),
                                  s.opt["pace.lighting"], s.opt["pace.render"])
            self.ops = []
            self.rng.bit_generator.state = state
        self.choreograph(max(s.speed, SPEED_RANGE[0]))

    def choreograph(self, speed):
        s = self.s
        total = sum(1 + m["ka"] + m["kb"] for m in self.meta)
        self.mice_hint = s.mice if s.mice > 0 else int(np.clip(total * 0.55 / 18.0, 72, 200))
        chains = self.model_ops()
        n_ops = len(self.ops)
        # ---------------- how long, how many mice
        weight = s.opt["pace.model_weight"]
        t_model = float(np.clip(10 + 0.35 * math.sqrt(n_ops), 12, 42)) * weight
        # target makespan in work units
        avg = np.mean([o["dur"] for o in self.ops]) + 0.2
        mice = s.mice if s.mice > 0 else int(np.clip(n_ops * avg / (t_model / 1.2), 72, 200))
        self.n_mice = mice
        # the pace is set with every operation at the film's speed, so a major one lasts as long as
        # it always did; the small ones then close up around it
        span = self.schedule(chains, mice)
        scale = min(t_model / max(span, 1e-3), 1.4 * weight)
        sizes = np.array([o["impact"] for o in self.ops if o["impact"] > 0.0])
        major = float(s.opt["pace.major_size"])
        # however delicate the model is, its largest work still gets to be seen
        self.major = min(major, float(sizes.max()) * 0.9) if len(sizes) else major
        self.accelerate(self.ops)
        span = self.schedule(chains, mice)
        t_model = span * scale
        sc = scale / speed
        for o in self.ops:
            o["t0"] *= sc
            o["t1"] *= sc
        T_model = t_model / speed
        for o in self.ops:
            o["phase"] = "model"
        self.model_end = T_model

        self.shade_ops(T_model, speed)

    # ----------------------------------------------------------------- shading
    def shade_ops(self, T_model, speed):
        d, rng = self.d, self.rng
        n_used = len(d.parts)
        mats_used = {}
        for i, p in enumerate(d.parts):
            for m in p["mats"]:
                if m >= 0:
                    mats_used.setdefault(m, []).append(i)
        weight = self.s.opt["pace.shade_weight"]
        t_shade = float(np.clip(3.5 + 0.32 * math.sqrt(n_used + len(mats_used) * 8), 4, 14)) * weight
        self.ops_shade = []
        if not self.s.opt["pace.shading"]:
            # no crew for this phase: the materials dissolve in on their own
            self.shade_end = T_model + 0.7 / speed
            self.mat_order, self.mats_used, self.mat_windows = [], mats_used, []
            return

        chains = []
        mat_order = sorted(mats_used, key=lambda m: -len(mats_used[m]))
        before = len(self.ops)
        for m in mat_order:
            M = d.materials[m]
            q = json.dumps(M["name"])
            chains.append([self.op("tweak", -1, 0.3, "Roughness", "bpy.data.materials[%s].roughness = %.3f" % (q, M["rough"]),
                                   anchor=("ui", "props.rough"), chip=("Roughness", M["rough"]))])
            chains.append([self.op("tweak", -1, 0.3, "Metallic", "bpy.data.materials[%s].metallic = %.3f" % (q, M["metal"]),
                                   anchor=("ui", "props.metal"), chip=("Metallic", M["metal"]))])
            hex_ = "#%02x%02x%02x" % tuple(int(round(255 * min(max(c, 0.0), 1.0) ** (1 / 2.2))) for c in M["color"][:3])
            chains.append([self.op("pick", -1, 0.3, "Base Color", "bpy.data.materials[%s].diffuse_color = %s" % (q, hex_),
                                   anchor=("ui", "props.basecolor"), chip=("Color", hex_))])
        for i in self.order:
            p, m = d.parts[i], self.meta[i]
            for mat in p["mats"][:4]:
                if mat < 0:
                    continue
                M = d.materials[mat]
                # brushwork is only claimed where the file really has a painted texture or vertex colours
                painted = m["nseeds"] > 2 and (M.get("tex") or M.get("vcol"))
                if painted:
                    label, api = ("Texture Paint", "paint.image_paint()") if M.get("tex") else ("Vertex Paint", "paint.vertex_paint()")
                    chains.append([[self.op("paint", i, 0.95, label, api, anchor=("seed", i, k),
                                            chip=("Radius", m["spread"]), impact=m["L"]) for k in range(m["nseeds"])]])
                else:
                    chains.append([self.op("assign", i, float(rng.uniform(0.3, 0.5)), "Assign %s" % M["name"],
                                           "object.material_slot_assign()", anchor=("seed", i, 0), chip=("Slot", M["name"]), impact=m["L"])])
        # the node editor gets its own crew
        for m in mat_order:
            g = d.materials[m].get("graph")
            if not g:
                continue
            for n_ in g["nodes"][:8]:
                chains.append([self.op("node", -1, float(rng.uniform(0.22, 0.4)), "Add Node: %s" % n_["label"],
                                       "node.add_node(type=\"%s\")" % n_["id"],
                                       anchor=("ui", "nodes"), chip=("Node", n_["label"]))])
        order_ = rng.permutation(len(chains))
        chains = [chains[int(k)] for k in order_]
        new_ops = self.ops[before:]
        span = self.schedule(chains, self.n_mice)
        scale = min(t_shade / max(span, 1e-3), 1.6 * weight)
        self.accelerate(new_ops)
        span = self.schedule(chains, self.n_mice)
        t_shade = span * scale / speed
        sc = scale / speed
        for o in new_ops:
            o["t0"] = o["t0"] * sc + T_model
            o["t1"] = o["t1"] * sc + T_model
            o["phase"] = "shade"
        self.shade_end = T_model + t_shade
        self.mat_order = mat_order
        self.mats_used = mats_used

        # material spotlight windows for the node editor
        t0, t1 = T_model, self.shade_end
        wn = max(1, len(mat_order))
        self.mat_windows = [(t0 + (t1 - t0) * j / wn, t0 + (t1 - t0) * (j + 1) / wn, m)
                            for j, m in enumerate(mat_order)]

    # ---------------------------------------------------- everything after
    def lighting_render(self, speed):
        t_light = light_time(speed, self.s.opt["pace.lighting"])
        t_render = render_time(speed, self.s.opt["pace.render"])
        self.t_light0 = self.shade_end
        self.t_light1 = self.shade_end + t_light
        self.t_render0 = self.t_light1
        self.t_render1 = self.t_render0 + t_render


def build_plan(d: SceneData, s: Settings):
    from .plan_finish import finish
    b = Builder(d, s)
    b.run()
    b.lighting_render(max(s.speed, 0.05))
    return finish(b), (b.PS, b.PA, b.PB, b.VPAINT, b.SRC)
