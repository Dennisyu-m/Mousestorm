# SPDX-License-Identifier: GPL-3.0-or-later
"""The finale: hero title, turntable with an x-ray sweep, the multi-camera photo
shoot with real parallax, the swarm's light-painting, and the contact sheet."""
import math
import os
from collections import OrderedDict

import numpy as np
import skia
from PIL import Image

from . import raster as rs
from . import ui as U
from .options import items_of
from .plan import ease_io, ease_out, show_schedule
from .plan_finish import BAKE_HZ, lens_tans
from .scene import Camera
from .themes import srgb_to_linear, theme as get_theme
from .ui import R, alpha, draw_cursor, paint, rr, text
from .ui_layout import is_wide


def count(n, one, many):
    return "{:,} {}".format(n, one if n == 1 else many)


def cam_vector(eye, target):
    """eye, right, up, forward as the flat array raster.view_interp expects."""
    cam = Camera(np.asarray(eye, np.float64), np.asarray(target, np.float64))
    r, u, f = cam.basis()
    return np.concatenate([np.asarray(eye, np.float64), r, u, f]).astype(np.float64)


class ViewWarp:
    """Synthesises new views of Cycles renders from the scene's real geometry
    (see raster.view_interp). Each worker keeps depth for recently used views."""

    def __init__(self, pl, d, W, H):
        self.pl, self.d, self.W, self.H = pl, d, W, H
        self.ss = 2 if max(W, H) <= 2560 else 1
        self.P = np.array(d.P, np.float32)
        self.V = np.empty_like(self.P)
        parts = d.parts
        self.tr = np.array([[p["t0"], p["t0"] + p["nt"]] for p in parts], np.int64)
        self.vr = np.array([[p["v0"], p["v0"] + p["nv"]] for p in parts], np.int64)
        self.opaque = np.ones(len(d.T), np.float32)
        self.cache = OrderedDict()
        ss = self.ss
        self.ZT = np.empty((H * ss, W * ss), np.float32)
        self.IDT = np.empty((H * ss, W * ss), np.int32)
        self.ID1 = np.empty((H, W), np.int32)
        self.out = np.empty((H, W, 4), np.uint8)
        self.floor_z = float(pl.rig["floor_z"]) if pl.rig.get("floor") else 0.0
        self.has_floor = bool(pl.rig.get("floor"))
        self.screen_bg = bool(pl.rig.get("enabled"))

    def depth(self, Z, ID, eye, target, W, H, tanx, tany):
        cam = Camera(np.asarray(eye, np.float64), np.asarray(target, np.float64))
        r, u, f = cam.basis()
        Rm = np.stack([r, u, f]).astype(np.float64)
        Z.fill(0.0)
        ID.fill(-1)
        rs.to_view(self.P, self.tr, self.vr, np.asarray(eye, np.float64), Rm, self.V)
        rs.rasterize(self.V, self.d.T, self.tr, W, H, tanx, tany, self.d.near, False, Z, ID, self.opaque, rs.BAYER)
        return Z

    def source(self, key, eye, target, tanx, tany, arr):
        """Depth buffer and backdrop profile of one rendered view."""
        v = self.cache.get(key)
        if v is None:
            Z = self.depth(np.empty((self.H, self.W), np.float32), self.ID1, eye, target, self.W, self.H, tanx, tany)
            rows = np.empty((self.H, 4), np.float64)
            rs.backdrop_rows(arr, Z, self.W, self.H, rows)
            v = (Z, rows)
            self.cache[key] = v
            while len(self.cache) > 8:
                self.cache.popitem(last=False)
        return v

    def synth(self, eye, target, tanx, tany, a, b, fr):
        """a, b = (key, eye, target, rgba array) of the two source views."""
        za, ra = self.source(a[0], a[1], a[2], tanx, tany, a[3])
        zb, rb = self.source(b[0], b[1], b[2], tanx, tany, b[3])
        ss = self.ss
        self.depth(self.ZT, self.IDT, eye, target, self.W * ss, self.H * ss, tanx, tany)
        rs.view_interp(self.W, self.H, ss, self.ZT, za, zb, a[3], b[3], ra, rb, cam_vector(eye, target),
                       cam_vector(a[1], a[2]), cam_vector(b[1], b[2]), tanx, tany, self.floor_z, self.has_floor,
                       self.screen_bg, float(fr), self.out)
        return self.out


