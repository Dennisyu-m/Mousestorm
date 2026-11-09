# SPDX-License-Identifier: GPL-3.0-or-later
"""Every advanced setting, declared once.

The command line (--set), config files, the app's Advanced panel, docs/OPTIONS.md
and validation are all generated from this table, so they cannot drift apart.
Values live in Settings.opt as a flat dict keyed by "group.name".
"""
import json
import math
import os
import re
from dataclasses import dataclass

GROUPS = [
    ("look", "Look", "外观"), ("swarm", "Mice", "鼠标"), ("pace", "Pacing", "节奏"), ("ui", "Workspace", "工作区"),
    ("camera", "Camera", "镜头"), ("show", "Finale", "结尾展示"), ("title", "Titles", "字幕"),
    ("render", "Cycles", "Cycles 渲染"), ("sound", "Sound", "声音"), ("encode", "Encoding", "编码"),
    ("output", "Extra files", "附加文件"),
]
THEMES = ("graphite", "midnight", "paper", "neon", "terminal", "blueprint")
GRADES = ("none", "clean", "warm", "cool", "teal_orange", "noir", "vivid", "film")
RIGS = ("studio", "sunset", "noir", "clean", "neon")
SKINS = ("classic", "dart", "pixel", "hollow", "bubble", "critter")
SEGMENTS = ("hero", "turntable", "shoot", "poster", "sheet")
CAMERAS = ("front", "right", "back", "high", "top", "low", "detail1", "detail2", "scene")
MOODS = ("dusk", "bright", "drive", "calm")


@dataclass(frozen=True)
class Opt:
    key: str
    kind: str            # bool int float choice color colors text list
    default: object
    en: str
    zh: str
    lo: float = None
    hi: float = None
    choices: tuple = ()
    web: bool = True     # shown in the app (file paths are command-line only)


