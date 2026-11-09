# SPDX-License-Identifier: GPL-3.0-or-later
"""Scene container: extracted geometry + a software viewport over it."""
import json
import os
from dataclasses import dataclass

import numpy as np

from . import raster as rs
from .themes import theme as get_theme

# sRGB-authored overlay colours, kept in linear for the shader
def lin(c):
    c = np.array(c, dtype=np.float32) / 255.0
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


@dataclass
class Camera:
    eye: np.ndarray
    target: np.ndarray
    tany: float = 0.3
    ortho: bool = False
    up: tuple = (0.0, 0.0, 1.0)

    def basis(self):
        f = self.target - self.eye
        f = f / max(np.linalg.norm(f), 1e-9)
        up = np.array(self.up, dtype=np.float64)
        r = np.cross(f, up)
        if np.linalg.norm(r) < 1e-5:
            up = np.array([0.0, 1.0, 0.0])
            r = np.cross(f, up)
        r = r / np.linalg.norm(r)
        u = np.cross(r, f)
        return r, u, f


class SceneData:
    """Read-only (memory-mapped) arrays produced by bscripts/extract.py."""

    def __init__(self, work):
        self.work = work
        with open(os.path.join(work, "scene.json"), encoding="utf-8") as f:
            self.doc = json.load(f)
        self.parts = self.doc["parts"]
        self.materials = self.doc["materials"]
        load = lambda n: np.load(os.path.join(work, n), mmap_mode="r")
        self.P = load("P.npy")
        self.BOX = load("BOX.npy")
        self.T = load("T.npy")
        self.TN = load("TN.npy")
        self.TMASK = load("TMASK.npy")
        self.TMAT = load("TMAT.npy")
        self.TUV = load("TUV.npy") if self.doc["has_uv"] else np.zeros((1, 3, 2), np.float32)
        self.TCOL = load("TCOL.npy") if self.doc["has_col"] else -np.ones((1, 3, 3), np.float32)
        self.nv, self.nt = len(self.P), len(self.T)
        self.npart = len(self.parts)
        lo = np.min([p["bbox"][0] for p in self.parts], axis=0)
        hi = np.max([p["bbox"][1] for p in self.parts], axis=0)
        # a millimetre-sized model must not vanish behind a fixed near plane
        self.near = float(min(0.01, max(np.linalg.norm(hi - lo), 1e-9) * 1e-3))
        self.tpart = np.empty(self.nt, np.int32)
        for i, p in enumerate(self.parts):
            self.tpart[p["t0"]: p["t0"] + p["nt"]] = i
        self._tables()

    def _tables(self):
        M = max(1, len(self.materials))
        self.mcol = np.full((M, 3), 0.6, np.float32)
        self.mpar = np.zeros((M, 7), np.float32)
        self.mtex = -np.ones((M, 3), np.int32)
        chunks, ofs, seen = [], 0, {}
        for i, m in enumerate(self.materials):
            self.mcol[i] = m["color"]
            self.mpar[i] = [m["metal"], m["rough"], m["alpha"], *m["emit"], float(m.get("vcol", False))]
            if m.get("tex"):
                if m["tex"] not in seen:
                    a = np.load(os.path.join(self.work, "tex", m["tex"]))
                    seen[m["tex"]] = (ofs, a.shape[1], a.shape[0])
                    chunks.append(a.reshape(-1))
                    ofs += a.size
                self.mtex[i] = seen[m["tex"]]
        self.tex = np.concatenate(chunks) if chunks else np.zeros(3, np.uint8)
        alpha = np.ones(len(self.TMAT), np.float32)
        if len(self.materials):
            lut = np.append(self.mpar[:, 2], 1.0)  # index -1 → opaque
            alpha = lut[np.asarray(self.TMAT)]
        self.talpha = alpha


def palette(name="graphite"):
    t = get_theme(name)
    return np.concatenate([lin(t["clay"]), lin(t["wire"]), lin(t["sel"]), lin(t["front"]),
                           np.array(t["view_base"]), np.array(t["view_glow"]), np.array(t["grid"])]).astype(np.float32)


