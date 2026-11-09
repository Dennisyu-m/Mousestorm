# SPDX-License-Identifier: GPL-3.0-or-later
"""Workspace geometry in logical units, as a pure function of the two
transition parameters. Shared by the choreographer (where a mouse should aim)
and the renderer (where the widget is drawn), so they can never disagree.

The logical canvas keeps its short side at 1080 units whatever the output
size. Landscape output gets the classic wide workspace; portrait and square
output get a stacked layout that fills a phone screen properly.
"""
from dataclasses import dataclass

UI_W, UI_H = 1920, 1080
WIDE_MIN = 1500.0    # below this logical width the workspace is stacked


def canvas(width, height):
    """Logical canvas size for an output size."""
    if width >= height:
        return 1080.0 * width / height, 1080.0
    return 1080.0, 1080.0 * height / width


def is_wide(w):
    return w >= WIDE_MIN


@dataclass
class Fx:
    """Transition state: nodes = 0 ortho row, 1 node-editor; full = 0 workspace,
    1 viewport fills the frame."""
    nodes: float = 0.0
    full: float = 0.0


def lerp(a, b, t):
    return a + (b - a) * t


def rect(x, y, w, h):
    return (float(x), float(y), float(w), float(h))


def layout_at(fx: Fx, W=UI_W, H=UI_H, mode="full"):
    """mode: full workspace; focus keeps only the viewport and timeline; clean
    is driven by Fx.full = 1 from the first frame."""
    return _wide(fx, W, H, mode) if is_wide(W) else _stacked(fx, W, H, mode)


def _wide(fx, W, H, mode):
    n, f = fx.nodes, fx.full
    side = mode == "full"
    top_h = lerp(34, 0, f)
    status_h = lerp(23, 0, f)
    right_w = lerp(342, 0, f) if side else 0.0
    tl_h = lerp(46, 0, f)
    gap = lerp(3, 0, f)
    body_y = top_h + gap * (1 - f)
    body_b = H - status_h - gap * (1 - f)
    body_x = gap
    body_r = W - gap * (1 - f)
    main_r = body_r - right_w - (gap if f < 1 else 0)
    bot_h = lerp(lerp(258, 400, n), 0, f) if side else 0.0
    view_b = body_b - tl_h - gap - bot_h - (gap if side else 0)
    if f >= 1:
        view_b = H
    r = {}
    r["top"] = rect(0, 0, W, top_h)
    r["status"] = rect(0, H - status_h, W, status_h)
    r["view"] = rect(body_x, body_y, main_r - body_x, view_b - body_y)
    bot_y = view_b + gap
    r["timeline"] = rect(body_x, body_b - tl_h, main_r - body_x, tl_h)
    w = main_r - body_x
    # three orthos give way to the node editor as shading begins
    ortho_w = lerp(278, 0, n)
    info_w = w - (ortho_w * 3 + gap * 3)
    for k in range(3):
        r["ortho%d" % k] = rect(body_x + (ortho_w + gap) * k, bot_y, ortho_w, bot_h)
    r["info"] = rect(body_x + (ortho_w + gap) * 3, bot_y, info_w, bot_h)
    rx = main_r + gap
    r["outliner"] = rect(rx, body_y, right_w, 372)
    r["props"] = rect(rx, body_y + 372 + gap, right_w, body_b - (body_y + 372 + gap))
    return r