_T = [
    # ---------------------------------------------------------------- look
    Opt("look.theme", "choice", "graphite", "Interface theme", "界面主题", choices=THEMES),
    Opt("look.grade", "choice", "clean", "Colour grade", "调色", choices=GRADES),
    Opt("look.saturation", "float", 1.0, "Saturation", "饱和度", 0.0, 2.0),
    Opt("look.contrast", "float", 1.0, "Contrast", "对比度", 0.0, 2.0),
    Opt("look.vignette", "float", 0.22, "Vignette", "暗角", 0.0, 1.0),
    Opt("look.grain", "float", 0.10, "Film grain", "胶片颗粒", 0.0, 1.0),
    Opt("look.bloom", "float", 0.30, "Bloom", "辉光", 0.0, 1.0),
    Opt("look.letterbox", "choice", "off", "Letterbox in the finale", "结尾宽银幕黑边", choices=("off", "1.85", "2.00", "2.39")),
    Opt("look.exposure", "float", 0.0, "Exposure (stops)", "曝光（档）", -3.0, 3.0),
    Opt("look.view_transform", "choice", "agx", "View transform", "显示变换", choices=("agx", "filmic", "standard")),
    Opt("look.lights", "choice", "studio", "Lighting rig", "灯光方案", choices=RIGS),
    Opt("look.light_power", "float", 1.0, "Light power", "灯光强度", 0.2, 3.0),
    Opt("look.floor", "choice", "auto", "Studio floor", "摄影棚地面", choices=("auto", "on", "off")),
    Opt("look.floor_color", "color", "", "Floor colour", "地面颜色"),
    Opt("look.backdrop_top", "color", "", "Backdrop top", "背景上方颜色"),
    Opt("look.backdrop_bottom", "color", "", "Backdrop bottom", "背景下方颜色"),
    # --------------------------------------------------------------- swarm
    Opt("swarm.cursor_scale", "float", 1.0, "Cursor size", "光标大小", 0.5, 3.0),
    Opt("swarm.trail", "float", 150.0, "Trail length", "拖尾长度", 0.0, 500.0),
    Opt("swarm.glow", "float", 0.35, "Cursor glow", "光标辉光", 0.0, 1.0),
    Opt("swarm.ripples", "bool", True, "Click ripples", "点击波纹"),
    Opt("swarm.chips", "bool", True, "Parameter pop-ups", "参数弹窗"),
    Opt("swarm.humanness", "float", 1.0, "Hand-like motion", "手感强度", 0.0, 2.0),
    Opt("swarm.fidget", "bool", True, "Idle mice fidget", "空闲鼠标摆弄界面"),
    Opt("swarm.skins", "list", ",".join(SKINS), "Pointer styles", "指针样式", choices=SKINS),
    Opt("swarm.palette", "colors", "", "Mouse colours", "鼠标配色"),
    Opt("swarm.names", "int", -1, "Name tags (-1 = auto)", "名牌数量（-1 自动）", -1, 64),
    Opt("swarm.crew", "text", "", "Custom names, comma separated", "自定义名字（逗号分隔）"),
    # ---------------------------------------------------------------- pace
    Opt("pace.shading", "bool", True, "Show the shading phase", "显示上材质阶段"),
    Opt("pace.lighting", "bool", True, "Show the lighting phase", "显示布光阶段"),
    Opt("pace.render", "bool", True, "Show the render tiles", "显示渲染方块"),
    Opt("pace.model_weight", "float", 1.0, "Modelling length", "建模时长倍率", 0.2, 4.0),
    Opt("pace.shade_weight", "float", 1.0, "Shading length", "上材质时长倍率", 0.2, 4.0),
    Opt("pace.tweak_speed", "float", 6.0, "Extra speed for small adjustments (1 = off)", "小调整额外倍速（1 为关闭）", 1.0, 20.0),
    Opt("pace.major_size", "float", 0.08, "Change that counts as major (share of the model)", "算作大操作的改动（占模型比例）", 0.005, 0.5),
    # ------------------------------------------------------------------ ui
    Opt("ui.layout", "choice", "full", "Workspace layout", "工作区布局", choices=("full", "focus", "clean")),
    Opt("ui.telemetry", "bool", True, "Live counters", "实时计数"),
    # -------------------------------------------------------------- camera
    Opt("camera.azimuth", "float", -58.0, "Hero azimuth", "主镜头方位角", -180.0, 180.0),
    Opt("camera.elevation", "float", 15.0, "Hero elevation", "主镜头仰角", 0.0, 80.0),
    Opt("camera.use_scene", "bool", True, "Start from the file's own camera angle", "主镜头沿用文件里相机的角度"),
    Opt("camera.swing", "float", 74.0, "Swing during the build", "搭建时环绕角度", 0.0, 360.0),
    Opt("camera.follow", "float", 0.3, "Follow the action", "跟随动作", 0.0, 1.0),
    Opt("camera.lens", "float", 50.0, "Lens (mm)", "镜头焦距（毫米）", 18.0, 135.0),
    Opt("camera.margin", "float", 0.09, "Frame margin", "取景留白", 0.0, 0.5),
    # ---------------------------------------------------------------- show
    Opt("show.segments", "list", ",".join(SEGMENTS), "Finale sections, in order", "结尾段落（按顺序）", choices=SEGMENTS),
    Opt("show.turns", "int", 1, "Turntable revolutions", "转台圈数", 1, 4),
    Opt("show.direction", "choice", "ccw", "Turntable direction", "转台方向", choices=("ccw", "cw")),
    Opt("show.turn_frames", "int", 0, "Cycles frames in the turntable (0 = by quality)", "转台 Cycles 帧数（0 按画质）", 0, 720),
    Opt("show.wipe", "bool", True, "X-ray sweep", "X 光扫描"),
    Opt("show.parallax", "float", 0.5, "Parallax on photos", "照片视差", 0.0, 1.0),
    Opt("show.flash", "float", 0.3, "Camera flash", "闪光强度", 0.0, 1.0),
    Opt("show.cameras", "list", ",".join(CAMERAS), "Photo cameras", "拍照机位", choices=CAMERAS),
    # --------------------------------------------------------------- title
    Opt("title.show", "bool", True, "Opening title", "片头标题"),
    Opt("title.text", "text", "", "Title (default: file name)", "标题（默认文件名）"),
    Opt("title.subtitle", "text", "", "Subtitle (default: statistics)", "副标题（默认统计信息）"),
    Opt("title.caption", "bool", True, "Caption on the hero shot", "主镜头字幕"),
    Opt("title.footer", "text", "", "End-card footer", "片尾脚注"),
    Opt("title.branding", "bool", True, "Mousestorm mark on the end card", "片尾显示 Mousestorm 标记"),
    # -------------------------------------------------------------- render
    Opt("render.samples", "int", 0, "Photo samples (0 = by quality)", "照片采样（0 按画质）", 0, 8192),
    Opt("render.turn_samples", "int", 0, "Turntable samples (0 = by quality)", "转台采样（0 按画质）", 0, 8192),
    Opt("render.denoise", "bool", True, "Denoise", "降噪"),
    Opt("render.bounces", "int", 8, "Light bounces", "光线反弹次数", 1, 32),
    # --------------------------------------------------------------- sound
    Opt("sound.mood", "choice", "dusk", "Mood", "配乐情绪", choices=MOODS),
    Opt("sound.tempo", "float", 100.0, "Tempo (BPM)", "速度（BPM）", 60.0, 180.0),
    Opt("sound.transpose", "int", 0, "Transpose (semitones)", "移调（半音）", -12, 12),
    Opt("sound.music", "float", 1.0, "Music level", "音乐音量", 0.0, 2.0),
    Opt("sound.clicks", "float", 1.0, "Click level", "点击声音量", 0.0, 2.0),
    Opt("sound.loudness", "float", -16.0, "Loudness (LUFS)", "响度（LUFS）", -30.0, -9.0),
    Opt("sound.track", "text", "", "Your own music file (replaces the score)", "自己的音乐文件（替换配乐）", web=False),
    # -------------------------------------------------------------- encode
    Opt("encode.codec", "choice", "h264", "MP4 codec", "MP4 编码", choices=("h264", "h265")),
    Opt("encode.crf", "int", 0, "Constant quality (0 = by quality)", "恒定画质（0 按画质）", 0, 51),
    Opt("encode.preset", "choice", "medium", "Encoder effort", "编码力度",
        choices=("ultrafast", "veryfast", "fast", "medium", "slow", "veryslow")),
    Opt("encode.audio_kbps", "int", 192, "Audio bitrate (kbps)", "音频码率（kbps）", 64, 512),
    Opt("encode.gif_width", "int", 960, "GIF long side", "GIF 长边像素", 160, 1920),
    Opt("encode.gif_fps", "int", 24, "GIF frame rate", "GIF 帧率", 5, 50),
    Opt("encode.chapters", "bool", True, "Chapter markers", "视频章节标记"),
    # -------------------------------------------------------------- output
    Opt("output.poster", "bool", True, "Swarm poster", "鼠标轨迹海报"),
    Opt("output.stills", "bool", True, "Photos", "多机位照片"),
    Opt("output.sheet", "bool", True, "Contact sheet", "接触印样"),
    Opt("output.report", "bool", True, "JSON report", "JSON 报告"),
]
OPTS = {o.key: o for o in _T}

