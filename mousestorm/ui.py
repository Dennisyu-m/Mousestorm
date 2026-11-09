# SPDX-License-Identifier: GPL-3.0-or-later
"""Everything drawn with Skia: the workspace chrome, live panels, the swarm.

The look is our own take on a DCC workspace (original icons, palette and type;
no third-party logos or artwork). Panels show the file's real data — object
names, transforms, modifier stacks, material node graphs.
"""
import math
import os

import numpy as np
import skia

from . import themes
from .plan_finish import BAKE_HZ, ICONS, SKIN_CRITTER, SKIN_NAMES
from .ui_layout import PROP_FIRST, PROP_ROW, ROW_H, TOOL_STEP, TOOL_Y0, is_wide

HERE = os.path.dirname(os.path.abspath(__file__))
FONT_DIR = os.path.join(HERE, "assets", "fonts")


def C(hex_str, a=255):
    v = hex_str.lstrip("#")
    return skia.Color(int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16), a)


TOKENS = (("bg", "BG"), ("panel", "PANEL"), ("head", "HEAD"), ("row_alt", "ROW_ALT"), ("text", "TEXT"), ("dim", "DIM"),
          ("faint", "FAINT"), ("field", "FIELD"), ("field_hi", "FIELD_HI"), ("accent", "ACCENT"), ("blue", "BLUE"),
          ("teal", "TEAL"), ("rose", "ROSE"), ("gold", "GOLD"), ("select", "SELECT"), ("select_text", "SELECT_TEXT"),
          ("line", "LINE"), ("shelf", "SHELF"), ("icon", "ICON"), ("strip", "STRIP"), ("modrow", "MODROW"),
          ("logalt", "LOGALT"), ("hist", "HIST"), ("node", "NODE"), ("dots", "DOTS"), ("chip", "CHIP"),
          ("chip_text", "CHIP_TEXT"), ("key", "KEY"), ("sheet", "SHEET"))


def set_theme(name):
    """Rebinds the colour tokens of this module. Each worker renders one film,
    so module-level state is safe here and keeps the drawing code readable."""
    t = themes.theme(name)
    g = globals()
    for key, token in TOKENS:
        g[token] = C(t[key])
    g["CURSOR_COLORS"] = list(t["cursors"])


BG = PANEL = HEAD = ROW_ALT = TEXT = DIM = FAINT = FIELD = FIELD_HI = ACCENT = BLUE = TEAL = ROSE = GOLD = SELECT = None
SELECT_TEXT = LINE = SHELF = ICON = STRIP = MODROW = LOGALT = HIST = NODE = DOTS = CHIP = CHIP_TEXT = KEY = SHEET = None
CURSOR_COLORS = []
set_theme("graphite")



def _variable(path, wght):
    tf = skia.Typeface.MakeFromFile(path)
    try:
        coord = skia.FontArguments.VariationPosition.Coordinate(
            int.from_bytes(b"wght", "big"), float(wght))
        coords = skia.FontArguments.VariationPosition.Coordinates([coord])
        args = skia.FontArguments()
        args.setVariationDesignPosition(skia.FontArguments.VariationPosition(coords))
        return tf.makeClone(args)
    except Exception:
        return tf


class Type:
    def __init__(self):
        inter = os.path.join(FONT_DIR, "Inter.ttf")
        mono = os.path.join(FONT_DIR, "JetBrainsMono.ttf")
        self.reg = _variable(inter, 420)
        self.med = _variable(inter, 560)
        self.bold = _variable(inter, 720)
        self.black = _variable(inter, 860)
        self.thin = _variable(inter, 260)
        self.mono = _variable(mono, 430)
        self.mono_b = _variable(mono, 700)
        self.cjk = self._find_cjk()
        self._fonts = {}

    @staticmethod
    def _find_cjk():
        for p in (r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\simhei.ttf",
                  "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
                  "/System/Library/Fonts/PingFang.ttc"):
            if os.path.exists(p):
                return skia.Typeface.MakeFromFile(p)
        return None

    def font(self, face, size):
        k = (id(face), size)
        f = self._fonts.get(k)
        if f is None:
            f = skia.Font(face, size)
            f.setSubpixel(True)
            f.setEdging(skia.Font.Edging.kAntiAlias)
            f.setHinting(skia.FontHinting.kSlight)
            self._fonts[k] = f
        return f


def paint(color, aa=True, style=None, sw=None):
    p = skia.Paint(Color=color, AntiAlias=aa)
    if style == "s":
        p.setStyle(skia.Paint.kStroke_Style)
        p.setStrokeWidth(sw or 1.0)
        p.setStrokeCap(skia.Paint.kRound_Cap)
        p.setStrokeJoin(skia.Paint.kRound_Join)
    return p


def alpha(col, a):
    return (col & 0x00FFFFFF) | (int(max(0, min(255, a))) << 24)


def R(x, y, w, h):
    return skia.Rect.MakeXYWH(x, y, w, h)


def rr(c, x, y, w, h, col, rad=5.0):
    c.drawRoundRect(R(x, y, w, h), rad, rad, paint(col))


