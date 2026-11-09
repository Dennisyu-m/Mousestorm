# SPDX-License-Identifier: GPL-3.0-or-later
"""Renders one output frame as a pure function of (plan, t)."""
import os
import pickle

import numpy as np
import skia
from PIL import Image

from . import raster as rs
from . import ui as U
from .grade import Finisher
from .plan import ease_io, ease_out_back, rot_axis
from .plan_finish import cam_at, fx_at
from .scene import SceneData, Viewport
from .showcase import Showcase
from .ui import Painter, R, alpha, paint, rr, text
from .ui_layout import layout_at


def load_rgb(path, w, h):
    im = Image.open(path).convert("RGBA")
    if im.size != (w, h):
        im = im.resize((w, h), Image.LANCZOS)
    return np.asarray(im, np.uint8)


class FrameRenderer:
    def __init__(self, work):
        self.work = work
        self.data = SceneData(work)
        with open(os.path.join(work, "plan.pkl"), "rb") as f:
            self.pl = pickle.load(f)
        s = self.pl.settings
        self.W, self.H = s.width, s.height
        self.scale = self.W / self.pl.ui_w
        self.vp = Viewport(self.data, s.opt["look.theme"])
        d = self.data
        self.SRC = np.load(os.path.join(work, "SRC.npy"), mmap_mode="r")
        self.PS = np.load(os.path.join(work, "PS.npy"), mmap_mode="r")
        self.PA = np.load(os.path.join(work, "PA.npy"), mmap_mode="r")
        self.PB = np.load(os.path.join(work, "PB.npy"), mmap_mode="r")
        self.vp.VPAINT[:] = np.load(os.path.join(work, "VPAINT.npy"))
        self.dirty = np.zeros(d.npart, bool)
        self.painter = Painter(self.pl, d)
        self.show = Showcase(self.pl, d, work, self.painter, self.W, self.H, self)
        self.finisher = Finisher(s.opt, self.W, self.H)
        self.rgba = np.zeros((self.H, self.W, 4), np.uint8)
        info = skia.ImageInfo.Make(self.W, self.H, skia.kRGBA_8888_ColorType, skia.kPremul_AlphaType)
        self.surface = skia.Surface.MakeRasterDirect(info, self.rgba)
        self.canvas = self.surface.getCanvas()
        self.hero = None
        self.hero_noisy = None
        self.stats_cache = {}

    # ------------------------------------------------------------ geometry
    def update_geometry(self, t):
        d, pl, vp = self.data, self.pl, self.vp
        parts = d.parts
        added = pl.part_add <= t
        active = np.where(added & (pl.part_settle > t))[0]
        restore = np.where(self.dirty & (~added | (pl.part_settle <= t)))[0]
        for i in restore:
            p = parts[i]
            v0, nv, t0, nt = p["v0"], p["nv"], p["t0"], p["nt"]
            vp.P[v0:v0 + nv] = d.P[v0:v0 + nv]
            vp.TN[t0:t0 + nt] = d.TN[t0:t0 + nt]
            vp.VSEL[v0:v0 + nv] = 0.0
            self.dirty[i] = False

        for i in active:
            i = int(i)
            p = parts[i]
            v0, nv, t0, nt = p["v0"], p["nv"], p["t0"], p["nt"]
            ofs0, ofs1 = int(pl.a_ofs[i]), int(pl.a_ofs[i + 1])
            ta0, ta1 = pl.a_t0[ofs0:ofs1], pl.a_t1[ofs0:ofs1]
            d_a = np.maximum(ta1 - ta0, 1e-5)
            uA = ease_out_back((t - ta0) / d_a)
            sA = np.clip(1.0 - np.abs((t - ta0) / d_a - 0.5) * 2.0 + 0.35, 0.0, 1.0) * (t >= ta0) * (t < ta1 + 0.18)
            has_b = bool(pl.has_b[i])
            if has_b:
                b0, b1 = int(pl.b_ofs[i]), int(pl.b_ofs[i + 1])
                tb0, tb1 = pl.b_t0[b0:b1], pl.b_t1[b0:b1]
                d_b = np.maximum(tb1 - tb0, 1e-5)
                uB = ease_out_back((t - tb0) / d_b)
                sB = np.clip(1.0 - np.abs((t - tb0) / d_b - 0.5) * 2.0 + 0.35, 0.0, 1.0) * (t >= tb0) * (t < tb1 + 0.18)
            else:
                uB = np.zeros(1, np.float64)
                sB = np.zeros(1, np.float64)
            # object-mode selection glow while being placed
            g0, s1 = pl.part_g0[i], pl.part_s1[i]
            base = 0.32 if g0 - 0.02 <= t <= s1 else 0.0
            if base > 0.0:
                sA = np.maximum(sA, base)
            rs.morph(vp.P, self.SRC, self.PS, d.P, v0, nv, self.PA, self.PB, uA.astype(np.float64),
                     uB.astype(np.float64), sA.astype(np.float64), sB.astype(np.float64), vp.VSEL, has_b)

            ug = float(ease_io((t - pl.part_g0[i]) / max(pl.part_g1[i] - pl.part_g0[i], 1e-5)))
            pivot1 = pl.pivot_spawn[i] + (pl.pivot_final[i] - pl.pivot_spawn[i]) * ug
            sc_from = float(pl.scale_from[i])
            us = float(ease_io((t - pl.part_s0[i]) / max(pl.part_s1[i] - pl.part_s0[i], 1e-5)))
            sc = sc_from + (1.0 - sc_from) * us
            pop = float(ease_out_back((t - pl.part_add[i]) / (0.16 / max(pl.settings.speed, 0.4))))
            sc *= max(pop, 0.0)
            if pl.part_r0[i] >= 0.0:
                ur = float(ease_io((t - pl.part_r0[i]) / max(pl.part_r1[i] - pl.part_r0[i], 1e-5)))
                R3 = rot_axis(pl.spin_axis[i], -float(pl.spin[i]) * (1.0 - ur))
            else:
                R3 = np.eye(3)
            rs.apply_transform(vp.P, v0, nv, np.asarray(pl.pivot_final[i], np.float64),
                               np.asarray(pivot1, np.float64), R3, sc)
            rs.recompute_normals(vp.P, d.T, vp.TN, t0, nt, v0, nv, bool(pl.smooth[i]), vp.VN)
            self.dirty[i] = True

    def update_material_state(self, t):
        pl, vp = self.pl, self.vp
        w = np.zeros_like(vp.pwire)
        p = np.zeros_like(vp.ppaint)
        fade = 0.14 / max(pl.settings.speed, 0.5)
        w0, w1 = pl.wire0, pl.wire1
        mask = np.isfinite(w0)
        up = np.clip((t - (w0[mask] - fade)) / fade, 0, 1)
        dn = np.clip(((w1[mask] + fade * 2) - t) / (fade * 2), 0, 1)
        w[mask] = np.minimum(up, dn) * 0.82
        p0, p1 = pl.paint0, pl.paint1
        mask2 = np.isfinite(p0)
        u = (t - p0[mask2]) / np.maximum(p1[mask2] - p0[mask2], 1e-5)
        pv = np.where(u >= 1.0, 2.0, np.clip(u, 0.0, 1.0) * 1.08)
        p[mask2] = pv
        if t >= pl.T["render"][0]:
            p[mask2] = 2.0
        vp.pwire[:], vp.ppaint[:] = w, p

    # ---------------------------------------------------------------- misc
    def active_part(self, t):
        pl = self.pl
        q = int(np.searchsorted(pl.op_t0, t))
        k = q - 1
        while k >= 0 and pl.op_part[k] < 0:
            k -= 1
        return int(pl.op_part[k]) if k >= 0 else -1

    def xform_values(self, t, i):
        pl = self.pl
        if i < 0:
            return np.zeros(3), np.zeros(3), np.ones(3)
        ug = float(ease_io((t - pl.part_g0[i]) / max(pl.part_g1[i] - pl.part_g0[i], 1e-5)))
        p = self.data.parts[i]
        M = np.array(p["matrix"])
        loc0 = M[:3, 3] * ug
        S = np.linalg.norm(M[:3, :3], axis=0)
        us = float(ease_io((t - pl.part_s0[i]) / max(pl.part_s1[i] - pl.part_s0[i], 1e-5)))
        return loc0, np.zeros(3), 1.0 + (S - 1.0) * us

    def active_ops(self, t):
        pl = self.pl
        lo = max(0, int(np.searchsorted(pl.op_t0, t - 4.0 / max(pl.settings.speed, 0.25))))
        hi = int(np.searchsorted(pl.op_t0, t))
        idx = np.arange(lo, hi)
        return idx[pl.op_t1[idx] >= t]

    def vertex_dots(self, c, t, rects, cam, active):
        pl, vp, d = self.pl, self.vp, self.data
        vr = rects["view"]
        if vr[2] < 10:
            return
        r, u, f = cam.basis()
        tany = cam.tany
        tanx = tany * vr[2] / vr[3]
        count = 0
        dot = paint(U.ACCENT)
        ring = paint(alpha(skia.ColorBLACK, 180), style="s", sw=1.0)
        ss = pl.settings.ss
        bw, bh = max(16, int(vr[2] * self.scale)) * ss, max(16, int(vr[3] * self.scale)) * ss
        Zb = vp.buffers.get((bw, bh), (None,))[0]
        c.save()
        c.clipRect(R(vr[0], vr[1] + 26, vr[2], vr[3] - 26))
        for q in active:
            if pl.op_kind[q] != "edit":
                continue
            i = int(pl.op_part[q])
            p = d.parts[i]
            v0, nv = p["v0"], p["nv"]
            patch = pl.op_patch[q]
            field = self.PA if pl.op_stage[q] == "A" else self.PB
            ids = np.flatnonzero(field[v0:v0 + nv] == patch)
            if len(ids) == 0:
                continue
            step = max(1, len(ids) // 7)
            pts = vp.P[v0 + ids[::step][:8]]
            eye = cam.eye
            for pw in pts:
                qq = np.asarray(pw, np.float64) - eye
                z = float(qq @ f)
                if z < 1e-3:
                    continue
                sx = vr[0] + vr[2] * (0.5 + 0.5 * float(qq @ r) / (z * tanx))
                sy = vr[1] + vr[3] * (0.5 - 0.5 * float(qq @ u) / (z * tany))
                if Zb is not None:
                    px = int((sx - vr[0]) * self.scale * ss)
                    py = int((sy - vr[1]) * self.scale * ss)
                    if not (0 <= px < bw and 0 <= py < bh) or Zb[py, px] > (1.0 / z) * 1.012:
                        continue
                c.drawCircle(sx, sy, 2.3, dot)
                c.drawCircle(sx, sy, 2.3, ring)
            count += 1
            if count > 26:
                break
        c.restore()

    def gizmos(self, c, t, rects, cam, chrome=True):
        """3D cursor at the origin, light & camera gizmos during lighting."""
        pl = self.pl
        vr = rects["view"]
        if vr[2] < 100:
            return
        r, u, f = cam.basis()
        tany = cam.tany
        tanx = tany * vr[2] / vr[3]

        def proj(pw):
            q = np.asarray(pw, np.float64) - cam.eye
            z = float(q @ f)
            if z < 1e-3:
                return None
            return (vr[0] + vr[2] * (0.5 + 0.5 * float(q @ r) / (z * tanx)),
                    vr[1] + vr[3] * (0.5 - 0.5 * float(q @ u) / (z * tany)))

        c.save()
        c.clipRect(R(vr[0], vr[1] + 26, vr[2], vr[3] - 26))
        a = proj(pl.center * np.array([1, 1, 0]) + np.array([0, 0, pl.ground]))
        if a and chrome:
            c.drawCircle(a[0], a[1], 8, paint(alpha(skia.ColorWHITE, 210), style="s", sw=1.1))
            c.drawCircle(a[0], a[1], 8, paint(alpha(skia.Color(235, 70, 80), 240), style="s", sw=1.1))
            c.drawLine(a[0] - 13, a[1], a[0] - 5, a[1], paint(alpha(skia.ColorWHITE, 180), style="s", sw=1))
            c.drawLine(a[0] + 5, a[1], a[0] + 13, a[1], paint(alpha(skia.ColorWHITE, 180), style="s", sw=1))
            c.drawLine(a[0], a[1] - 13, a[0], a[1] - 5, paint(alpha(skia.ColorWHITE, 180), style="s", sw=1))
            c.drawLine(a[0], a[1] + 5, a[0], a[1] + 13, paint(alpha(skia.ColorWHITE, 180), style="s", sw=1))
        for L in pl.lights:
            if t < L["t0"] or t >= pl.T["render"][0]:
                continue
            uu = float(ease_out_back((t - L["t0"]) / max(L["t1"] - L["t0"], 1e-3)))
            spawn = pl.center + np.array([0, 0, pl.radius * 0.1])
            pw = spawn + (L["loc"] - spawn) * uu
            s = proj(pw)
            if s is None:
                continue
            col = skia.Color(255, 217, 110)
            c.drawCircle(s[0], s[1], 7, paint(alpha(col, 70)))
            c.drawCircle(s[0], s[1], 7, paint(col, style="s", sw=1.5))
            c.drawLine(s[0] - 3, s[1], s[0] + 3, s[1], paint(col, style="s", sw=1.2))
            c.drawLine(s[0], s[1] - 3, s[0], s[1] + 3, paint(col, style="s", sw=1.2))
            o = proj(pl.center)
            if o:
                dash = paint(alpha(col, 130), style="s", sw=1.0)
                dash.setPathEffect(skia.DashPathEffect.Make([5, 5], t * 40))
                c.drawLine(s[0], s[1], o[0], o[1], dash)
            ff = self.painter.f("med", 11)
            text(c, L["name"], s[0] + 12, s[1] + 4, ff, col)
        c.restore()

    # ------------------------------------------------------------- the frame
    def frame(self, n):
        pl, s = self.pl, self.pl.settings
        t = n / s.fps
        c = self.canvas
        c.save()
        c.scale(self.scale, self.scale)
        c.clear(U.BG)
        if t >= pl.show0:
            self.show.draw(c, t)
            c.restore()
            self.surface.flushAndSubmit()
            self.finisher.apply(self.rgba, n, 1.0)
            return self.rgba

        fx = fx_at(pl, t)
        rects = layout_at(fx, pl.ui_w, pl.ui_h, pl.ui_mode)
        self.update_geometry(t)
        self.update_material_state(t)
        added = pl.part_add <= t
        visible = np.flatnonzero(added)

        cam = cam_at(pl, t)
        v = rects["view"]
        vw = max(16, int(v[2] * self.scale))
        vh = max(16, int(v[3] * self.scale))
        vp = self.vp
        shadow = vp.shadow_map(pl.center, pl.radius, visible)
        img = vp.render(cam, vw, vh, visible, ss=s.ss, ground=pl.ground, grid=pl.grid, fade=pl.fade,
                        shadow=shadow, cavity=22.0, exposure=1.0)
        view_img = skia.Image.fromarray(img, colorType=skia.kRGBA_8888_ColorType)

        p = self.painter
        active = self.active_part(t)
        ops_now = self.active_ops(t)
        mode = "Object Mode"
        if any(pl.op_kind[q] == "edit" for q in ops_now[-8:]):
            mode = "Sculpt Mode" if p._sculpting(t) else "Edit Mode"
        elif pl.T["shade"][0] <= t < pl.T["shade"][1]:
            mode = "Texture Paint" if any(pl.op_kind[q] == "paint" for q in ops_now[-8:]) else "Object Mode"

        chrome = fx.full < 0.95
        p.viewport_chrome(c, v, t, view_img, self.data.parts[active]["name"] if active >= 0 else "—", mode, chrome)

        # orthographic views: flat + wire, no shadows, native resolution
        ortho_imgs = []
        for k, (label, oc) in enumerate(pl.ortho_cams):
            rr_ = rects["ortho%d" % k]
            if rr_[2] < 12 or rr_[3] < 12:
                continue
            ow = max(8, int(rr_[2] * self.scale))
            oh = max(8, int(rr_[3] * self.scale))
            from .scene import Camera
            cam_o = Camera(oc.eye, oc.target, tany=oc.tany, ortho=True, up=oc.up)
            arr = vp.render(cam_o, ow, oh, visible, ss=1, ground=pl.ground, grid=0.0, fade=pl.fade,
                            shadow=None, flat=True, cavity=0.0)
            ortho_imgs.append(arr)
            p.ortho(c, rr_, skia.Image.fromarray(arr, colorType=skia.kRGBA_8888_ColorType), label)

        if fx.full < 1.0:
            xform = self.xform_values(t, active)
            p.outliner(c, rects["outliner"], t, active)
            p.properties(c, rects["props"], t, active, xform)
            if fx.nodes > 0.02:
                p.nodes(c, rects["info"], t)
            else:
                p.info_log(c, rects["info"], t)
            p.timeline(c, rects["timeline"], t)
            done = (pl.part_add <= t)
            stats = self.stats(t, done)
            p.topbar(c, rects["top"], t)
            p.statusbar(c, rects["status"], t, stats)

        self.vertex_dots(c, t, rects, cam, ops_now)
        self.gizmos(c, t, rects, cam, chrome)

        r0, r1 = pl.T["render"]
        if t >= r0:
            self.render_phase(c, t, r0, r1)
        p.chips(c, t, rects, ops_now)
        p.trails_and_cursors(c, t, self.scale)
        p.title_card(c, t)
        c.restore()
        self.surface.flushAndSubmit()
        # the look fades in as the interface leaves, or runs throughout with no interface
        self.finisher.apply(self.rgba, n, 1.0 if pl.ui_mode == "clean" else float(fx.full))
        return self.rgba

    def stats(self, t, done):
        d = self.data
        ids = np.flatnonzero(done)
        key = len(ids)
        if key not in self.stats_cache:
            nv = sum(d.parts[i]["nv"] for i in ids)
            nt = sum(d.parts[i]["nt"] for i in ids)
            nf = sum(d.parts[i]["npoly"] for i in ids)
            self.stats_cache[key] = "Objects %d/%d  |  Verts %s  |  Faces %s  |  Tris %s" % (
                key, d.npart, format(nv, ","), format(nf, ","), format(nt, ","))
        return self.stats_cache[key]

    def _load_hero(self):
        if self.hero is not None:
            return
        path = os.path.join(getattr(self.pl, "beauty_dir", None) or self.work, "stills", "00.png")
        arr = load_rgb(path, self.W, self.H)
        rng = np.random.default_rng(3)
        self.hero_arr = arr
        self.hero = skia.Image.fromarray(arr, colorType=skia.kRGBA_8888_ColorType)
        noisy = arr.astype(np.float32).copy()
        grain = rng.random((self.H // 2, self.W // 2), np.float32)
        grain = grain.repeat(2, 0).repeat(2, 1)
        grain = np.pad(grain, ((0, self.H - grain.shape[0]), (0, self.W - grain.shape[1])), mode="edge")
        sparse = 0.45 + (grain ** 2.2) * 1.5
        noisy[..., :3] = np.clip(noisy[..., :3] * sparse[..., None] * 0.92 + 8, 0, 255)
        self.hero_noisy_arr = noisy.astype(np.uint8)
        self.hero_noisy = skia.Image.fromarray(self.hero_noisy_arr, colorType=skia.kRGBA_8888_ColorType)

    def render_phase(self, c, t, r0, r1):
        self._load_hero()
        pl = self.pl
        if not pl.tiles:            # the render phase is switched off: a simple dissolve
            k = float(ease_io((t - r0) / max(r1 - r0, 1e-3)))
            pn = skia.Paint(AntiAlias=True)
            pn.setAlphaf(k)
            c.drawImageRect(self.hero, R(0, 0, pl.ui_w, pl.ui_h), skia.SamplingOptions(skia.FilterMode.kLinear), pn)
            return
        tw, th = pl.ui_w / pl.tile_grid[0], pl.ui_h / pl.tile_grid[1]
        sampling = skia.SamplingOptions(skia.FilterMode.kLinear)
        sw, sh = self.W / pl.tile_grid[0], self.H / pl.tile_grid[1]
        bracket = paint(U.ACCENT, style="s", sw=2.0)
        for x, y, t0, t1, cursor in pl.tiles:
            if t < t0:
                continue
            u = (t - t0) / max(t1 - t0, 1e-4)
            dst = R(x * tw, y * th, tw + 0.5, th + 0.5)
            src = R(x * sw, y * sh, sw, sh)
            if u >= 1.0:
                c.drawImageRect(self.hero, src, dst, sampling)
                continue
            c.drawImageRect(self.hero_noisy, src, dst, sampling)
            k = min(1.0, max(0.0, (u - 0.25) / 0.6))
            pn = skia.Paint(Alphaf=k, AntiAlias=False)
            c.drawImageRect(self.hero, src, dst, sampling, pn)
            xx, yy = x * tw + 2, y * th + 2
            w2, h2 = tw - 4, th - 4
            L = 12
            for cx, cy, sx_, sy_ in ((xx, yy, 1, 1), (xx + w2, yy, -1, 1), (xx, yy + h2, 1, -1), (xx + w2, yy + h2, -1, -1)):
                c.drawLine(cx, cy, cx + L * sx_, cy, bracket)
                c.drawLine(cx, cy, cx, cy + L * sy_, bracket)
        # progress header
        done = sum(1 for tile in pl.tiles if t >= tile[3])
        p = self.painter
        rr(c, 36, 36, 266, 64, alpha(skia.Color(10, 12, 16), 200), 10)
        text(c, "RENDERING", 54, 62, p.f("bold", 13), U.TEAL)
        text(c, "%d / %d tiles" % (done, len(pl.tiles)), 150, 62, p.f("mono", 13), skia.Color(205, 209, 217))
        rr(c, 54, 76, 230, 6, skia.Color(45, 50, 60), 3)
        rr(c, 54, 76, 230 * done / max(1, len(pl.tiles)), 6, U.TEAL, 3)
