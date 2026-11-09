# SPDX-License-Identifier: GPL-3.0-or-later
"""Fast tests: no Blender needed, nothing heavier than numpy."""
import numpy as np
import pytest

from mousestorm import raster as rs
from mousestorm.cli import collect_inputs, parse_aspect, parse_res
from mousestorm.plan import (CubicPath, Settings, film_time, kd_labels, laplace, normalized, segment, show_schedule,
                             solve_speed, weld)
from mousestorm.plan_finish import lens_tans
from mousestorm.render import FORMATS, beauty_key, encoder_cmd, prune_cache, resolve_output
from mousestorm.scene import Camera
from mousestorm.showcase import cam_vector
from mousestorm.sound import SR, lufs, master
from mousestorm.ui_layout import Fx, anchor_xy, canvas, layout_at


# ------------------------------------------------------------------ settings
def test_odd_dimensions_become_even():
    s = normalized(Settings(width=641, height=361))
    assert (s.width, s.height) == (640, 360)


@pytest.mark.parametrize("field,value", [("speed", float("nan")), ("showcase", float("inf"))])
def test_non_finite_numbers_rejected(field, value):
    with pytest.raises(ValueError):
        normalized(Settings(**{field: value}))


@pytest.mark.parametrize("kw", [{"width": 10}, {"height": 9000}, {"quality": "ultra"}, {"lighting": "x"}, {"soundtrack": "x"}])
def test_bad_settings_rejected(kw):
    with pytest.raises(ValueError):
        normalized(Settings(**kw))


def test_values_are_clamped():
    s = normalized(Settings(speed=9999, fps=1, mice=-3, showcase=0))
    assert s.speed == 40.0 and s.fps == 6 and s.mice == 0 and s.showcase == 6.0


def test_no_sound_means_no_soundtrack():
    s = normalized(Settings(sound=False))
    assert s.soundtrack == "off" and s.sound is False


# ------------------------------------------------------------------ cli bits
@pytest.mark.parametrize("res,aspect,expected", [
    ("1080p", 16 / 9, (1920, 1080)), ("1080p", 9 / 16, (1080, 1920)), ("720p", 1.0, (720, 720)),
    ("4k", 16 / 9, (3840, 2160)), ("1000x500", 16 / 9, (1000, 500)),
])
def test_parse_res(res, aspect, expected):
    assert parse_res(res, aspect) == expected


@pytest.mark.parametrize("bad", ["huge", "12x", "x", ""])
def test_parse_res_rejects_nonsense(bad):
    with pytest.raises(ValueError):
        parse_res(bad)


@pytest.mark.parametrize("bad", ["16", "a:b", "0:9", "100:1"])
def test_parse_aspect_rejects_nonsense(bad):
    with pytest.raises(ValueError):
        parse_aspect(bad)


def test_collect_inputs_expands_folders(tmp_path):
    (tmp_path / "a.blend").write_bytes(b"x")
    (tmp_path / "b.blend").write_bytes(b"x")
    (tmp_path / "c.txt").write_bytes(b"x")
    assert [p[-7:] for p in collect_inputs([str(tmp_path)])] == ["a.blend", "b.blend"]


@pytest.mark.parametrize("out,fmt,expected", [
    ("film.mp4", "mp4", "film.mp4"), ("film.mp4", "webm", "film.webm"), ("film", "gif", "film.gif"),
    ("a.b/film.v2", "mov", "a.b/film.v2.mov"), ("seq.png", "png", "seq"), ("seq", "png", "seq"),
])
def test_output_extension_matches_format(out, fmt, expected):
    assert resolve_output(out, fmt) == expected


@pytest.mark.parametrize("fmt", FORMATS)
def test_every_format_has_an_encoder_command(fmt, tmp_path):
    s = Settings(fmt=fmt)
    cmd = encoder_cmd("ffmpeg", s, str(tmp_path / "x"), None, 4)
    assert cmd[0] == "ffmpeg" and "rawvideo" in cmd


# ---------------------------------------------------------------- timing maths
@pytest.mark.parametrize("target", [8.0, 20.0, 45.0, 120.0])
def test_solve_speed_hits_the_target(target):
    speed = solve_speed(30.0, 8.0, target)
    assert film_time(speed, 30.0, 8.0) == pytest.approx(target, abs=0.05)


def test_solve_speed_clamps_when_impossible():
    assert solve_speed(30.0, 8.0, 0.01) == 40.0
    assert solve_speed(30.0, 8.0, 1e6) == 0.05


def test_film_time_shrinks_as_speed_rises():
    times = [film_time(s, 30.0, 8.0) for s in (0.5, 1, 2, 4, 8, 16)]
    assert times == sorted(times, reverse=True)


def test_finale_schedule_fills_the_finale():
    sched = show_schedule(20.0, ["hero", "turntable", "shoot", "poster", "sheet"])
    assert sum(d for _, _, d in sched) == pytest.approx(20.0)
    assert sched[1][1] == pytest.approx(sched[0][2])