# A preset is a bundle of options plus optional film settings ("film" key).
PRESETS = {
    "shorts": dict(zh="竖屏短视频", en="30-second vertical film",
                   film=dict(aspect="9:16", duration=30.0), opt={"show.turns": 1, "camera.margin": 0.12}),
    "cinema": dict(zh="电影感", en="Widescreen, graded, unhurried",
                   film=dict(), opt={"look.letterbox": "2.39", "look.grade": "film", "look.grain": 0.22, "look.vignette": 0.35,
                                     "camera.lens": 70.0, "ui.layout": "focus", "swarm.chips": False, "sound.mood": "calm"}),
    "neon": dict(zh="霓虹", en="Magenta and cyan at night",
                 film=dict(), opt={"look.theme": "neon", "look.lights": "neon", "look.grade": "vivid", "look.bloom": 0.6,
                                   "swarm.glow": 0.8, "sound.mood": "drive"}),
    "blueprint": dict(zh="蓝图", en="White lines on engineering blue",
                      film=dict(), opt={"look.theme": "blueprint", "look.lights": "clean", "look.grade": "cool",
                                        "look.grain": 0.0, "show.wipe": True, "sound.mood": "calm"}),
    "noir": dict(zh="黑白", en="Hard light, black and white",
                 film=dict(), opt={"look.theme": "graphite", "look.lights": "noir", "look.grade": "noir",
                                   "look.grain": 0.3, "look.vignette": 0.5, "swarm.palette": "ffffff,bbbbbb,888888"}),
    "paper": dict(zh="纸面（亮色）", en="A bright, quiet studio",
                  film=dict(), opt={"look.theme": "paper", "look.lights": "clean", "look.grade": "clean", "look.grain": 0.04,
                                    "look.vignette": 0.08, "sound.mood": "bright"}),
    "hacker": dict(zh="终端", en="Green on black",
                   film=dict(), opt={"look.theme": "terminal", "look.grade": "cool", "swarm.skins": "pixel,hollow",
                                     "sound.mood": "drive"}),
    "pure": dict(zh="只看模型", en="No interface, just the model and the mice",
                 film=dict(), opt={"ui.layout": "clean", "swarm.chips": False, "swarm.names": 0, "pace.render": False}),
    "quick": dict(zh="快速试片", en="Fast and rough, for checking settings",
                  film=dict(quality="draft", res="720p"), opt={"look.grain": 0.0}),
}


def _as_bool(v):
    if isinstance(v, bool):
        return v
    s = str(v).strip().lower()
    if s in ("1", "true", "yes", "on", "y"):
        return True
    if s in ("0", "false", "no", "off", "n", ""):
        return False
    raise ValueError("expected true or false, got %r" % (v,))


def _hex(c):
    c = str(c).strip().lstrip("#").lower()
    if not re.fullmatch(r"[0-9a-f]{6}", c):
        raise ValueError("expected a colour like ff8a3d, got %r" % c)
    return c