# ------------------------------------------------------------------- cursors
def _cursor_paths():
    P = {}
    s = skia.Path()
    s.moveTo(0, 0); s.lineTo(0, 18.5); s.lineTo(4.6, 14.4); s.lineTo(7.7, 21.6)
    s.lineTo(11.0, 20.3); s.lineTo(7.9, 13.2); s.lineTo(14.0, 12.8); s.close()
    P["arrow"] = s
    s = skia.Path()  # pointing hand
    s.moveTo(4, 1)
    s.cubicTo(4, -1, 8, -1, 8, 1); s.lineTo(8, 7.5); s.lineTo(14.5, 8.8)
    s.cubicTo(16.5, 9.4, 17, 11, 16.5, 13); s.lineTo(15, 20); s.lineTo(5.5, 20); s.lineTo(0.2, 13)
    s.cubicTo(-0.8, 11, 2, 9.5, 4, 11.2); s.close()
    P["pointer"] = s
    s = skia.Path()  # closed hand
    s.addRoundRect(R(1, 5, 15, 12), 5, 5)
    s.addRoundRect(R(1.5, 2, 3.4, 7), 1.7, 1.7)
    s.addRoundRect(R(5.5, 1, 3.4, 8), 1.7, 1.7)
    s.addRoundRect(R(9.5, 1, 3.4, 8), 1.7, 1.7)
    s.addRoundRect(R(13, 3, 3.4, 7), 1.7, 1.7)
    P["grab"] = s

    def arrowhead(path, x, y, dx, dy, size=4.5):
        px, py = -dy, dx
        path.moveTo(x + dx * size, y + dy * size)
        path.lineTo(x + px * size * 0.8, y + py * size * 0.8)
        path.lineTo(x - px * size * 0.8, y - py * size * 0.8)
        path.close()

    s = skia.Path()  # four-way move
    s.addRect(R(-1.1, -7, 2.2, 14))
    s.addRect(R(-7, -1.1, 14, 2.2))
    arrowhead(s, 0, -7, 0, -1); arrowhead(s, 0, 7, 0, 1); arrowhead(s, -7, 0, -1, 0); arrowhead(s, 7, 0, 1, 0)
    P["move"] = s
    s = skia.Path()  # rotate
    s.addArc(R(-8, -8, 16, 16), 40, 270)
    P["rotate_arc"] = s
    t = skia.Path()
    arrowhead(t, 6.0, 4.8, 0.65, 0.76, 5.2)
    P["rotate"] = t
    s = skia.Path()  # scale
    s.moveTo(-7, 7); s.lineTo(7, -7)
    P["scale_line"] = s
    t = skia.Path()
    arrowhead(t, 8, -8, 0.707, -0.707, 5.0); arrowhead(t, -8, 8, -0.707, 0.707, 5.0)
    P["scale"] = t
    P["cross"] = None
    s = skia.Path()  # knife
    s.moveTo(0, 18); s.lineTo(4, 14); s.lineTo(18, 0); s.lineTo(14.5, 12); s.lineTo(7, 17); s.close()
    P["knife"] = s
    P["brush"] = None
    s = skia.Path()  # eyedropper
    s.moveTo(0, 19); s.lineTo(2, 12); s.lineTo(11, 3.5); s.lineTo(15.5, 8); s.lineTo(7, 16.5); s.close()
    s.addCircle(14.5, 3.5, 3.6)
    P["eyedrop"] = s
    P["ibeam"] = None
    s = skia.Path()  # horizontal resize
    s.addRect(R(-7, -1, 14, 2))
    arrowhead(s, -7, 0, -1, 0); arrowhead(s, 7, 0, 1, 0)
    P["hresize"] = s
    s = skia.Path()  # paint roller-style brush
    s.moveTo(0, 20); s.cubicTo(4, 20, 5.5, 18, 5.5, 15); s.lineTo(14, 3)
    s.cubicTo(15.5, 0.8, 19, 3.8, 17.5, 5.8); s.lineTo(9, 18)
    s.cubicTo(7, 21, 2, 22, 0, 20); s.close()
    P["paint"] = s

    # ---- pointer styles: other ways of being an arrow
    s = skia.Path()                       # dart: a slim stealth arrowhead
    s.moveTo(0, 0); s.lineTo(4.4, 19.5); s.lineTo(7.2, 11.4); s.lineTo(15.4, 9.2); s.close()
    P["dart"] = s
    s = skia.Path()                       # pixel: stair-stepped, early-computer style
    rows = [1, 2, 3, 4, 5, 6, 7, 8, 9, 6, 4, 3, 2]
    for y_, w_ in enumerate(rows):
        s.addRect(R(0, y_ * 1.6, w_ * 1.6, 1.62))
    for y_, x_ in ((12, 5), (13, 6), (13, 5)):
        s.addRect(R(x_ * 1.6, y_ * 1.6, 1.6, 1.62))
    P["pixel"] = s
    s = skia.Path()                       # bubble: round-shouldered, like a shared-document cursor
    s.moveTo(1.6, 0.9)
    s.cubicTo(0.2, 0.2, -0.5, 1.1, 0.0, 2.6)
    s.lineTo(4.9, 17.0)
    s.cubicTo(5.5, 18.6, 7.3, 18.5, 7.9, 17.0)
    s.lineTo(10.0, 11.2)
    s.lineTo(15.9, 8.9)
    s.cubicTo(17.3, 8.3, 17.3, 6.5, 15.9, 6.0)
    s.close()
    P["bubble"] = s
    s = skia.Path()                       # critter: an actual mouse, nose first along +x
    s.addOval(R(-8.5, -4.8, 15.5, 9.6))
    s.addCircle(-1.5, -5.0, 2.7)
    s.addCircle(-1.5, 5.0, 2.7)
    s.moveTo(7.0, -1.3); s.lineTo(11.4, 0.0); s.lineTo(7.0, 1.3); s.close()
    P["critter"] = s
    s = skia.Path()
    s.moveTo(-8.4, 0)
    s.cubicTo(-12.5, 0.5, -13.5, -3.8, -17.8, -2.2)
    P["critter_tail"] = s
    return P


def draw_cursor(c, kind, x, y, col, scale=1.0, down=False, skin=0, heading=0.0):
    """Vector mouse cursors in the mouse's own colour with a white keyline.
    `skin` only changes the idle/travelling arrow; tool cursors stay readable."""
    paths = _CURS
    c.save()
    c.translate(x, y)
    c.scale(scale, scale)
    white = KEY
    if kind == "arrow" and skin:
        style = SKIN_NAMES[skin]
        if style == "hollow":
            p = paths["arrow"]
            c.drawPath(p, paint(alpha(col, 46)))
            c.drawPath(p, paint(col, style="s", sw=1.5))
        elif style == "pixel":
            p = paths["pixel"]
            c.drawPath(p, paint(skia.ColorBLACK, aa=False, style="s", sw=1.6))
            c.drawPath(p, paint(col, aa=False))
        elif style == "critter":
            c.rotate(math.degrees(heading))
            c.drawPath(paths["critter_tail"], paint(alpha(white, 210), style="s", sw=1.4))
            body = paths["critter"]
            c.drawPath(body, paint(white, style="s", sw=1.5))
            c.drawPath(body, paint(col))
            c.drawCircle(4.0, -1.8, 0.95, paint(skia.ColorBLACK))
            c.drawCircle(4.0, 1.8, 0.95, paint(skia.ColorBLACK))
        else:
            p = paths[style]
            c.save()
            c.translate(0.9, 1.3)
            c.drawPath(p, paint(alpha(skia.ColorBLACK, 90)))
            c.restore()
            c.drawPath(p, paint(white, style="s", sw=1.5))
            c.drawPath(p, paint(col))
        c.restore()
        return
    ink = paint(col)
    key = paint(white, style="s", sw=1.5)
    shadow = paint(alpha(skia.ColorBLACK, 90))
    if kind == "cross":
        c.drawLine(-9, 0, -3, 0, key)
        c.drawLine(3, 0, 9, 0, key)
        c.drawLine(0, -9, 0, -3, key)
        c.drawLine(0, 3, 0, 9, key)
        hl = paint(col, style="s", sw=1.0)
        c.drawCircle(0, 0, 2.2, hl)
    elif kind == "brush":
        c.drawCircle(0, 0, 13, paint(alpha(col, 40)))
        c.drawCircle(0, 0, 13, paint(col, style="s", sw=1.5))
        c.drawCircle(0, 0, 13, paint(alpha(white, 70), style="s", sw=0.6))
        c.drawCircle(0, 0, 1.8, paint(white))
    elif kind == "ibeam":
        c.drawLine(0, -8, 0, 8, key)
        c.drawLine(-3, -9, 3, -9, key)
        c.drawLine(-3, 9, 3, 9, key)
    elif kind in ("rotate", "scale"):
        line = paths["rotate_arc" if kind == "rotate" else "scale_line"]
        for w, cc in ((4.0, white), (2.1, col)):
            c.drawPath(line, paint(cc, style="s", sw=w))
        c.drawPath(paths[kind], key)
        c.drawPath(paths[kind], ink)
    else:
        p = paths[kind]
        c.save()
        c.translate(0.9, 1.3)
        c.drawPath(p, shadow)
        c.restore()
        c.drawPath(p, key)
        c.drawPath(p, ink)
    c.restore()