def test_finale_schedule_respects_order_and_omissions():
    sched = show_schedule(10.0, ["turntable", "hero"])
    assert [s for s, _, _ in sched] == ["turntable", "hero"]
    assert segment(sched, "shoot") is None and segment(sched, "hero") is not None
    assert show_schedule(10.0, [])[0][0] == "hero"


def test_switching_off_phases_shortens_the_film():
    assert film_time(1.0, 30, 8, lighting=False, tiles=False) < film_time(1.0, 30, 8)


def test_cubic_path_passes_through_keys_and_holds_at_the_ends():
    p = CubicPath([0.0, 1.0, 3.0], [[0.0], [2.0], [5.0]])
    assert p(1.0)[0] == pytest.approx(2.0)
    assert p(-5.0)[0] == pytest.approx(0.0)
    assert p(99.0)[0] == pytest.approx(5.0)


# -------------------------------------------------------------------- geometry
def test_kd_labels_make_equal_patches():
    pts = np.random.default_rng(0).random((1000, 3))
    labels = kd_labels(pts, 8)
    counts = np.bincount(labels)
    assert len(counts) == 8 and counts.min() >= 120 and counts.max() <= 130


def test_weld_merges_duplicates():
    p = np.array([[0, 0, 0], [0, 0, 0], [1, 0, 0]], float)
    inv, n = weld(p, 1e-6)
    assert n == 2 and inv[0] == inv[1] != inv[2]


def test_laplace_keeps_the_centroid_of_a_closed_mesh():
    v = np.array([[1, 0, 0], [-1, 0, 0], [0, 1, 0], [0, -1, 0], [0, 0, 1], [0, 0, -1]], float)
    t = np.array([[0, 2, 4], [2, 1, 4], [1, 3, 4], [3, 0, 4], [2, 0, 5], [1, 2, 5], [3, 1, 5], [0, 3, 5]])
    assert np.allclose(laplace(v, t, 8).mean(0), 0.0, atol=1e-9)


@pytest.mark.parametrize("aspect,lens", [(16 / 9, 50), (9 / 16, 50), (1.0, 35), (2.4, 85)])
def test_lens_fits_the_long_side(aspect, lens):
    x, y = lens_tans(lens, aspect)
    assert max(x, y) == pytest.approx(18.0 / lens)
    assert x / y == pytest.approx(aspect)


# --------------------------------------------------------------------- layout
@pytest.mark.parametrize("size", [(1920, 1080), (3840, 2160), (1080, 1920), (1080, 1080), (2560, 1080), (1000, 1000)])
@pytest.mark.parametrize("fx", [Fx(0, 0), Fx(1, 0), Fx(0.5, 0.5), Fx(0, 1)])
def test_every_panel_stays_on_the_canvas(size, fx):
    W, H = canvas(*size)
    for name, (x, y, w, h) in layout_at(fx, W, H).items():
        assert w >= -1e-6 and h >= -1e-6, name
        assert x >= -1e-6 and y >= -1e-6, name
        assert x + w <= W + 1e-6 and y + h <= H + 1e-6, name


@pytest.mark.parametrize("size", [(1920, 1080), (1080, 1920), (1080, 1080)])
def test_viewport_fills_the_frame_at_full(size):
    W, H = canvas(*size)
    v = layout_at(Fx(0, 1), W, H)["view"]
    assert v == pytest.approx((0.0, 0.0, W, H))


@pytest.mark.parametrize("size", [(1920, 1080), (1080, 1920), (1080, 1080)])
@pytest.mark.parametrize("name", ["menu.add", "tool.3", "props.tab", "props.field.4", "props.mods", "props.basecolor",
                                  "props.metal", "props.rough", "outliner", "timeline", "info", "nodes", "gizmo",
                                  "render.button", "workspace.2"])
def test_mice_never_aim_outside_the_frame(size, name):
    W, H = canvas(*size)
    r = layout_at(Fx(0, 0), W, H)
    for seed in range(0, 200, 7):
        x, y = anchor_xy(name, r, seed=seed)
        assert 0 <= x <= W and 0 <= y <= H


def test_a_square_frame_has_no_side_panels():
    W, H = canvas(1080, 1080)
    r = layout_at(Fx(0, 0), W, H)
    assert r["props"][3] == 0 and r["outliner"][3] == 0
    assert r["view"][3] > 600


# ----------------------------------------------------------- view interpolation
def _plane_views(W=200, H=120, depth=5.0):
    """A wall facing the camera, carrying a pattern fixed to the world."""
    tany = 0.3
    tanx = tany * W / H

    def view(cx):
        cam = cam_vector((cx, -depth, 0.0), (cx, 0.0, 0.0))
        Z = np.full((H, W), 1.0 / depth, np.float32)
        C = np.zeros((H, W, 4), np.uint8)
        px = (np.arange(W) + 0.5 - W / 2) / (W / 2) * tanx * depth + cx
        C[:, :, 0] = (127 + 120 * np.sin(px * 6.0))[None].repeat(H, 0)
        C[:, :, 3] = 255
        return cam, Z, C
    return view, tanx, tany, W, H