def coerce(o: Opt, v):
    """A value of the right type and range, or ValueError with a usable message."""
    try:
        if o.kind == "bool":
            return _as_bool(v)
        if o.kind in ("int", "float"):
            x = float(v)
            if not math.isfinite(x):
                raise ValueError("must be finite")
            x = max(o.lo, min(o.hi, x))
            return int(round(x)) if o.kind == "int" else x
        if o.kind == "choice":
            s = str(v).strip().lower()
            if s not in o.choices:
                raise ValueError("choose one of: %s" % ", ".join(o.choices))
            return s
        if o.kind == "color":
            return "" if str(v).strip() == "" else _hex(v)
        if o.kind == "colors":
            items = v if isinstance(v, (list, tuple)) else str(v).split(",")
            return ",".join(_hex(x) for x in items if str(x).strip())
        if o.kind == "list":
            items = v if isinstance(v, (list, tuple)) else str(v).split(",")
            items = [str(x).strip().lower() for x in items if str(x).strip()]
            bad = [x for x in items if x not in o.choices]
            if bad:
                raise ValueError("unknown %s; choose from: %s" % (", ".join(bad), ", ".join(o.choices)))
            return ",".join(dict.fromkeys(items))
        return str(v)[:400]
    except (TypeError, ValueError) as e:
        raise ValueError("%s: %s" % (o.key, e))


def defaults():
    return {k: o.default for k, o in OPTS.items()}


def resolve(*layers):
    """Merges option dicts, later ones winning, over the defaults."""
    out = defaults()
    for layer in layers:
        for k, v in (layer or {}).items():
            if k not in OPTS:
                close = [c for c in OPTS if k.split(".")[-1] in c]
                raise ValueError("Unknown option %r%s" % (k, "; did you mean %s?" % close[0] if close else ""))
            out[k] = coerce(OPTS[k], v)
    return out


def items_of(opt, key):
    return [x for x in str(opt[key]).split(",") if x]


def parse_assignments(pairs):
    out = {}
    for p in pairs or ():
        if "=" not in p:
            raise ValueError("--set needs key=value, got %r" % p)
        k, v = p.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def load_config(path):
    """A TOML or JSON file: [film] holds command-line settings, every other
    table holds options, e.g. [look] theme = "neon"."""
    with open(path, "rb") as f:
        raw = f.read()
    if os.path.splitext(path)[1].lower() == ".json":
        doc = json.loads(raw.decode("utf-8"))
    else:
        try:
            import tomllib
        except ModuleNotFoundError:
            import tomli as tomllib      # Python 3.10
        doc = tomllib.loads(raw.decode("utf-8"))
    film = dict(doc.pop("film", {}))
    opt = {}
    for group, table in doc.items():
        if group == "preset":
            film["preset"] = table
        elif isinstance(table, dict):
            for k, v in table.items():
                opt["%s.%s" % (group, k)] = v
        else:
            opt[group] = table
    return film, opt


def markdown():
    """docs/OPTIONS.md, generated."""
    lines = ["# Advanced options", "",
             "Generated from `mousestorm/options.py`. Set them with `--set key=value`, in a config file (`--config film.toml`) or in the app's Advanced panel.", "",
             "```toml", "# film.toml", "[film]", "speed = 3", 'aspect = "9:16"', "", "[look]", 'theme = "neon"', "bloom = 0.6", "", "[camera]", "lens = 85", "```", "",
             "## Presets", "", "| Name | Result |", "| --- | --- |"]
    for k, p in PRESETS.items():
        lines.append("| `%s` | %s |" % (k, p["en"]))
    for g, en, _ in GROUPS:
        lines += ["", "## %s" % en, "", "| Option | Default | Allowed | Meaning |", "| --- | --- | --- | --- |"]
        for o in _T:
            if not o.key.startswith(g + "."):
                continue
            if o.kind in ("int", "float"):
                allowed = "%g to %g" % (o.lo, o.hi)
            elif o.kind == "bool":
                allowed = "true, false"
            elif o.choices:
                allowed = ", ".join("`%s`" % c for c in o.choices)
            elif o.kind == "color":
                allowed = "hex colour"
            elif o.kind == "colors":
                allowed = "hex colours, comma separated"
            else:
                allowed = "text"
            default = "`%s`" % o.default if o.default != "" else "theme / automatic"
            lines.append("| `%s` | %s | %s | %s |" % (o.key, default, allowed, o.en))
    return "\n".join(lines) + "\n"


def as_json():
    """What the app needs to build its Advanced panel."""
    return dict(
        groups=[dict(id=g, en=en, zh=zh) for g, en, zh in GROUPS],
        options=[dict(key=o.key, kind=o.kind, default=o.default, en=o.en, zh=o.zh, lo=o.lo, hi=o.hi, choices=list(o.choices))
                 for o in _T if o.web],
        presets=[dict(id=k, en=p["en"], zh=p["zh"], opt=p["opt"], film=p["film"]) for k, p in PRESETS.items()],
    )