_CURS = _cursor_paths()


# ----------------------------------------------------------------- icon bits
def icon(c, name, x, y, col, s=1.0):
    """Tiny original glyphs (x,y = centre)."""
    c.save()
    c.translate(x, y)
    c.scale(s, s)
    st = paint(col, style="s", sw=1.25)
    fill = paint(col)
    if name == "mesh":
        p = skia.Path()
        p.moveTo(0, -6); p.lineTo(5.5, 4.4); p.lineTo(-5.5, 4.4); p.close()
        c.drawPath(p, fill)
    elif name == "folder":
        c.drawRoundRect(R(-6, -4.5, 12, 9), 1.5, 1.5, st)
        c.drawLine(-6, -1.5, 6, -1.5, st)
    elif name == "wrench":
        c.drawLine(-4.5, 4.5, 3, -3, paint(col, style="s", sw=2.2))
        c.drawCircle(4, -4, 2.2, st)
    elif name == "sphere":
        c.drawCircle(0, 0, 5.2, st)
        c.drawOval(R(-5.2, -2, 10.4, 4), st)
    elif name == "eye":
        p = skia.Path()
        p.moveTo(-6, 0); p.quadTo(0, -6.5, 6, 0); p.quadTo(0, 6.5, -6, 0)
        c.drawPath(p, st)
        c.drawCircle(0, 0, 1.7, fill)
    elif name == "light":
        c.drawCircle(0, -1, 3.8, st)
        c.drawLine(-1.5, 3.9, 1.5, 3.9, st)
    elif name == "camera":
        c.drawRoundRect(R(-6, -3.8, 8.5, 7.6), 1.5, 1.5, st)
        p = skia.Path()
        p.moveTo(2.8, -1); p.lineTo(6.2, -3.2); p.lineTo(6.2, 3.2); p.lineTo(2.8, 1); p.close()
        c.drawPath(p, fill)
    elif name == "cursor3d":
        c.drawCircle(0, 0, 4.5, st)
    elif name == "move":
        c.drawLine(-6, 0, 6, 0, st); c.drawLine(0, -6, 0, 6, st)
        for dx, dy in ((6, 0), (-6, 0), (0, 6), (0, -6)):
            c.drawCircle(dx, dy, 1.1, fill)
    elif name == "rotate":
        c.drawArc(R(-5.5, -5.5, 11, 11), 30, 290, False, st)
    elif name == "scale":
        c.drawRect(R(-5.5, -1, 6.5, 6.5), st)
        c.drawLine(1, -1, 5.5, -5.5, st); c.drawCircle(5.5, -5.5, 1.1, fill)
    elif name == "transform":
        c.drawRect(R(-4.5, -4.5, 9, 9), st)
        for dx, dy in ((-4.5, -4.5), (4.5, -4.5), (4.5, 4.5), (-4.5, 4.5)):
            c.drawCircle(dx, dy, 1.3, fill)
    elif name == "select":
        p = skia.Path()
        p.moveTo(-4, -6); p.lineTo(-4, 5); p.lineTo(-1, 2.4); p.lineTo(1.3, 6.5); p.lineTo(3.4, 5.5)
        p.lineTo(1.2, 1.5); p.lineTo(5, 1.2); p.close()
        c.drawPath(p, st)
    elif name == "extrude":
        c.drawRect(R(-5, 0, 10, 5), st)
        c.drawLine(0, 0, 0, -6, st); c.drawCircle(0, -6, 1.3, fill)
    elif name == "inset":
        c.drawRect(R(-5.5, -5.5, 11, 11), st); c.drawRect(R(-2.5, -2.5, 5, 5), st)
    elif name == "bevel":
        p = skia.Path()
        p.moveTo(-5.5, 5.5); p.lineTo(-5.5, -1); p.lineTo(-1, -5.5); p.lineTo(5.5, -5.5)
        c.drawPath(p, st)
    elif name == "loop":
        c.drawRect(R(-5.5, -5.5, 11, 11), st); c.drawLine(0, -5.5, 0, 5.5, paint(col, style="s", sw=1.6))
    elif name == "knife":
        c.drawLine(-5.5, 5.5, 5.5, -5.5, paint(col, style="s", sw=1.8))
    elif name == "sculpt":
        c.drawCircle(0, 0, 5.4, st); c.drawCircle(0, 0, 1.4, fill)
    elif name == "render":
        c.drawCircle(0, 0, 5, st)
        for k in range(6):
            a = k * math.tau / 6
            c.drawLine(math.cos(a) * 2.4, math.sin(a) * 2.4, math.cos(a + 1.0) * 5, math.sin(a + 1.0) * 5, st)
    elif name == "dot":
        c.drawCircle(0, 0, 2.2, fill)
    elif name == "tab_obj":
        c.drawRect(R(-4.8, -4.8, 9.6, 9.6), st)
    elif name == "tab_mat":
        c.drawCircle(0, 0, 5.2, st); c.drawCircle(-1.6, -1.6, 1.5, fill)
    elif name == "tab_world":
        c.drawCircle(0, 0, 5.2, st); c.drawLine(-5.2, 0, 5.2, 0, st)
    elif name == "tab_scene":
        c.drawRoundRect(R(-5.5, -4, 11, 8), 1.5, 1.5, st)
    elif name == "tab_data":
        p = skia.Path()
        p.moveTo(0, -5.5); p.lineTo(5, 3.5); p.lineTo(-5, 3.5); p.close()
        c.drawPath(p, st)
    c.restore()


def text(c, s, x, y, font, col):
    c.drawString(s, x, y, font, paint(col))