class Viewport:
    """Per-worker mutable state + the render call."""

    def __init__(self, data: SceneData, theme="graphite"):
        self.d = data
        self.pal = palette(theme)
        self.P = np.array(data.P, dtype=np.float32)
        self.TN = np.array(data.TN, dtype=np.float32)
        self.V = np.empty_like(self.P)
        self.VN = np.empty_like(self.P, dtype=np.float32)
        self.VSEL = np.zeros(data.nv, np.float32)
        self.VPAINT = np.zeros(data.nv, np.float32)
        self.pwire = np.zeros(data.npart, np.float32)
        self.ppaint = np.zeros(data.npart, np.float32)
        self.buffers = {}
        self.shadow_buf = None

    def _buf(self, W, H):
        k = (W, H)
        if k not in self.buffers:
            self.buffers[k] = (
                np.empty((H, W), np.float32), np.empty((H, W), np.int32),
                np.empty((H, W, 3), np.float32), np.empty((H, W), np.float32),
            )
        return self.buffers[k]

    def render(self, cam: Camera, w, h, visible, ss=2, ground=0.0, grid=0.0,
               fade=40.0, shadow=None, flat=False, cavity=0.0, exposure=1.0):
        """Returns an RGBA8 image of size (h, w)."""
        d = self.d
        W, H = w * ss, h * ss
        Z, ID, OUT, NB = self._buf(W, H)
        ID.fill(-1)
        Z.fill(0.0 if not cam.ortho else -1e18)
        r, u, f = cam.basis()
        R = np.stack([r, u, f]).astype(np.float64)
        eye = np.asarray(cam.eye, dtype=np.float64)
        tany = float(cam.tany)
        tanx = tany * W / H

        if len(visible):
            parts = d.parts
            tr = np.array([[parts[i]["t0"], parts[i]["t0"] + parts[i]["nt"]] for i in visible], np.int64)
            vr = np.array([[parts[i]["v0"], parts[i]["v0"] + parts[i]["nv"]] for i in visible], np.int64)
            rs.to_view(self.P, tr, vr, eye, R, self.V)
            rs.rasterize(self.V, d.T, tr, W, H, tanx, tany, d.near, cam.ortho, Z, ID,
                         d.talpha, rs.BAYER)

        cam_arr = np.empty(19, np.float64)
        cam_arr[:3] = eye
        cam_arr[3:6], cam_arr[6:9], cam_arr[9:12] = r, u, f
        cam_arr[12], cam_arr[13] = tanx, tany
        cam_arr[14], cam_arr[15] = cam.target[0], cam.target[1]
        cam_arr[16] = fade
        cam_arr[17] = fade * 0.0025
        cam_arr[18] = fade * 0.0035

        if shadow is None:
            SZ = np.zeros((1, 1), np.float32)
            shc = np.zeros(13, np.float64)
            shc[9:12] = [0.35, 0.35, -0.87]
            shc[12] = 1.0
        else:
            SZ, shc = shadow

        rs.shade(W, H, ID, Z, OUT, NB, cam_arr, cam.ortho, self.P, d.T, self.TN, d.TUV, d.TCOL,
                 d.doc["has_uv"], d.doc["has_col"], d.TMAT, d.tpart, d.TMASK,
                 d.mcol, d.mpar, d.mtex, d.tex, self.pwire, self.ppaint, self.VPAINT,
                 self.VSEL, SZ, shc, float(ground), float(grid), self.pal, flat)
        if cavity > 0.0:
            rs.cavity(OUT, Z, ID, cam.ortho, float(cavity))
        rgba = np.empty((h, w, 4), np.uint8)
        rs.resolve(OUT, ss, w, h, rgba, float(exposure))
        return rgba

    def shadow_map(self, center, radius, visible, res=1400, light_dir=(-0.48, -0.52, 0.71)):
        """Orthographic key-light depth, re-rendered every frame because the
        geometry is moving."""
        if self.shadow_buf is None or self.shadow_buf[0].shape[0] != res:
            self.shadow_buf = (np.empty((res, res), np.float32), np.empty((res, res), np.int32))
        SZ, SID = self.shadow_buf
        SZ.fill(-1e18)
        SID.fill(-1)
        d = self.d
        ld = np.array(light_dir, np.float64)
        ld /= np.linalg.norm(ld)
        half = float(radius) * 1.25
        eye = np.asarray(center, np.float64) + ld * radius * 4.0
        cam = Camera(eye, np.asarray(center, np.float64), ortho=True)
        r, u, f = cam.basis()
        R = np.stack([r, u, f]).astype(np.float64)
        if len(visible):
            parts = d.parts
            tr = np.array([[parts[i]["t0"], parts[i]["t0"] + parts[i]["nt"]] for i in visible], np.int64)
            vr = np.array([[parts[i]["v0"], parts[i]["v0"] + parts[i]["nv"]] for i in visible], np.int64)
            rs.to_view(self.P, tr, vr, eye, R, self.V)
            rs.rasterize(self.V, d.T, tr, res, res, half, half, 0.01, True, SZ, SID,
                         d.talpha, rs.BAYER)
        shc = np.empty(13, np.float64)
        shc[:3] = eye
        shc[3:6], shc[6:9], shc[9:12] = r, u, f
        shc[12] = half
        return SZ, shc
