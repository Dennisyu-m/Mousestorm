# SPDX-License-Identifier: GPL-3.0-or-later
"""The film look: colour grade, bloom, vignette and grain, applied to finished frames."""
import numpy as np

from . import raster as rs

# lift, gain, S-curve amount, saturation
GRADES = {
    "none": ((0, 0, 0), (1, 1, 1), 0.0, 1.0),
    "clean": ((0, 0, 0), (1, 1, 1), 0.12, 1.02),
    "warm": ((0.012, 0.004, -0.006), (1.05, 1.0, 0.93), 0.12, 1.04),
    "cool": ((-0.004, 0.003, 0.016), (0.94, 1.0, 1.07), 0.1, 1.0),
    "teal_orange": ((-0.01, 0.02, 0.035), (1.08, 0.99, 0.9), 0.22, 1.08),
    "noir": ((0, 0, 0), (1, 1, 1), 0.38, 0.0),
    "vivid": ((0, 0, 0), (1.02, 1.0, 1.02), 0.2, 1.28),
    "film": ((0.03, 0.028, 0.035), (1.0, 0.985, 0.95), 0.26, 0.92),
}


def build_lut(name, contrast=1.0):
    """A 256 × 3 table of output values for each channel."""
    lift, gain, s, _ = GRADES.get(name, GRADES["none"])
    x = np.arange(256, dtype=np.float64) / 255.0
    out = np.empty((256, 3), np.float32)
    for c in range(3):
        v = lift[c] * (1.0 - x) + x * gain[c]
        v = np.clip(v, 0.0, 1.0)
        v = v + s * (v * v * (3.0 - 2.0 * v) - v)
        v = (v - 0.5) * contrast + 0.5
        out[:, c] = np.clip(v, 0.0, 1.0) * 255.0
    return out


class Finisher:
    """Holds what can be computed once per worker; apply() is cheap per frame."""

    def __init__(self, opt, W, H):
        self.W, self.H = W, H
        name = opt["look.grade"]
        self.lut = build_lut(name, float(opt["look.contrast"]))
        self.sat = GRADES.get(name, GRADES["none"])[3] * float(opt["look.saturation"])
        self.grain_amt = float(opt["look.grain"])
        self.bloom = float(opt["look.bloom"])
        vig = float(opt["look.vignette"])
        self.active = (name != "none" or self.sat != 1.0 or self.grain_amt > 0 or self.bloom > 0 or vig > 0
                       or float(opt["look.contrast"]) != 1.0)
        if not self.active:
            return
        ys, xs = np.mgrid[0:H, 0:W]
        rx = (xs + 0.5) / W * 2.0 - 1.0
        ry = (ys + 0.5) / H * 2.0 - 1.0
        d = np.clip(((rx * rx + ry * ry) * 0.5 - 0.22) / 0.78, 0.0, 1.0)
        self.vig = (1.0 - vig * 0.78 * d * d * (3.0 - 2.0 * d)).astype(np.float32)
        rng = np.random.default_rng(11)
        tile = rng.standard_normal((1024, 1024)).astype(np.float32)
        # a little softening so the grain reads as film rather than as digital noise
        self.grain = ((tile + np.roll(tile, 1, 0) * 0.5 + np.roll(tile, 1, 1) * 0.5) / 2.0 * 0.9).astype(np.float32)
        self.step = max(2, int(max(W, H) / 240))
        self.B = np.zeros((max(1, H // self.step), max(1, W // self.step), 3), np.float32)

    def apply(self, rgba, frame, strength=1.0):
        if not self.active or strength <= 0.0:
            return
        if self.bloom > 0:
            rs.bloom_map(rgba, self.W, self.H, self.step, 0.72, self.B)
        ox, oy = (frame * 7919) & 1023, (frame * 104729) & 1023
        rs.finish_frame(rgba, self.W, self.H, self.lut, float(self.sat), float(strength), self.vig, self.grain,
                        self.grain_amt, ox, oy, self.B, self.bloom, self.step)