class Painter:
    """Bound to one plan; called once per frame by a worker process."""

    def __init__(self, pl, data):
        self.pl = pl
        self.d = data
        self.ty = Type()
        self.opt = pl.settings.opt
        set_theme(self.opt["look.theme"])
        colours = [x for x in self.opt["swarm.palette"].split(",") if x] or CURSOR_COLORS
        self.col = [C(colours[i % len(colours)]) for i in range(pl.n_mice)]
        self.trail_max = float(self.opt["swarm.trail"])
        self.cursor_scale = float(self.opt["swarm.cursor_scale"])
        self.glow = float(self.opt["swarm.glow"])
        self.sprites = {}
        self.UW, self.UH = pl.ui_w, pl.ui_h
        self.mod_names = [p["mods"] for p in data.parts]

    def f(self, face, size):
        return self.ty.font(getattr(self.ty, face), size)

    def str_font(self, s, face, size):
        if self.ty.cjk is not None and any(ord(ch) > 0x2E7F for ch in s):
            return self.ty.font(self.ty.cjk, size)
        return self.f(face, size)

    # -------------------------------------------------------------- top bar
    def topbar(self, c, r, t):
        x, y, w, h = r
        if h < 1:
            return
        c.drawRect(R(x, y, w, h), paint(HEAD))
        # our own glyph — a tiny storm of three cursors
        c.save()
        c.translate(13, 8)
        c.scale(0.52, 0.52)
        draw_cursor(c, "arrow", 0, 0, ACCENT)
        draw_cursor(c, "arrow", 12, 6, BLUE)
        draw_cursor(c, "arrow", 4, 12, TEAL)
        c.restore()
        font = self.f("reg", 13)
        fx = 48
        wide = is_wide(w)
        for m in ("File", "Edit", "Render", "Window", "Help") if wide else ():
            text(c, m, fx, 22, font, DIM)
            fx += font.measureText(m) + 17
        tabs = ["Layout", "Modeling", "Sculpting", "UV", "Texture Paint", "Shading", "Animation", "Rendering"]
        T = self.pl.T
        active = 1
        if t >= T["render"][0]:
            active = 7
        elif t >= T["light"][0]:
            active = 5
        elif t >= T["shade"][0]:
            active = 5
        elif self._sculpting(t):
            active = 2
        tx = 300 if wide else 56
        for i, m in enumerate(tabs):
            if not wide and i != active:
                continue
            f2 = self.f("med" if i == active else "reg", 13)
            mw = f2.measureText(m)
            if i == active:
                rr(c, tx - 11, 4, mw + 22, h - 4, PANEL, 6)
                c.drawRect(R(tx - 11, h - 8, mw + 22, 8), paint(PANEL))
            text(c, m, tx, 22, f2, TEXT if i == active else DIM)
            tx += mw + 22

        # ---- swarm telemetry, right aligned
        pl = self.pl
        done = int(np.searchsorted(pl.op_t0, t))
        rate = int(np.searchsorted(pl.op_t0, t) - np.searchsorted(pl.op_t0, t - 0.5)) * 2
        items = [("MICE", "%d" % pl.n_mice, ACCENT), ("OPS", "{:,}".format(done), TEXT),
                 ("OPS/S", "{:,}".format(rate), TEAL), ("SPEED", "×%.3g" % pl.settings.speed, GOLD)]
        if not self.opt["ui.telemetry"]:
            items = []
        fl, fv = self.f("med", 10), self.f("mono_b", 15)
        cx = w - 12
        for label, value, col in reversed(items):
            vw = fv.measureText(value)
            lw = fl.measureText(label)
            text(c, value, cx - vw, 23, fv, col)
            cx -= vw + 6
            text(c, label, cx - lw, 22, fl, DIM)
            cx -= lw + 22

    def _sculpting(self, t):
        pl = self.pl
        i = int(np.searchsorted(pl.op_t0, t))
        lookback = pl.op_stage[max(0, i - 12):i]
        return sum(1 for s in lookback if s == "B") > 6

    # ------------------------------------------------------------ viewports
    def viewport_chrome(self, c, r, t, img, active_name, pl_mode, chrome=True):
        x, y, w, h = r
        rr(c, x, y, w, h, PANEL, 7)
        if w < 8 or h < 8:
            return
        c.save()
        c.clipRRect(skia.RRect.MakeRectXY(R(x, y, w, h), 7, 7), True)
        c.drawImageRect(img, R(x, y, w, h), skia.SamplingOptions(skia.FilterMode.kLinear))

        if chrome:
            # header strip
            c.drawRect(R(x, y, w, 26), paint(alpha(HEAD, 232)))
            f = self.f("reg", 12)
            mode_txt = pl_mode
            fx = x + 9
            mw = f.measureText(mode_txt)
            rr(c, fx, y + 4, mw + 16, 18, FIELD, 4)
            text(c, mode_txt, fx + 8, y + 17, f, TEXT)
            fx += mw + 32
            for m in ("View", "Select", "Add", "Object" if "Object" in pl_mode else "Mesh"):
                text(c, m, fx, y + 17, f, DIM)
                fx += f.measureText(m) + 15
            shading = 1
            if t >= self.pl.T["shade"][0]:
                shading = 2
            if t >= self.pl.T["light"][0]:
                shading = 3
            sx = x + w - 124
            for i in range(4):
                rr(c, sx + i * 24, y + 4, 22, 18, BLUE if i == shading else FIELD, 4)
                g = ("transform", "tab_obj", "tab_mat", "render")[i]
                icon(c, g, sx + 11 + i * 24, y + 13, TEXT if i != shading else skia.ColorWHITE, 0.72)

            # labels
            fs = self.f("reg", 12.5)
            text(c, "User Perspective", x + 56, y + 46, fs, TEXT)
            text(c, "(1) Scene | %s" % active_name, x + 56, y + 63, self.str_font(active_name, "reg", 12.5), DIM)

            # tool shelf
            tools = ["select", "cursor3d", "move", "rotate", "scale", "transform",
                     "extrude", "inset", "bevel", "loop", "knife", "sculpt"]
            shelf_h = TOOL_STEP * len(tools) + 8
            rr(c, x + 6, y + TOOL_Y0 - 4, 33, shelf_h, alpha(SHELF, 245), 6)
            cur = self._tool_at(t)
            for i, name in enumerate(tools):
                ty = y + TOOL_Y0 + 16 + TOOL_STEP * i - 4
                if i == cur:
                    rr(c, x + 9, ty - 13, 27, 26, BLUE, 5)
                icon(c, name, x + 22.5, ty, SELECT_TEXT if i == cur else ICON, 1.05)

            self.nav_gizmo(c, x + w - 64, y + 92, t)
        c.restore()

    def _tool_at(self, t):
        pl = self.pl
        i = int(np.searchsorted(pl.op_t0, t)) - 1
        if i < 0:
            return 0
        k = pl.op_kind[i]
        if k == "edit":
            if pl.op_stage[i] == "B":
                return 11
            label = pl.op_label[i]
            for prefix, tool in (("Extrude", 6), ("Inset", 7), ("Bevel", 8), ("Loop", 9), ("Knife", 10), ("Rotate", 3), ("Resize", 4)):
                if label.startswith(prefix):
                    return tool
            return 2
        return {"grab": 2, "rot": 3, "scale": 4, "add": 0, "assign": 1, "paint": 11,
                "tweak": 5, "mod": 5, "pick": 0, "node": 5, "tile": 0, "cut": 9}.get(k, 0)

    def nav_gizmo(self, c, cx, cy, t):
        from .plan_finish import cam_at
        cam = cam_at(self.pl, t)
        r, u, f = cam.basis()
        axes = [("X", np.array([1.0, 0, 0]), C("e8495c")), ("Y", np.array([0, 1.0, 0]), C("7fc74a")),
                ("Z", np.array([0, 0, 1.0]), C("4a90f0"))]
        items = []
        for name, a, col in axes:
            for sign in (1, -1):
                items.append((float(np.dot(a * sign, f)) * -1, sign, name, a * sign, col))
        items.sort(key=lambda z: z[0])
        font = self.f("bold", 10.5)
        for depth, sign, name, a, col in items:
            sx = cx + float(np.dot(a, r)) * 24
            sy = cy - float(np.dot(a, u)) * 24
            if sign > 0:
                c.drawLine(cx, cy, sx, sy, paint(col, style="s", sw=2.0))
                c.drawCircle(sx, sy, 8.5, paint(col))
                text(c, name, sx - font.measureText(name) / 2, sy + 3.8, font, skia.ColorBLACK)
            else:
                c.drawCircle(sx, sy, 6.5, paint(alpha(col, 110)))

    def ortho(self, c, r, img, label):
        x, y, w, h = r
        if w < 6 or h < 6:
            return
        rr(c, x, y, w, h, PANEL, 7)
        c.save()
        c.clipRRect(skia.RRect.MakeRectXY(R(x, y, w, h), 7, 7), True)
        c.drawImageRect(img, R(x, y, w, h), skia.SamplingOptions(skia.FilterMode.kLinear))
        text(c, label, x + 10, y + 20, self.f("reg", 11.5), DIM)
        c.restore()

    # --------------------------------------------------------------- outliner
    def outliner(self, c, r, t, active):
        x, y, w, h = r
        if w < 10 or h < 30:
            return
        rr(c, x, y, w, h, PANEL, 7)
        c.save()
        c.clipRect(R(x, y, w, h))
        c.drawRect(R(x, y, w, 26), paint(HEAD))
        text(c, "Outliner", x + 28, y + 17, self.f("med", 12), DIM)
        icon(c, "folder", x + 14, y + 13, DIM, 0.8)

        pl, parts = self.pl, self.d.parts
        n_live = int(np.searchsorted(pl.row_t, t, side="right"))
        rows_cap = int((h - 54) / ROW_H)
        scroll = max(0, n_live - rows_cap)
        ry = y + 50
        c.drawString("Scene Collection", x + 28, ry, self.f("med", 12), paint(TEXT))
        icon(c, "folder", x + 14, ry - 4, TEXT, 0.8)
        ry += ROW_H
        for k in range(scroll, n_live):
            i = int(pl.row_order[k])
            p = parts[i]
            row_y = ry + (k - scroll) * ROW_H
            if i == active:
                c.drawRect(R(x + 3, row_y - 15, w - 6, ROW_H), paint(SELECT))
            elif (k - scroll) % 2 == 0:
                c.drawRect(R(x + 3, row_y - 15, w - 6, ROW_H), paint(ROW_ALT))
            age = t - float(pl.row_t[k])
            icon(c, "mesh", x + 30, row_y - 4, ACCENT if age < 0.35 else C("e8a35e"), 0.8)
            name = p["name"]
            f2 = self.str_font(name, "reg", 12)
            text(c, name[:30], x + 46, row_y, f2, SELECT_TEXT if i == active else TEXT)
            if p["mods"]:
                icon(c, "wrench", x + w - 46, row_y - 4, DIM, 0.75)
            icon(c, "eye", x + w - 22, row_y - 4, FAINT, 0.8)
        c.restore()

    # ------------------------------------------------------------- properties
    def field(self, c, x, y, w, label, value, hot=False):
        h = 21
        text(c, label, x - 64, y + 15, self.f("reg", 11.5), DIM)
        rr(c, x, y, w, h, FIELD_HI if hot else FIELD, 4)
        vf = self.f("mono", 11.5)
        text(c, value, x + w / 2 - vf.measureText(value) / 2, y + 15, vf, TEXT)

    def slider(self, c, x, y, w, label, v, col=BLUE):
        h = 21
        text(c, label, x - 64, y + 15, self.f("reg", 11.5), DIM)
        rr(c, x, y, w, h, FIELD, 4)
        c.save()
        c.clipRRect(skia.RRect.MakeRectXY(R(x, y, w, h), 4, 4), True)
        c.drawRect(R(x, y, w * max(0.0, min(1.0, v)), h), paint(col))
        c.restore()
        s = "%.3f" % v
        vf = self.f("mono", 11.5)
        text(c, s, x + w / 2 - vf.measureText(s) / 2, y + 15, vf, TEXT)

    def section(self, c, x, y, w, title):
        c.drawRect(R(x, y, w, 22), paint(HEAD))
        p = skia.Path()
        p.moveTo(x + 9, y + 8); p.lineTo(x + 17, y + 8); p.lineTo(x + 13, y + 14); p.close()
        c.drawPath(p, paint(DIM))
        text(c, title, x + 24, y + 15.5, self.f("med", 12), TEXT)

    def properties(self, c, r, t, active, xform):
        x, y, w, h = r
        if w < 10 or h < 30:
            return
        rr(c, x, y, w, h, PANEL, 7)
        c.save()
        c.clipRect(R(x, y, w, h))
        c.drawRect(R(x, y, w, 26), paint(HEAD))
        text(c, "Properties", x + 42, y + 17, self.f("med", 12), DIM)
        tabs = ["tab_scene", "tab_world", "tab_obj", "wrench", "tab_data", "tab_mat"]
        rr(c, x + 4, y + 30, 26, h - 36, STRIP, 5)
        hot = 5 if t >= self.pl.T["shade"][0] else 2
        for i, name in enumerate(tabs):
            yy = y + 48 + i * 27
            if i == hot:
                rr(c, x + 7, yy - 11, 20, 22, BLUE, 4)
            icon(c, name, x + 17, yy, TEXT if i == hot else DIM, 0.85)

        px = x + 36
        pw = w - 44
        field_x = px + 72
        fw = pw - 80
        parts = self.d.parts
        p = parts[active] if active >= 0 else None
        name = p["name"] if p else "—"
        fyy = y + 34
        rr(c, px, fyy, pw, 22, FIELD, 4)
        icon(c, "mesh", px + 13, fyy + 11, ACCENT, 0.72)
        text(c, name[:34], px + 25, fyy + 15, self.str_font(name, "reg", 12), TEXT)

        sy = y + 64
        self.section(c, px, sy, pw, "Transform")
        y0 = y + PROP_FIRST + 16
        loc, euler, scale = xform
        for k, ax in enumerate("XYZ"):
            self.field(c, field_x, y0 + k * PROP_ROW, fw, "Location %s" % ax if k == 0 else ax,
                       "%.3f m" % loc[k], hot=(int(t * 8) + k) % 5 == 0)
        y1 = y0 + 3 * PROP_ROW + 4
        for k, ax in enumerate("XYZ"):
            self.field(c, field_x, y1 + k * PROP_ROW, fw, "Rotation %s" % ax if k == 0 else ax,
                       "%.1f°" % euler[k])
        y2 = y1 + 3 * PROP_ROW + 4
        for k, ax in enumerate("XYZ"):
            self.field(c, field_x, y2 + k * PROP_ROW, fw, "Scale %s" % ax if k == 0 else ax,
                       "%.3f" % scale[k])

        ym = y2 + 3 * PROP_ROW + 6
        self.section(c, px, ym, pw, "Modifiers")
        mods = p["mods"][:3] if p else []
        yy = ym + 27
        if not mods:
            text(c, "No modifiers", px + 12, yy + 13, self.f("reg", 11.5), FAINT)
        for m in mods:
            rr(c, px, yy, pw, 21, MODROW, 4)
            icon(c, "wrench", px + 13, yy + 10.5, TEAL, 0.72)
            text(c, m["name"], px + 26, yy + 15, self.f("med", 11.5), TEXT)
            text(c, m["type"].title(), px + pw - 76, yy + 15, self.f("reg", 11), DIM)
            yy += 24
        yy = max(yy, ym + 52)

        self.section(c, px, yy + 2, pw, "Material")
        yy += 30
        mid = -1
        if p and p["mats"]:
            mid = p["mats"][0]
        if mid >= 0:
            m = self.d.materials[mid]
            rr(c, px, yy, pw, 22, FIELD, 4)
            icon(c, "tab_mat", px + 13, yy + 11, ROSE, 0.75)
            text(c, m["name"][:30], px + 25, yy + 15, self.str_font(m["name"], "reg", 12), TEXT)
            yy += 27
            text(c, "Base Color", field_x - 64, yy + 15, self.f("reg", 11.5), DIM)
            col = self._srgb(m["color"])
            rr(c, field_x, yy, fw, 21, col, 4)
            yy += 25
            self.slider(c, field_x, yy, fw, "Metallic", m["metal"], C("6b83a8"))
            yy += 25
            self.slider(c, field_x, yy, fw, "Roughness", m["rough"])
        else:
            text(c, "No material", px + 12, yy + 14, self.f("reg", 11.5), FAINT)
        c.restore()

    @staticmethod
    def _srgb(rgb):
        def to(x):
            x = max(0.0, min(1.0, x))
            return int((12.92 * x if x < 0.0031308 else 1.055 * x ** (1 / 2.4) - 0.055) * 255 + 0.5)
        return skia.Color(to(rgb[0]), to(rgb[1]), to(rgb[2]))

    # ----------------------------------------------------------------- info
    def info_log(self, c, r, t):
        x, y, w, h = r
        if w < 10 or h < 10:
            return
        rr(c, x, y, w, h, PANEL, 7)
        c.save()
        c.clipRect(R(x, y, w, h))
        c.drawRect(R(x, y, w, 26), paint(HEAD))
        text(c, "Info", x + 12, y + 17, self.f("med", 12), DIM)
        pl = self.pl
        i = int(np.searchsorted(pl.op_t0, t))
        font = self.f("mono", 11)
        lines = max(1, int((h - 36) / 15.5))
        yy = y + h - 10
        k = i - 1
        count = 0
        while k >= 0 and count < lines:
            a = pl.op_api[k]
            if a:
                age = t - float(pl.op_t0[k])
                col = TEAL if age < 0.14 else (LOGALT if count % 2 else DIM)
                text(c, a if a.startswith("bpy.") else "bpy.ops." + a, x + 10, yy, font, col)
                yy -= 15.5
                count += 1
            k -= 1
        c.restore()

    # ---------------------------------------------------------- node editor
    def nodes(self, c, r, t):
        x, y, w, h = r
        if w < 10 or h < 10:
            return
        rr(c, x, y, w, h, PANEL, 7)
        pl = self.pl
        c.save()
        c.clipRRect(skia.RRect.MakeRectXY(R(x, y, w, h), 7, 7), True)
        step = 26
        dot = paint(DOTS)
        for gx in range(int(x) + 8, int(x + w), step):
            for gy in range(int(y) + 30, int(y + h), step):
                c.drawCircle(gx, gy, 0.9, dot)
        c.drawRect(R(x, y, w, 26), paint(HEAD))
        mw = None
        for t0, t1, m in pl.mat_windows:
            if t0 <= t < t1 or mw is None:
                mw = (t0, t1, m)
                if t0 <= t < t1:
                    break
        if mw is None:
            c.restore()
            return
        t0, t1, mid = mw
        mat = self.d.materials[mid]
        name = mat["name"]
        text(c, "Shader Editor", x + 12, y + 17, self.f("med", 12), DIM)
        rr(c, x + 112, y + 4, 8 + 8 * min(len(name), 28) + 30, 18, FIELD, 4)
        icon(c, "tab_mat", x + 124, y + 13, ROSE, 0.7)
        text(c, name[:28], x + 136, y + 17, self.str_font(name, "reg", 12), TEXT)
        g = mat.get("graph")
        if not g or not g["nodes"]:
            c.restore()
            return
        nodes = g["nodes"]
        minx = min(n["x"] for n in nodes)
        maxx = max(n["x"] + n["w"] for n in nodes)
        ys = [n["y"] for n in nodes]
        miny, maxy = min(ys) - 280, max(ys)
        pad = 36
        sc = min((w - pad * 2) / max(maxx - minx, 1), (h - 50) / max(maxy - miny, 1), 0.9)
        ox = x + w / 2 - (minx + maxx) * 0.5 * sc
        oy = y + 34 + (maxy) * sc + 8

        def sx(n):
            return ox + n["x"] * sc

        def sy(n):
            return oy - n["y"] * sc

        CAT = {"Bsdf": C("2d6a4f"), "Output": C("7a2636"), "Tex": C("85622b"), "ValToRGB": C("4a4e8a"),
               "Mix": C("4a4e8a"), "Mapping": C("3a5a7a"), "TexCoord": C("7a4a26"), "Emission": C("2d6a4f"),
               "RGB": C("7a4a26"), "Value": C("7a4a26"), "Bump": C("3a5a7a"), "Vertex": C("7a4a26"),
               "Attribute": C("7a4a26")}
        nw = len(nodes)
        wnd = max(t1 - t0, 1e-3)
        appear = lambda j: t0 + wnd * (j / nw) * 0.62
        socket_y = {}
        for j, n in enumerate(nodes):
            nx, ny = sx(n), sy(n)
            bw = n["w"] * sc
            yy = ny + 26 * sc
            for s in n["out"]:
                socket_y[(j, "o", s["n"])] = (nx + bw, yy)
                yy += 17 * sc
            yy += 4 * sc
            for s in n["in"]:
                socket_y[(j, "i", s["n"])] = (nx, yy)
                yy += 17 * sc
        for j, L in enumerate(g["links"]):
            a, b = L["a"], L["b"]
            pa = socket_y.get((a, "o", L["as"]))
            pb = socket_y.get((b, "i", L["bs"]))
            if pa is None or pb is None:
                continue
            ta = max(appear(a), appear(b)) + wnd * 0.12
            u = (t - ta) / (wnd * 0.1)
            if u <= 0:
                continue
            u = min(1.0, u)
            ex, ey = pa[0] + (pb[0] - pa[0]) * u, pa[1] + (pb[1] - pa[1]) * u
            path = skia.Path()
            path.moveTo(pa[0], pa[1])
            bend = max(24, abs(ex - pa[0]) * 0.45)
            path.cubicTo(pa[0] + bend, pa[1], ex - bend, ey, ex, ey)
            c.drawPath(path, paint(alpha(C("d9c37a"), 70), style="s", sw=4.2))
            c.drawPath(path, paint(C("e9d48d"), style="s", sw=1.5))
        tf = self.f("med", max(9.5, 12 * sc))
        lf = self.f("reg", max(8.5, 10.5 * sc))
        for j, n in enumerate(nodes):
            u = (t - appear(j)) / (wnd * 0.1)
            if u <= 0:
                continue
            nx, ny = sx(n), sy(n)
            bw = n["w"] * sc
            n_rows = len(n["in"]) + len(n["out"])
            bh = 30 * sc + n_rows * 17 * sc + 6 * sc
            pop = min(1.0, u)
            ease = 1.0 - (1.0 - pop) ** 3
            c.save()
            c.translate(nx + bw / 2, ny + bh / 2)
            c.scale(ease, ease)
            c.translate(-(nx + bw / 2), -(ny + bh / 2))
            shadow = paint(alpha(skia.ColorBLACK, 120))
            shadow.setMaskFilter(skia.MaskFilter.MakeBlur(skia.kNormal_BlurStyle, 7))
            c.drawRoundRect(R(nx + 2, ny + 3, bw, bh), 6, 6, shadow)
            rr(c, nx, ny, bw, bh, NODE, 6)
            idn = n["id"]
            col = C("4a505c")
            for key, value in CAT.items():
                if key in idn:
                    col = value
                    break
            c.save()
            c.clipRRect(skia.RRect.MakeRectXY(R(nx, ny, bw, bh), 6, 6), True)
            c.drawRect(R(nx, ny, bw, 22 * sc), paint(col))
            c.restore()
            text(c, n["label"][:24], nx + 7, ny + 15 * sc, tf, skia.ColorWHITE)
            yy = ny + 26 * sc
            type_col = {"RGBA": C("c7c729"), "VALUE": C("a1a1a1"), "VECTOR": C("6363c7"), "SHADER": C("63c763")}
            for s in n["out"]:
                c.drawCircle(nx + bw, yy, 3.1, paint(type_col.get(s["t"], C("cfd3db"))))
                tw = lf.measureText(s["n"][:18])
                text(c, s["n"][:18], nx + bw - tw - 7, yy + 3.5, lf, TEXT)
                yy += 17 * sc
            yy += 4 * sc
            for s in n["in"]:
                c.drawCircle(nx, yy, 3.1, paint(type_col.get(s["t"], C("cfd3db"))))
                label = s["n"][:15]
                v = s.get("v")
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    label += "  %.2f" % v
                text(c, label, nx + 7, yy + 3.5, lf, DIM)
                yy += 17 * sc
            c.restore()
        c.restore()

    # ------------------------------------------------------------- timeline
    def timeline(self, c, r, t):
        x, y, w, h = r
        if h < 8:
            return
        rr(c, x, y, w, h, PANEL, 7)
        pl = self.pl
        c.save()
        c.clipRRect(skia.RRect.MakeRectXY(R(x, y, w, h), 7, 7), True)
        hist = pl.hist
        peak = max(1, int(hist.max()))
        bx0, bw = x + 8, w - 16
        ph = h - 12
        for k, v in enumerate(hist):
            hh = (v / peak) ** 0.55 * ph
            c.drawRect(R(bx0 + bw * k / len(hist), y + h - 6 - hh, max(1.2, bw / len(hist) - 0.8), hh),
                       paint(HIST if pl.show0 * k / len(hist) > t else BLUE))
        names = [("model", "MODEL", ACCENT), ("shade", "SHADE", ROSE), ("light", "LIGHT", GOLD), ("render", "RENDER", TEAL)]
        for key, label, col in names:
            a, b = pl.T[key]
            xa = bx0 + bw * a / pl.show0
            xb = bx0 + bw * b / pl.show0
            c.drawRect(R(xa, y + 3, max(0.0, xb - xa - 2), 2.5), paint(col))
            text(c, label, xa + 2, y + 15, self.f("bold", 8.5), col)
        px = bx0 + bw * min(1.0, t / pl.show0)
        c.drawLine(px, y, px, y + h, paint(ACCENT, style="s", sw=1.6))
        c.restore()

    def statusbar(self, c, r, t, stats):
        x, y, w, h = r
        if h < 2:
            return
        c.drawRect(R(x, y, w, h), paint(HEAD))
        f = self.f("reg", 11.5)
        text(c, "Mousestorm — %s" % self.pl.source[:60], 12, y + 16, self.str_font(self.pl.source, "reg", 11.5), DIM)
        s = stats
        rt = f.measureText(s)
        text(c, s, w - rt - 14, y + 16, f, DIM)

    # ---------------------------------------------------- swarm & overlays
    def trails_and_cursors(self, c, t, xscale):
        pl = self.pl
        idx = t * BAKE_HZ
        i0 = int(idx)
        fr = idx - i0
        n = pl.n_mice
        i0 = max(0, min(i0, pl.bake_n - 2))
        pos = pl.cpos[:, i0] * (1.0 - fr) + pl.cpos[:, i0 + 1] * fr
        icons = pl.cicon[:, i0]
        downs = pl.cdown[:, i0]

        # trails under everything else
        if i0 >= 2 and self.trail_max > 0:
            window = pl.cpos[:, max(0, i0 - 16):i0 + 1:2]
            for k in range(n):
                pts = window[k]
                seg = np.hypot(*(pts[1:] - pts[:-1]).T)
                if len(pts) < 2 or float(seg.sum()) < 3.0:
                    continue
                # measured back from the head, capped: a tail, not a laser
                back = np.cumsum(seg[::-1])[::-1]
                col = self.col[k]
                for j in range(len(seg)):
                    if back[j] > self.trail_max:
                        continue
                    a = 1.0 - float(back[j]) / self.trail_max
                    c.drawLine(float(pts[j, 0]), float(pts[j, 1]), float(pts[j + 1, 0]), float(pts[j + 1, 1]),
                               paint(alpha(col, 40 + 130 * a), style="s", sw=0.7 + 2.2 * a))

        # press ripples
        rip = pl.agenda_ripples
        if len(rip) and self.opt["swarm.ripples"]:
            a = int(np.searchsorted(rip[:, 0], t - 0.26))
            b = int(np.searchsorted(rip[:, 0], t))
            stride = max(1, (b - a) // 60)
            for q in range(a, b, stride):
                t0, cur = float(rip[q, 0]), int(rip[q, 1])
                age = (t - t0) / 0.26
                j = int(t0 * BAKE_HZ)
                if j >= pl.bake_n:
                    continue
                x, y = float(pl.cpos[cur, j, 0]), float(pl.cpos[cur, j, 1])
                c.drawCircle(x, y, 3 + 19 * age, paint(alpha(self.col[cur], 190 * (1 - age)), style="s", sw=1.5))

        skins = pl.cskin
        tag_font = self.f("med", 10.5)
        for k in range(n):
            x, y = float(pos[k, 0]), float(pos[k, 1])
            if x < -40 or y < -40 or x > self.UW + 40 or y > self.UH + 40:
                continue
            skin = int(skins[k])
            heading = float(pl.cheading[k][i0]) if skin == SKIN_CRITTER else 0.0
            if self.glow > 0:
                self.halo(c, x, y, self.col[k])
            base = self.cursor_scale * (0.95 if downs[k] else 1.0)
            draw_cursor(c, ICONS[int(icons[k])], x, y, self.col[k], base, skin=skin, heading=heading)
            name = pl.crew.get(k)
            if name is not None:
                tw = tag_font.measureText(name)
                bx, by = min(x + 13 * self.cursor_scale, self.UW - tw - 16), min(y + 21 * self.cursor_scale, self.UH - 22)
                rr(c, bx, by, tw + 12, 17, alpha(self.col[k], 232), 8.5)
                text(c, name, bx + 6, by + 12.4, tag_font, C("0c0e13"))

    def halo(self, c, x, y, col):
        """A soft additive glow under a mouse, from a sprite cached per colour."""
        img = self.sprites.get(col)
        if img is None:
            size = 72
            arr = np.zeros((size, size, 4), np.uint8)
            info = skia.ImageInfo.Make(size, size, skia.kRGBA_8888_ColorType, skia.kPremul_AlphaType)
            surf = skia.Surface.MakeRasterDirect(info, arr)
            shader = skia.GradientShader.MakeRadial(skia.Point(size / 2, size / 2), size / 2, [alpha(col, 150), alpha(col, 0)])
            surf.getCanvas().drawPaint(skia.Paint(Shader=shader))
            surf.flushAndSubmit()
            img = skia.Image.fromarray(arr.copy(), colorType=skia.kRGBA_8888_ColorType)
            self.sprites[col] = img
        p = skia.Paint(BlendMode=skia.BlendMode.kPlus)
        p.setAlphaf(min(1.0, self.glow))
        s = 36 * self.cursor_scale
        c.drawImageRect(img, R(x + 4 - s, y + 6 - s, s * 2, s * 2), skia.SamplingOptions(skia.FilterMode.kLinear), p)

    def display_name(self):
        return self.opt["title.text"] or os.path.splitext(self.pl.source)[0]

    def title_card(self, c, t):
        """Opens the film: which file this is, while the swarm pours in."""
        end = min(2.2, self.pl.T["model"][1] * 0.5)
        if t >= end or not self.opt["title.show"]:
            return
        a = min(1.0, t / 0.2) * min(1.0, (end - t) / 0.55)
        name = self.display_name()
        size = 68
        while size > 26:
            font = self.str_font(name, "black", size)
            if font.measureText(name) <= self.UW - 120:
                break
            size -= 4
        d = self.d
        sub = self.opt["title.subtitle"] or "{:,} objects  ·  {:,} polygons  ·  {} mice".format(
            len(d.parts), d.doc["totals"]["polys"], self.pl.n_mice)
        fs = self.str_font(sub, "mono", 18)
        tw, sw = font.measureText(name), fs.measureText(sub)
        cx, cy = self.UW / 2, self.UH * 0.44
        box_w = max(tw, sw) + 90
        rr(c, cx - box_w / 2, cy - size * 1.15, box_w, size * 1.15 + 66, alpha(CHIP, 214 * a), 16)
        text(c, name, cx - tw / 2, cy, font, alpha(CHIP_TEXT, 255 * a))
        text(c, sub, cx - sw / 2, cy + 38, fs, alpha(ACCENT, 255 * a))

    def chips(self, c, t, rects, active_ops):
        pl = self.pl
        if not self.opt["swarm.chips"]:
            return
        tf, vf = self.f("med", 11), self.f("mono", 11)
        count = 0
        limit = max(4, int(rects["view"][2] / 190))          # enough to read, never a wall of labels
        for q in active_ops:
            if not pl.op_show[q]:
                continue
            chip = pl.op_chip[q]
            if chip is None:
                continue
            cur = int(pl.op_cursor[q])
            j = int(t * BAKE_HZ)
            if j >= pl.bake_n:
                continue
            x, y = float(pl.cpos[cur, j, 0]), float(pl.cpos[cur, j, 1])
            u = (t - float(pl.op_t0[q])) / max(float(pl.op_t1[q] - pl.op_t0[q]), 1e-4)
            label = pl.op_label[q]
            vl, value = chip
            if isinstance(value, np.ndarray):
                axis = ["X", "Y", "Z"][int(u * 3) % 3]
                val = "%s %.3f" % (axis, float(value[int(u * 3) % 3]) * u)
            elif isinstance(value, float):
                val = "%.3f" % (value * u) if vl == label else "%s %.3f" % (vl, value * u)
            elif isinstance(value, int):
                val = "%s %d" % (vl, value)
            else:
                val = "" if str(value) in label else str(value)
            w1, w2 = tf.measureText(label), vf.measureText(val)
            bx, by = x + 15, y + 19
            w = w1 + w2 + 22
            bx = min(bx, self.UW - w - 6)
            by = min(by, self.UH - 34)
            rr(c, bx, by, w, 21, alpha(CHIP, 214), 5)
            c.drawRoundRect(R(bx, by, w, 21), 5, 5, paint(alpha(self.col[cur], 150), style="s", sw=1.0))
            text(c, label, bx + 8, by + 15, tf, CHIP_TEXT)
            text(c, val, bx + 14 + w1, by + 15, vf, GOLD)
            count += 1
            if count >= limit:
                break
