# SPDX-License-Identifier: GPL-3.0-or-later
import os

import numpy as np
import pytest

from mousestorm import options as O
from mousestorm import raster as rs
from mousestorm import themes
from mousestorm.grade import GRADES, Finisher, build_lut
from mousestorm.plan import Settings, normalized
from mousestorm.sound import MOODS


def test_defaults_are_valid():
    for k, o in O.OPTS.items():
        assert O.coerce(o, o.default) == o.default, k


def test_every_option_belongs_to_a_group():
    groups = {g for g, _, _ in O.GROUPS}
    assert all(k.split(".")[0] in groups for k in O.OPTS)


@pytest.mark.parametrize("name", sorted(O.PRESETS))
def test_presets_are_valid(name):
    p = O.PRESETS[name]
    O.resolve(p["opt"])
    assert set(p["film"]) <= {"aspect", "duration", "quality", "res"}


@pytest.mark.parametrize("key,value,expected", [
    ("look.bloom", "0.5", 0.5), ("look.bloom", 9, 1.0), ("swarm.names", "3.4", 3), ("show.wipe", "off", False),
    ("swarm.palette", "#FF0000, 00ff00", "ff0000,00ff00"), ("look.floor_color", "", ""),
    ("swarm.skins", "pixel,pixel,dart", "pixel,dart"),
])
def test_coerce(key, value, expected):
    assert O.coerce(O.OPTS[key], value) == expected


@pytest.mark.parametrize("key,value", [
    ("look.bloom", "bright"), ("look.bloom", float("nan")), ("show.wipe", "maybe"), ("look.theme", "pink"),
    ("look.floor_color", "red"), ("swarm.skins", "pixel,square"),
])
def test_bad_values_rejected(key, value):
    with pytest.raises(ValueError):
        O.coerce(O.OPTS[key], value)


def test_unknown_option_rejected_with_a_hint():
    with pytest.raises(ValueError, match="trail"):
        O.resolve({"swarm.trails": 1})


def test_later_layers_win():
    assert O.resolve({"look.grain": 0.5}, {"look.grain": 0.2})["look.grain"] == 0.2


def test_assignments_need_an_equals_sign():
    assert O.parse_assignments(["a.b=c=d"]) == {"a.b": "c=d"}
    with pytest.raises(ValueError):
        O.parse_assignments(["nope"])


def test_settings_are_normalized_with_options():
    s = normalized(Settings(opt={"look.theme": "neon"}))
    assert s.opt["look.theme"] == "neon" and s.opt["look.grain"] == 0.10


def test_toml_config(tmp_path):
    p = tmp_path / "f.toml"
    p.write_text('[film]\nspeed = 3\n\n[look]\ntheme = "neon"\nbloom = 0.6\n', encoding="utf-8")
    film, opt = O.load_config(str(p))
    assert film == {"speed": 3} and opt == {"look.theme": "neon", "look.bloom": 0.6}


def test_json_config(tmp_path):
    p = tmp_path / "f.json"
    p.write_text('{"camera": {"lens": 85}}', encoding="utf-8")
    assert O.load_config(str(p))[1] == {"camera.lens": 85}


def test_docs_are_in_sync():
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs", "OPTIONS.md")
    with open(path, encoding="utf-8") as f:
        assert f.read() == O.markdown(), "regenerate with: python -m mousestorm options > docs/OPTIONS.md"


def test_web_never_exposes_file_paths():
    assert "sound.track" not in [o["key"] for o in O.as_json()["options"]]


# ------------------------------------------------------------------ themes
@pytest.mark.parametrize("name", O.THEMES)
def test_theme_is_complete(name):
    assert set(themes.THEMES["graphite"]) == set(themes.THEMES[name])


def test_option_tables_match_implementations():
    assert set(O.THEMES) == set(themes.THEMES)
    assert set(O.GRADES) == set(GRADES)
    assert set(O.RIGS) == set(themes.RIGS)
    assert set(O.MOODS) == set(MOODS)


def test_srgb_conversion():
    assert themes.srgb_to_linear("ffffff") == pytest.approx((1, 1, 1))
    assert themes.srgb_to_linear("000000") == pytest.approx((0, 0, 0))
    assert themes.srgb_to_linear("808080")[0] == pytest.approx(0.2158, abs=1e-3)