class Showcase:
    def __init__(self, pl, d, work, painter, W, H, owner=None):
        self.pl, self.d, self.work, self.p = pl, d, work, painter
        self.W, self.H = W, H
        self.owner = owner
        self.UW, self.UH = pl.ui_w, pl.ui_h
        self.wide = is_wide(self.UW)
        self.opt = pl.settings.opt
        self.src = getattr(pl, "beauty_dir", None) or work
        self.still_cache = {}
        self.turn_cache = OrderedDict()
        self.schedule = show_schedule(pl.show_D, items_of(self.opt, "show.segments"))
        self.lengths = {name: length for name, _, length in self.schedule}
        self.shot_each = self.lengths.get("shoot", 1.0) / max(1, len(pl.stills))
        self.warp = None
        self.aspect = W / H
        self.bar = 0.0
        lb = self.opt["look.letterbox"]
        if lb != "off" and self.aspect > float(lb) * 0.55 and self.aspect < float(lb):
            self.bar = (self.UH - self.UW / float(lb)) / 2.0
        self.poster_buf = None
        self.xray_cam = None

    # ------------------------------------------------------------- sources
    def still(self, i):
        im = self.still_cache.get(i)
        if im is None:
            arr = np.asarray(Image.open(os.path.join(self.src, "stills", "%02d.png" % i)).convert("RGBA"), np.uint8)
            im = (skia.Image.fromarray(arr, colorType=skia.kRGBA_8888_ColorType), arr)
            self.still_cache[i] = im
        return im[0]

    def still_arr(self, i):
        self.still(i)
        return self.still_cache[i][1]

    def turn(self, i):
        i = i % self.pl.turn_n
        v = self.turn_cache.get(i)
        if v is None:
            arr = np.asarray(Image.open(os.path.join(self.src, "turntable", "%04d.jpg" % i)).convert("RGBA"), np.uint8)
            v = (skia.Image.fromarray(arr, colorType=skia.kRGBA_8888_ColorType), arr)
            self.turn_cache[i] = v
            while len(self.turn_cache) > 16:
                self.turn_cache.popitem(last=False)
        else:
            self.turn_cache.move_to_end(i)
        return v

    def get_warp(self):
        if self.warp is None:
            self.warp = ViewWarp(self.pl, self.d, self.W, self.H)
        return self.warp

    # ------------------------------------------------------------- drawing
    def full(self, c, img, zoom=1.0, cx=0.5, cy=0.5, alpha_=1.0):
        w, h = self.UW * zoom, self.UH * zoom
        x, y = (self.UW - w) * cx, (self.UH - h) * cy
        p = skia.Paint(AntiAlias=True)
        if alpha_ < 1.0:
            p.setAlphaf(alpha_)
        c.drawImageRect(img, R(x, y, w, h), skia.SamplingOptions(skia.FilterMode.kLinear), p)

    def gradient_bar(self, c, y0, y1, a0, a1):
        pts = [skia.Point(0, y0), skia.Point(0, y1)]
        shader = skia.GradientShader.MakeLinear(pts, [alpha(skia.ColorBLACK, a0), alpha(skia.ColorBLACK, a1)])
        c.drawRect(R(0, y0, self.UW, y1 - y0), skia.Paint(Shader=shader))

    def draw(self, c, t):
        u = t - self.pl.show0
        for name, start, length in self.schedule:
            if u < start + length or name == self.schedule[-1][0]:
                getattr(self, "seg_" + name)(c, u - start, length, t)
                break
        self.letterbox(c, u)

    def letterbox(self, c, u):
        if self.bar <= 0:
            return
        a = float(ease_io(min(1.0, u / 0.5)))
        h = self.bar * a
        black = paint(skia.ColorBLACK)
        c.drawRect(R(0, 0, self.UW, h), black)
        c.drawRect(R(0, self.UH - h, self.UW, h), black)

    def lines_of(self):
        d, pl = self.d, self.pl
        totals = d.doc["totals"]
        parts = [count(len(d.parts), "object", "objects"), count(totals["verts"], "vert", "verts"),
                 count(totals["polys"], "polygon", "polygons"), count(len(d.materials), "material", "materials")]
        built = "Assembled by {} in {:.1f} s  ·  {:,} operations".format(
            count(pl.n_mice, "mouse", "mice"), pl.T["render"][0], pl.n_ops)
        return parts, built

    def fitted(self, s, face, size, limit, floor=10):
        p = self.p
        font = p.str_font(s, face, size)
        while font.measureText(s) > limit and size > floor:
            size -= 1
            font = p.str_font(s, face, size)
        return font

    def caption(self, c, a):
        p = self.p
        UW, UH = self.UW, self.UH
        name = self.p.display_name()
        self.gradient_bar(c, UH * 0.62, UH, 0, int(180 * a))
        c.save()
        c.translate(0, -self.bar * 0.75)
        x = 72 if self.wide else 54
        text(c, name, x, UH - 132, self.fitted(name, "black", 62, UW - x * 2, 26), alpha(skia.ColorWHITE, 255 * a))
        parts, built = self.lines_of()
        lines = ["   ·   ".join(parts)] if self.wide else ["  ·  ".join(parts[:2]), "  ·  ".join(parts[2:])]
        y = UH - 90 if self.wide else UH - 96
        for line in lines:
            text(c, line, x + 3, y, p.f("med", 21), alpha(skia.Color(214, 218, 226), 255 * a))
            y += 30
        size2 = 16.5
        while p.f("mono", size2).measureText(built) > UW - x * 2 and size2 > 10:
            size2 -= 0.5
        text(c, built, x + 3, y + 2 if not self.wide else UH - 58, p.f("mono", size2), alpha(U.ACCENT, 255 * a))
        c.restore()

    def tag(self, c, big, small, a, lift=None):
        p = self.p
        H = self.UH
        lift = (0 if self.wide else 130) + self.bar * 0.75 if lift is None else lift
        rr(c, 54, H - 120 - lift, 392, 76, alpha(skia.Color(8, 9, 12), 190 * a), 10)
        c.drawRect(R(54, H - 112 - lift, 3.5, 60), paint(alpha(U.ACCENT, 255 * a)))
        text(c, big, 76, H - 86 - lift, p.f("bold", 22), alpha(skia.ColorWHITE, 255 * a))
        text(c, small, 76, H - 59 - lift, p.f("mono", 14.5), alpha(skia.Color(190, 195, 205), 255 * a))

    def progress_arc(self, c, cx, cy, s, a):
        pr = paint(alpha(skia.ColorWHITE, 50 * a), style="s", sw=3.2)
        c.drawCircle(cx, cy, 26, pr)
        c.drawArc(R(cx - 26, cy - 26, 52, 52), -90, 360 * s, False, paint(alpha(U.ACCENT, 255 * a), style="s", sw=3.2))
        p = self.p
        lab = "%03d°" % int(s * 360)
        f = p.f("mono_b", 12)
        text(c, lab, cx - f.measureText(lab) / 2, cy + 4.5, f, alpha(skia.ColorWHITE, 230 * a))

    # ---------------------------------------------------------------- hero
    def seg_hero(self, c, u, length, t):
        k = u / length
        # breathes in and back out, so both neighbouring shots join without a jump
        self.full(c, self.still(0), 1.0 + 0.03 * math.sin(math.pi * float(ease_io(k))))
        a = min(1.0, u / 0.5) * min(1.0, (length - u) / 0.45)
        if a > 0 and self.opt["title.caption"]:
            self.caption(c, a)

    # ----------------------------------------------------------- turntable
    def turn_camera(self, i, fr):
        n = self.pl.turn_n
        a, b = self.pl.turntable[i % n], self.pl.turntable[(i + 1) % n]
        ea, eb = np.array(a["eye"], np.float64), np.array(b["eye"], np.float64)
        ta, tb = np.array(a["target"], np.float64), np.array(b["target"], np.float64)
        target = ta + (tb - ta) * fr
        # travel on the orbit, not along the chord between the two eyes
        oa, ob = ea - ta, eb - tb
        la, lb = np.linalg.norm(oa), np.linalg.norm(ob)
        off = oa + (ob - oa) * fr
        eye = target + off / max(np.linalg.norm(off), 1e-12) * (la + (lb - la) * fr)
        return eye, target, ea, ta, eb, tb

    def xray(self, t, eye, target, tany):
        """The same camera, drawn by the modelling viewport: clay and wireframe."""
        fr = self.owner
        fr.update_geometry(t)
        vp = fr.vp
        vp.pwire[:] = 0.85
        vp.ppaint[:] = 0.0
        pl = self.pl
        visible = np.arange(self.d.npart)
        cam = Camera(np.asarray(eye, np.float64), np.asarray(target, np.float64), tany=tany)
        shadow = vp.shadow_map(pl.center, pl.radius, visible)
        return vp.render(cam, self.W, self.H, visible, ss=2 if max(self.W, self.H) <= 1920 else 1, ground=pl.ground,
                         grid=pl.grid, fade=pl.fade, shadow=shadow, cavity=22.0, exposure=1.0)

    def seg_turntable(self, c, u, length, t):
        pl = self.pl
        n = pl.turn_n
        turns = int(self.opt["show.turns"])
        s = u / length
        eased = s - math.sin(2.0 * math.pi * s) / (2.0 * math.pi) * 0.55     # eases out of the hero pose and back into it
        x = eased * turns * n
        i = int(x)
        fr = x - i
        lens = pl.turntable[0]["lens"]
        tanx, tany = lens_tans(lens, self.aspect)
        eye, target, ea, ta, eb, tb = self.turn_camera(i, fr)
        if fr < 0.004:
            self.full(c, self.turn(i)[0])
        else:
            arr = self.get_warp().synth(eye, target, tanx, tany, ("t%d" % (i % n), ea, ta, self.turn(i)[1]),
                                        ("t%d" % ((i + 1) % n), eb, tb, self.turn(i + 1)[1]), fr)
            self.full(c, skia.Image.fromarray(arr, colorType=skia.kRGBA_8888_ColorType))

        # x-ray: a scan line reveals how it is made, then a second one takes it back
        if self.opt["show.wipe"]:
            a0, a1, b0, b1 = 0.16, 0.42, 0.58, 0.84
            left, right = None, None
            if a0 < s < a1:
                left, right = 0.0, float(ease_io((s - a0) / (a1 - a0)))
                edge = right
            elif a1 <= s <= b0:
                left, right, edge = 0.0, 1.0, None
            elif b0 < s < b1:
                left, right = float(ease_io((s - b0) / (b1 - b0))), 1.0
                edge = left
            if left is not None:
                arr = self.xray(t, eye, target, tany)
                c.save()
                c.clipRect(R(self.UW * left, 0, self.UW * (right - left), self.UH))
                self.full(c, skia.Image.fromarray(arr, colorType=skia.kRGBA_8888_ColorType))
                c.restore()
                if edge is not None:
                    self.scan_line(c, edge * self.UW)

        a = min(1.0, u / 0.3, (length - u) / 0.3)
        self.tag(c, "TURNTABLE", "%s · %d mm · Cycles" % ("360°" if turns == 1 else "%d × 360°" % turns, lens), a)
        self.progress_arc(c, self.UW - 100, 100 + self.bar * 0.75, (eased * turns) % 1.0, a)

    def scan_line(self, c, x):
        w = 90.0
        for x0, x1, a0, a1 in ((x - w, x, 0, 90), (x, x + w, 90, 0)):
            pts = [skia.Point(x0, 0), skia.Point(x1, 0)]
            shader = skia.GradientShader.MakeLinear(pts, [alpha(U.ACCENT, a0), alpha(U.ACCENT, a1)])
            c.drawRect(R(x0, 0, x1 - x0, self.UH), skia.Paint(Shader=shader))
        c.drawRect(R(x - 1.5, 0, 3.0, self.UH), paint(alpha(skia.ColorWHITE, 235)))

    # --------------------------------------------------------------- photos
    def parallax_view(self, i, local):
        """The photograph, seen from a camera that keeps moving: real parallax,
        where a Ken Burns zoom would only slide a flat picture."""
        amp = float(self.opt["show.parallax"])
        shot = self.pl.stills[i]
        if amp <= 0 or shot.get("matrix"):
            return None
        eye0, target = np.array(shot["eye"], np.float64), np.array(shot["target"], np.float64)
        off = eye0 - target
        ang = math.radians(amp * 3.4) * (local - 0.5) * 2.0
        ca, sa = math.cos(ang), math.sin(ang)
        off2 = np.array([off[0] * ca - off[1] * sa, off[0] * sa + off[1] * ca, off[2]])
        eye = target + off2 * (1.0 - 0.02 * amp * local)
        tanx, tany = lens_tans(shot["lens"], self.aspect)
        src = ("s%d" % i, eye0, target, self.still_arr(i))
        return self.get_warp().synth(eye, target, tanx, tany, src, src, 0.5)

    def draw_photo(self, c, i, local, alpha_=1.0):
        arr = self.parallax_view(i, local)
        if arr is None:
            self.full(c, self.still(i), 1.015 + 0.05 * local, 0.5 + 0.06 * math.sin(i * 2.4), 0.5, alpha_)
        else:
            # a slight overscan hides the edge the moved camera could not see
            self.full(c, skia.Image.fromarray(arr.copy(), colorType=skia.kRGBA_8888_ColorType), 1.035, 0.5, 0.5, alpha_)

    def seg_shoot(self, c, u, length, t):
        pl = self.pl
        UW, UH = self.UW, self.UH
        n = len(pl.stills)
        each = self.shot_each
        i = min(n - 1, int(u / each))
        local = (u - i * each) / each
        shot = pl.stills[i]
        self.draw_photo(c, i, local)
        if local < 0.16 and i > 0:      # the last frame dissolves out under the flash
            self.draw_photo(c, i - 1, 1.0, 1.0 - float(ease_io(local / 0.16)))

        # viewfinder corners
        bar = self.bar
        m = 44
        L = 46
        vf = paint(alpha(skia.ColorWHITE, 170), style="s", sw=2.2)
        for x, y, sx, sy in ((m, m + bar, 1, 1), (UW - m, m + bar, -1, 1), (m, UH - m - bar, 1, -1), (UW - m, UH - m - bar, -1, -1)):
            c.drawLine(x, y, x + L * sx, y, vf)
            c.drawLine(x, y, x, y + L * sy, vf)

        # thumbnail strip builds up
        gap = 8
        tw = min(121.0, (UW - 140.0 - (n - 1) * gap) / max(n, 1))
        th = tw / self.aspect
        total = n * tw + (n - 1) * gap
        x0 = UW - total - 70
        y0 = UH - th - 56 - bar
        lens = shot["lens"]
        # in a tall frame the strip is tall too, so the caption stands on top of it
        self.tag(c, "CAM %02d  ·  %s" % (i + 1, shot["label"]),
                 "%d mm  ·  %s" % (lens, "f/%.1f" % shot["dof"] if shot.get("dof") else "Pinhole"), min(1.0, local / 0.08),
                 lift=(None if self.wide else th + 12 + 56 - 44 + bar))
        for k in range(i + 1):
            fx = x0 + k * (tw + gap)
            c.drawImageRect(self.still(k), R(fx, y0, tw, th), skia.SamplingOptions(skia.FilterMode.kLinear))
            border = U.ACCENT if k == i else alpha(skia.ColorWHITE, 120)
            c.drawRect(R(fx, y0, tw, th), paint(border, style="s", sw=1.4 if k != i else 2.2))

        # a mouse presses the shutter
        cu = local * 4.0
        cx = UW * (0.5 + 0.28 * math.sin(i * 1.9))
        cy = UH * (0.42 + 0.13 * math.cos(i * 1.3))
        if cu < 1.0:
            draw_cursor(c, "cross", cx + (1.0 - ease_out(cu)) * 140, cy + (1.0 - ease_out(cu)) * 90, U.ACCENT, 1.8)
        else:
            draw_cursor(c, "cross", cx, cy, U.ACCENT, 1.6)

        # A gentle flash. Full white nine times in a row is a strobe, not a shutter.
        flash = float(self.opt["show.flash"])
        if local < 0.12 and flash > 0:
            c.drawRect(R(0, 0, UW, UH), paint(alpha(skia.ColorWHITE, 210 * flash * (1 - local / 0.12))))

    # --------------------------------------------------------------- poster
    def poster_colors(self):
        """Light colour along the timeline: each phase of the build paints in its own."""
        pl = self.pl
        t = get_theme(self.opt["look.theme"])
        n = pl.bake_n
        axis = [0.0, pl.T["model"][1], pl.T["shade"][1], pl.T["light"][1], pl.T["render"][1]]
        cols = [srgb_to_linear(t[k]) for k in ("accent", "rose", "gold", "teal", "teal")]
        times = np.arange(n) / BAKE_HZ
        return np.stack([np.interp(times, axis, [c[j] for c in cols]) for j in range(3)], 1).astype(np.float32)

    def light_painting(self, W, H, fraction):
        pl = self.pl
        scale = W / pl.ui_w
        if self.poster_buf is None or self.poster_buf.shape[:2] != (H, W):
            self.poster_buf = np.empty((H, W, 3), np.float32)
            self.poster_rgba = np.empty((H, W, 4), np.uint8)
        buf = self.poster_buf
        buf.fill(0.0)
        if not hasattr(self, "pcol"):
            self.pcol = self.poster_colors()
        stop = int(min(pl.show0, pl.duration) * BAKE_HZ)
        end = max(2, int(stop * fraction))
        # the look should not depend on how many mice there are or how large the image is
        gain = 1.0 / max(pl.n_mice / 72.0, 0.3) ** 0.55
        rs.splat_paths(pl.cpos, self.pcol, end, 2, float(scale), W, H, buf, float(gain))
        t = get_theme(self.opt["look.theme"])
        base = np.array(srgb_to_linear(t["sheet"]), np.float64)
        rs.tonemap_paths(buf, W, H, base, self.poster_rgba, 0.42 * W / 1920.0)
        return self.poster_rgba

    def poster_text(self, c, UW, UH, a, big=1.0, lift=0.0):
        """lift keeps the block clear of the letterbox; the exported print has none."""
        p = self.p
        name = p.display_name()
        x = (72 if self.wide else 54) * big
        ink = U.TEXT if get_theme(self.opt["look.theme"]) is get_theme("paper") else skia.ColorWHITE
        limit = UW - x * 2
        y = UH - 118 * big - lift
        text(c, name, x, y, self.fitted(name, "black", 58 * big, limit, 22), alpha(ink, 255 * a))
        _, built = self.lines_of()
        size = 18.0 * big
        while p.f("mono", size).measureText(built) > limit and size > 10:
            size -= 0.5
        text(c, built, x + 2, UH - 74 * big - lift, p.f("mono", size), alpha(U.ACCENT, 255 * a))
        f = p.f("med", 14 * big)
        text(c, "Every line is a mouse.", x + 2, UH - 46 * big - lift, f, alpha(ink, 170 * a))

    def seg_poster(self, c, u, length, t):
        fraction = float(ease_io(min(1.0, u / (length * 0.72))))
        arr = self.light_painting(self.W, self.H, fraction)
        self.full(c, skia.Image.fromarray(arr, colorType=skia.kRGBA_8888_ColorType))
        a = min(1.0, max(0.0, (u - length * 0.25) / 0.4)) * min(1.0, (length - u) / 0.3)
        self.poster_text(c, self.UW, self.UH, a, lift=self.bar)

    def export_poster(self, path):
        """A large standalone print of the swarm's light-painting."""
        k = 2 if max(self.W, self.H) <= 2160 else 1
        W, H = self.W * k, self.H * k
        arr = self.light_painting(W, H, 1.0)
        info = skia.ImageInfo.Make(W, H, skia.kRGBA_8888_ColorType, skia.kPremul_AlphaType)
        surf = skia.Surface.MakeRasterDirect(info, arr)
        c = surf.getCanvas()
        c.save()
        c.scale(W / self.UW, W / self.UW)
        self.poster_text(c, self.UW, self.UH, 1.0)
        c.restore()
        surf.flushAndSubmit()
        Image.fromarray(arr[..., :3]).save(path)

    # ---------------------------------------------------------------- sheet
    def seg_sheet(self, c, u, length, t):
        pl = self.pl
        UW, UH = self.UW, self.UH
        n = min(len(pl.stills), 9)
        c.clear(U.SHEET)
        # the frame never quite stops: a slow push until the fade
        push = 1.0 + 0.022 * float(ease_io(u / max(length, 1e-3)))
        c.save()
        c.translate(UW / 2, UH / 2)
        c.scale(push, push)
        c.translate(-UW / 2, -UH / 2)
        cols = 3
        rows = max(1, int(math.ceil(n / cols)))
        gap = 18
        M_side = 200 if self.wide else 54
        tw = (UW - M_side * 2 - gap * (cols - 1)) / cols
        th = tw / self.aspect
        max_h = UH - 230 - self.bar * 2
        total_h = th * rows + gap * (rows - 1)
        if total_h > max_h:          # a square frame runs out of height before width
            th = (max_h - gap * (rows - 1)) / rows
            tw = th * self.aspect
            M_side = (UW - tw * cols - gap * (cols - 1)) / 2
            total_h = max_h
        y0 = (UH - total_h) * 0.5 + 4
        p = self.p
        # short films have a short end card; the animation keeps enough of it to read
        fade = min(0.55, length * 0.2)
        fly = min(0.5, length * 0.22)
        stagger = min(0.05, length * 0.012)
        fade_tail = min(1.0, max(0.0, (length - u) / fade))
        for k in range(n):
            row, col = divmod(k, cols)
            tx = M_side + col * (tw + gap)
            ty = y0 + row * (th + gap)
            d = k * stagger
            e = float(ease_io((u - d) / fly))
            ox = (UW * 0.5 - tw * 0.5 - tx) * (1 - e)
            oy = (UH * 0.5 - th * 0.5 - ty) * (1 - e)
            sc = 1.5 - 0.5 * e if e < 1 else 1.0
            c.save()
            c.translate(tx + ox + tw / 2, ty + oy + th / 2)
            c.scale(sc, sc)
            c.drawImageRect(self.still(k), R(-tw / 2, -th / 2, tw, th), skia.SamplingOptions(skia.FilterMode.kLinear))
            lab = "%02d  %s" % (k + 1, pl.stills[k]["label"])
            c.drawRect(R(-tw / 2, th / 2 - 26, tw, 26), paint(alpha(skia.ColorBLACK, 130)))
            text(c, lab, -tw / 2 + 8, th / 2 - 8, p.f("mono", 11.5), alpha(skia.ColorWHITE, 230))
            c.restore()
        a = min(1.0, max(0.0, (u - fly) / 0.4))
        name = p.display_name()
        text(c, name, M_side, y0 - 34, self.fitted(name, "bold", 24, UW * 0.6, 12), alpha(U.TEXT, 255 * a))
        if self.opt["title.branding"]:
            tag = "MOUSESTORM"
            f2 = p.f("black", 15)
            tw2 = f2.measureText(tag)
            c.save()
            c.translate(UW - M_side - tw2 - 60, y0 - 49)
            c.scale(0.62, 0.62)
            draw_cursor(c, "arrow", 0, 0, U.ACCENT)
            draw_cursor(c, "arrow", 14, 6, U.BLUE)
            draw_cursor(c, "arrow", 4, 14, U.TEAL)
            c.restore()
            text(c, tag, UW - M_side - tw2, y0 - 34, f2, alpha(U.TEXT, 255 * a))
        foot = self.opt["title.footer"] or "Built in {:.1f} s by {}  ·  {:,} operations  ·  Open source under the GPL".format(
            pl.T["render"][0], count(pl.n_mice, "mouse", "mice"), pl.n_ops)
        f3 = self.fitted(foot, "mono", 14, UW - 60, 9)
        text(c, foot, UW / 2 - f3.measureText(foot) / 2, y0 + total_h + 42, f3, alpha(U.DIM, 255 * a))
        c.restore()
        if fade_tail < 1:
            c.drawRect(R(0, 0, UW, UH), paint(alpha(skia.ColorBLACK, 255 * (1 - fade_tail))))

    def export_sheet(self, path):
        """Writes the same contact sheet as a standalone PNG deliverable."""
        pl = self.pl
        n = len(pl.stills)
        cols = 3
        rows = max(1, int(math.ceil(n / cols)))
        tw = 800 if self.aspect >= 1.0 else 450
        th = int(round(tw / self.aspect))
        gap = 14
        M = 44
        head = 92
        W = M * 2 + tw * cols + gap * (cols - 1)
        H = head + M + th * rows + gap * (rows - 1) + 56
        arr = np.zeros((H, W, 4), np.uint8)
        info = skia.ImageInfo.Make(W, H, skia.kRGBA_8888_ColorType, skia.kPremul_AlphaType)
        surf = skia.Surface.MakeRasterDirect(info, arr)
        c = surf.getCanvas()
        c.clear(U.SHEET)
        p = self.p
        name = p.display_name()
        text(c, name, M, 64, p.str_font(name, "bold", 36), U.TEXT)
        for k in range(n):
            row, col = divmod(k, cols)
            x = M + col * (tw + gap)
            y = head + row * (th + gap)
            c.drawImageRect(self.still(k), R(x, y, tw, th), skia.SamplingOptions(skia.FilterMode.kLinear))
            c.drawRect(R(x, y + th - 30, tw, 30), paint(alpha(skia.ColorBLACK, 130)))
            text(c, "%02d  %s  ·  %d mm" % (k + 1, pl.stills[k]["label"], pl.stills[k]["lens"]), x + 10, y + th - 10,
                 p.f("mono", 14), alpha(skia.ColorWHITE, 235))
        foot = "Mousestorm · {} · {:,} operations".format(count(pl.n_mice, "mouse", "mice"), pl.n_ops)
        text(c, foot, M, H - 22, p.f("mono", 15), U.DIM)
        surf.flushAndSubmit()
        Image.fromarray(arr[..., :3]).save(path)