def test_view_interpolation_recovers_the_true_middle_view():
    view, tanx, tany, W, H = _plane_views()
    ca, za, cola = view(0.0)
    cb, zb, colb = view(0.2)
    cm, zm, truth = view(0.1)
    rows = np.zeros((H, 4), np.float64)
    out = np.zeros((H, W, 4), np.uint8)
    ztarget = np.repeat(np.repeat(zm, 2, 0), 2, 1)
    rs.view_interp(W, H, 2, ztarget, za, zb, cola, colb, rows, rows, cm, ca, cb, tanx, tany, 0.0, False, False, 0.5, out)
    err = np.abs(out[10:-10, 15:-15, 0].astype(float) - truth[10:-10, 15:-15, 0].astype(float)).mean()
    fade = np.abs((cola[10:-10, 15:-15, 0].astype(float) + colb[10:-10, 15:-15, 0].astype(float)) / 2
                  - truth[10:-10, 15:-15, 0].astype(float)).mean()
    assert err < 3.0 and err < fade / 3     # a plain dissolve is what this replaces


def test_backdrop_never_picks_up_an_objects_edge():
    W, H = 80, 60
    Z = np.zeros((H, W), np.float32)
    C = np.full((H, W, 4), 20, np.uint8)
    Z[20:40, 30:50] = 0.2
    C[19:41, 29:51, :3] = 240         # the object, with an anti-aliased fringe around it
    rows = np.zeros((H, 4), np.float64)
    rs.backdrop_rows(C, Z, W, H, rows)
    cam = cam_vector((0.0, -5.0, 0.0), (0.0, 0.0, 0.0))
    out = np.zeros((H, W, 4), np.uint8)
    empty = np.zeros((H * 2, W * 2), np.float32)
    rs.view_interp(W, H, 2, empty, Z, Z, C, C, rows, rows, cam, cam, cam, 0.5, 0.375, 0.0, False, True, 0.5, out)
    assert out[..., 0].max() <= 120    # no bright speck anywhere; fringe texels are refused


# ------------------------------------------------------------------ loudness
def test_lufs_of_a_reference_tone():
    # BS.1770: a 997 Hz stereo sine at -3.01 dBFS peak reads about -3 LUFS per channel pair
    t = np.arange(SR * 3) / SR
    x = np.sin(2 * np.pi * 997 * t) * 10 ** (-3.01 / 20)
    assert lufs(np.stack([x, x], 1)) == pytest.approx(-3.0, abs=0.6)


def test_master_hits_target_under_ceiling():
    rng = np.random.default_rng(0)
    t = np.arange(SR * 4) / SR
    x = np.stack([np.sin(2 * np.pi * 220 * t), np.sin(2 * np.pi * 330 * t)], 1) * 0.02 + rng.normal(0, 0.003, (len(t), 2))
    out = master(x)
    assert lufs(out) == pytest.approx(-16.0, abs=1.0)
    assert np.abs(out).max() <= 0.89 + 1e-6


# --------------------------------------------------------------------- cache
def test_cache_key_ignores_what_does_not_change_the_picture(tmp_path):
    blend = tmp_path / "x.blend"
    blend.write_bytes(b"BLENDER-v500")
    base = dict(width=1920, height=1080, samples=96, turn_samples=24, device="cpu", rig={}, stills=[1], turntable=[2])
    a = beauty_key(str(blend), dict(base, threads=4), (5, 2, 3))
    assert a == beauty_key(str(blend), dict(base, threads=32), (5, 2, 3))
    assert a != beauty_key(str(blend), dict(base, samples=320), (5, 2, 3))
    assert a != beauty_key(str(blend), base, (4, 5, 0))
    blend.write_bytes(b"BLENDER-v501")
    assert a != beauty_key(str(blend), base, (5, 2, 3))


def test_prune_removes_oldest_first_but_never_the_current(tmp_path):
    import os
    for i, name in enumerate(("old", "mid", "new")):
        d = tmp_path / name
        d.mkdir()
        (d / "f").write_bytes(b"x" * 1024 * 1024)
        os.utime(d, (1000 + i, 1000 + i))
    prune_cache(str(tmp_path), str(tmp_path / "old"), limit_gb=2.5 / 1024)
    left = sorted(p.name for p in tmp_path.iterdir())
    assert left == ["new", "old"]


def test_camera_basis_is_orthonormal():
    r, u, f = Camera(np.array([3.0, -4.0, 2.0]), np.zeros(3)).basis()
    M = np.stack([r, u, f])
    assert np.allclose(M @ M.T, np.eye(3))