# ------------------------------------------------------------------- grade
def test_none_grade_is_identity():
    assert np.allclose(build_lut("none")[:, 0], np.arange(256))


@pytest.mark.parametrize("name", sorted(GRADES))
def test_luts_stay_in_range_and_rise(name):
    lut = build_lut(name, 1.2)
    assert lut.min() >= 0 and lut.max() <= 255
    assert (np.diff(lut, axis=0) >= -1e-3).all()


def _opts(**kw):
    o = O.defaults()
    o.update(kw)
    return o


def test_finisher_with_everything_off_changes_nothing():
    f = Finisher(_opts(**{"look.grade": "none", "look.grain": 0.0, "look.bloom": 0.0, "look.vignette": 0.0}), 64, 48)
    img = np.random.default_rng(0).integers(0, 255, (48, 64, 4), np.uint8)
    before = img.copy()
    f.apply(img, 3)
    assert (img == before).all()


def test_grain_is_a_pure_function_of_the_frame():
    f = Finisher(_opts(), 64, 48)
    a = np.full((48, 64, 4), 120, np.uint8)
    b = a.copy()
    c = a.copy()
    f.apply(a, 7)
    f.apply(b, 7)
    f.apply(c, 8)
    assert (a == b).all() and not (a == c).all()


def test_noir_is_grey():
    f = Finisher(_opts(**{"look.grade": "noir", "look.grain": 0.0, "look.bloom": 0.0, "look.vignette": 0.0}), 8, 8)
    img = np.zeros((8, 8, 4), np.uint8)
    img[..., 0], img[..., 1], img[..., 3] = 200, 40, 255
    f.apply(img, 0)
    assert abs(int(img[4, 4, 0]) - int(img[4, 4, 1])) <= 1


def test_vignette_darkens_corners_only():
    f = Finisher(_opts(**{"look.grade": "none", "look.grain": 0.0, "look.bloom": 0.0, "look.vignette": 1.0}), 64, 48)
    img = np.full((48, 64, 4), 200, np.uint8)
    f.apply(img, 0)
    assert img[0, 0, 0] < img[24, 32, 0]


# --------------------------------------------------------------- light paint
def test_light_painting_accumulates_along_the_path():
    pos = np.zeros((1, 11, 2), np.float32)
    pos[0, :, 0] = np.linspace(2, 40, 11)
    pos[0, :, 1] = 10
    col = np.ones((11, 3), np.float32)
    buf = np.zeros((20, 50, 3), np.float32)
    rs.splat_paths(pos, col, 11, 1, 1.0, 50, 20, buf, 1.0)
    assert buf[10, 2:40, 0].min() > 0 and buf[3, :, 0].max() == 0


def test_light_painting_stops_where_asked_and_ignores_parked_mice():
    pos = np.full((1, 11, 2), -400.0, np.float32)
    buf = np.zeros((20, 50, 3), np.float32)
    rs.splat_paths(pos, np.ones((11, 3), np.float32), 11, 1, 1.0, 50, 20, buf, 1.0)
    assert buf.max() == 0


def test_ink_on_paper_keeps_the_stroke_hue():
    """On a light theme the light-painting is ink: an orange path must stay orange, not turn cyan."""
    buf = np.zeros((1, 2, 3), np.float32)
    buf[0, 1] = (2.0, 0.6, 0.1)
    rgba = np.zeros((1, 2, 4), np.uint8)
    rs.tonemap_paths(buf, 2, 1, np.array((0.9, 0.87, 0.8)), rgba, 1.0)
    paper, stroke = rgba[0, 0, :3].astype(int), rgba[0, 1, :3].astype(int)
    assert paper.min() > 220
    assert stroke[0] > stroke[1] > stroke[2]
    assert stroke[2] < paper[2] - 100


def test_light_adds_up_on_a_dark_base():
    buf = np.zeros((1, 2, 3), np.float32)
    buf[0, 1] = (2.0, 0.0, 0.0)
    rgba = np.zeros((1, 2, 4), np.uint8)
    rs.tonemap_paths(buf, 2, 1, np.array((0.01, 0.01, 0.01)), rgba, 1.0)
    assert rgba[0, 1, 0] > rgba[0, 0, 0] + 100
    assert rgba[0, 1, 1] == rgba[0, 0, 1]