def _stacked(fx, W, H, mode):
    """Viewport on top; ortho/node row, then outliner + properties below it.
    A square frame has no room for the side panels and drops them."""
    n, f = fx.nodes, fx.full
    top_h = lerp(34, 0, f)
    status_h = lerp(23, 0, f)
    tl_h = lerp(46, 0, f)
    gap = lerp(3, 0, f)
    side = mode == "full"
    side_full = min(H * 0.26, 520.0) if H >= 1400 and side else 0.0
    side_h = lerp(side_full, 0, f)
    bot_h = lerp(lerp(min(250.0, H * 0.18), min(430.0, H * 0.28), n), 0, f) if side else 0.0
    body_y = top_h + gap
    body_b = H - status_h - gap
    view_b = body_b - tl_h - gap - side_h - (gap if side_h > 0 else 0) - bot_h - (gap if side else 0)
    if f >= 1:
        body_y, view_b = 0.0, H
    w = W - gap * 2
    r = {}
    r["top"] = rect(0, 0, W, top_h)
    r["status"] = rect(0, H - status_h, W, status_h)
    r["view"] = rect(gap, body_y, w, view_b - body_y)
    bot_y = view_b + gap
    ortho_w = lerp((w - gap * 3) / 4.0, 0, n)
    info_w = w - (ortho_w * 3 + gap * 3)
    for k in range(3):
        r["ortho%d" % k] = rect(gap + (ortho_w + gap) * k, bot_y, ortho_w, bot_h)
    r["info"] = rect(gap + (ortho_w + gap) * 3, bot_y, info_w, bot_h)
    side_y = bot_y + bot_h + gap
    col = (w - gap) / 2.0
    r["outliner"] = rect(gap, side_y, col, side_h)
    r["props"] = rect(gap * 2 + col, side_y, col, side_h)
    r["timeline"] = rect(gap, body_b - tl_h, w, tl_h)
    return r


ROW_H = 21.0
PROP_FIRST = 76.0
PROP_ROW = 25.0
TOOL_Y0 = 52.0
TOOL_STEP = 33.0


def anchor_xy(name, rects, t=0.0, seed=0, phase=0):
    """Screen-space anchor of a named widget."""
    v = rects["view"]
    p = rects["props"]
    o = rects["outliner"]
    head = rects["top"]
    # with no interface on screen there is only the model left to work on
    bare = head[3] < 1.0
    panel_gone = ((name.startswith("props") and min(p[2], p[3]) < 40) or (name == "outliner" and min(o[2], o[3]) < 40)
                  or (name in ("info", "nodes") and min(rects["info"][2], rects["info"][3]) < 40))
    if bare or (panel_gone and bare):
        return v[0] + v[2] * (0.1 + 0.8 * ((seed * 7) % 97) / 97.0), v[1] + v[3] * (0.12 + 0.76 * ((seed * 11) % 89) / 89.0)
    # a frame with no side panels: those mice work the shelf instead
    if panel_gone:
        return v[0] + 22, v[1] + TOOL_Y0 + 16 + TOOL_STEP * (seed % 12)
    if name == "menu.add":
        return v[0] + 178 + seed % 7, v[1] + 13
    if name.startswith("tool."):
        k = int(name.split(".")[1])
        return v[0] + 22, v[1] + TOOL_Y0 + 16 + TOOL_STEP * k
    if name == "props.tab":
        return p[0] + 15, p[1] + 60 + (seed % 9) * 28
    if name.startswith("props.field."):
        k = int(name.split(".")[2])
        return p[0] + 200 + (seed % 60), p[1] + PROP_FIRST + k * PROP_ROW
    if name == "props.mods":
        return p[0] + 170, p[1] + PROP_FIRST + 11 * PROP_ROW
    if name == "props.basecolor":
        return p[0] + 240, p[1] + PROP_FIRST + 15 * PROP_ROW
    if name == "props.metal":
        return p[0] + 220 + (seed % 90), p[1] + PROP_FIRST + 16 * PROP_ROW
    if name == "props.rough":
        return p[0] + 220 + (seed % 90), p[1] + PROP_FIRST + 17 * PROP_ROW
    if name.startswith("workspace."):
        k = int(name.split(".")[1])
        return min(340 + k * 92, head[2] - 40), head[1] + 17
    if name == "render.button":
        return min(1120, head[2] - 60), head[1] + 17
    if name == "outliner":
        return o[0] + 120 + (seed % 90), o[1] + 40 + (seed % 14) * ROW_H
    if name == "timeline":
        tl = rects["timeline"]
        return tl[0] + 80 + (seed % max(1, int(min(900, tl[2] - 120)))), tl[1] + 28
    if name == "info":
        r = rects["info"]
        return r[0] + 60 + (seed % max(1, int(r[2] - 90))), min(r[1] + 30 + (seed % 7) * 30, r[1] + max(r[3] - 14, 0))
    if name == "nodes":
        r = rects["info"]
        return r[0] + 80 + (seed % max(1, int(r[2] - 160))), min(r[1] + 60 + (seed % 11) * 34, r[1] + max(r[3] - 14, 0))
    if name == "gizmo":
        return v[0] + v[2] - 60, v[1] + 90
    raise KeyError(name)
