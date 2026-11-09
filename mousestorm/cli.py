# SPDX-License-Identifier: GPL-3.0-or-later
import argparse
import math
import os
import shutil
import sys
from glob import glob

from . import __version__
from . import options as O
from . import hardware
from .steps import EXTENSION as STEPS_EXT
from .steps import load as load_steps
from .plan import DEVICES, LIGHTINGS, QUALITY, SOUNDTRACKS, Settings
from .render import FORMATS, MIN_BLENDER, Cancelled, MousestormError, blender_version, find_blender, find_ffmpeg, run

SHORT_SIDE = {"720p": 720, "1080p": 1080, "1440p": 1440, "2160p": 2160, "4k": 2160}


def parse_aspect(s):
    try:
        a, b = s.replace("/", ":").split(":")
        a, b = float(a), float(b)
    except ValueError:
        raise ValueError("Aspect ratio must look like 16:9, 9:16 or 1:1 (got %r)" % s)
    if not (math.isfinite(a) and math.isfinite(b)) or a <= 0 or b <= 0 or not 0.2 <= a / b <= 5:
        raise ValueError("Aspect ratio %r is out of range" % s)
    return a / b


def parse_res(s, aspect=16 / 9):
    """'1080p' names the short side, so 1080p at 9:16 is 1080x1920."""
    key = s.lower()
    if key in SHORT_SIDE:
        short = SHORT_SIDE[key]
        if aspect >= 1.0:
            return int(round(short * aspect)) & ~1, short & ~1
        return short & ~1, int(round(short / aspect)) & ~1
    try:
        w, h = key.split("x")
        return int(w), int(h)
    except ValueError:
        raise ValueError("Resolution must be 720p, 1080p, 1440p, 4k or WxH (got %r)" % s)


def collect_inputs(items):
    files = []
    for item in items:
        if os.path.isdir(item):
            files += sorted(glob(os.path.join(item, "*.blend")))
        else:
            matches = glob(item) if any(ch in item for ch in "*?[") else [item]
            files += sorted(matches)
    return files


def check_steps(paths):
    """Validates step logs without rendering anything."""
    if not paths:
        print("Usage: mousestorm check-steps FILE%s" % STEPS_EXT, file=sys.stderr)
        return 2
    bad = 0
    for path in paths:
        try:
            log = load_steps(path)
        except (OSError, ValueError) as e:
            print("%s: invalid: %s" % (path, e), file=sys.stderr)
            bad += 1
            continue
        print("%s: valid, %d steps on %d objects" % (path, len(log["steps"]), len({st["object"] for st in log["steps"]})))
    return 1 if bad else 0


def doctor(blender=None):
    """Prints what Mousestorm found on this machine. Paste this into bug reports."""
    ok = True

    def line(name, good, detail):
        nonlocal ok
        ok = ok and good
        print("  [%s] %-11s %s" % ("ok" if good else "!!", name, detail))

    print("Mousestorm %s, Python %s" % (__version__, sys.version.split()[0]))
    try:
        path = find_blender(blender)
        v = blender_version(path)
        good = v is not None and v[:2] >= MIN_BLENDER
        line("Blender", good, "%s (%s)" % (path, ".".join(map(str, v)) if v else "version unknown"))
    except FileNotFoundError as e:
        line("Blender", False, str(e))
    try:
        ff = find_ffmpeg()
        import subprocess
        out = subprocess.run([ff, "-hide_banner", "-encoders"], capture_output=True, text=True).stdout
        want = {"libx264": "mp4", "libvpx-vp9": "webm", "prores_ks": "mov", "libwebp_anim": "webp", "gif": "gif"}
        missing = [fmt for enc, fmt in want.items() if enc not in out]
        line("ffmpeg", not missing, ff + ("" if not missing else "; cannot encode: " + ", ".join(missing)))
    except Exception as e:
        line("ffmpeg", False, str(e))
    for mod in ("numpy", "numba", "skia", "PIL"):
        try:
            m = __import__(mod)
            line(mod, True, getattr(m, "__version__", "?"))
        except Exception as e:
            line(mod, False, str(e))
    from .render import cache_root, cpu_plan
    w, th = cpu_plan()
    line("CPU", True, "%d logical cores; %d frame workers, %d Cycles threads by default" % (os.cpu_count() or 0, w, th))
    free = shutil.disk_usage(os.getcwd()).free / 1e9
    line("Disk", free > 2.0, "%.1f GB free here" % free)
    print("  Cache:      %s" % cache_root())
    return 0 if ok else 1


def build_parser():
    ap = argparse.ArgumentParser(
        prog="mousestorm",
        description="Turn any .blend file into a film of a swarm of mice building it.",
        epilog="Also: 'mousestorm doctor' checks your setup.")
    ap.add_argument("blend", nargs="*", help=".blend files or folders (omit to open the drag-and-drop app)")
    ap.add_argument("--version", action="version", version="Mousestorm " + __version__)
    ap.add_argument("-o", "--output", help="output path; with several inputs, a folder (default: next to the input)")
    ap.add_argument("-f", "--format", choices=FORMATS, help="default: taken from --output, else mp4")
    ap.add_argument("-s", "--speed", type=float, default=1.0, help="how fast the mice work (0.05 to 40)")
    ap.add_argument("-d", "--duration", type=float, default=0.0, help="target film length in seconds; picks the speed for you")
    ap.add_argument("-m", "--mice", type=int, default=0, help="number of mice (0 = automatic)")
    ap.add_argument("-q", "--quality", default="standard", choices=sorted(QUALITY))
    ap.add_argument("-r", "--res", default="1080p", help="720p, 1080p, 1440p, 4k (short side) or WxH")
    ap.add_argument("-a", "--aspect", default="16:9", help="16:9, 9:16 for phones, 1:1, ... (ignored with WxH)")
    ap.add_argument("--fps", type=int, default=60)
    ap.add_argument("--showcase", type=float, default=None, help="seconds of finale (default 20)")
    ap.add_argument("--lighting", default="auto", choices=LIGHTINGS)
    ap.add_argument("--device", default="auto", choices=DEVICES, help="what renders the photo-real shots; auto uses an idle GPU, else the CPU")
    ap.add_argument("--gpu", default="", metavar="N", help="with --device gpu, which card; see 'mousestorm devices' (default: all)")
    ap.add_argument("--soundtrack", default="score", choices=SOUNDTRACKS, help="score = music + clicks (default)")
    ap.add_argument("--no-sound", action="store_true")
    ap.add_argument("--no-cache", action="store_true", help="always re-render the Cycles shots")
    ap.add_argument("-p", "--preset", choices=sorted(O.PRESETS), help="a ready-made look; see 'mousestorm options'")
    ap.add_argument("-c", "--config", help="TOML or JSON file with film settings and options")
    ap.add_argument("--set", action="append", default=[], metavar="KEY=VALUE", help="set one advanced option; repeatable")
    ap.add_argument("--steps", metavar="FILE", help="optional %s step log recorded while modelling; see docs/STEPS_FORMAT.md" % STEPS_EXT)
    ap.add_argument("--storyboard", action="store_true", help="a quick six-panel preview: no Cycles, no film")
    ap.add_argument("--workers", type=int, default=0, help="frame worker processes (0 = 55%% of logical cores)")
    ap.add_argument("--threads", type=int, default=0, help="Cycles threads (0 = 55%% of logical cores)")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--blender", help="path to the Blender executable")
    ap.add_argument("--keep-work", action="store_true")
    ap.add_argument("--port", type=int, default=7878, help="port of the drag-and-drop app")
    ap.add_argument("--no-browser", action="store_true", help="do not open a browser for the app")
    return ap


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["doctor"]:
        blender = argv[argv.index("--blender") + 1] if "--blender" in argv[:-1] else None
        return doctor(blender)
    if argv[:1] == ["options"]:
        print(O.markdown())
        return 0
    if argv[:1] == ["web"]:                   # the same as giving no files, but says what it does
        argv = argv[1:]
    if argv[:1] == ["devices"]:
        try:
            blender = find_blender(argv[argv.index("--blender") + 1] if "--blender" in argv[:-1] else None)
        except FileNotFoundError:
            blender = None
        hardware.describe(blender)
        return 0
    if argv[:1] == ["check-steps"]:
        return check_steps(argv[1:])
    ap = build_parser()
    # a preset or config file supplies new defaults; explicit flags still win
    first, _ = ap.parse_known_args(argv)
    opt_layers = []
    try:
        cfg_film, cfg_opt = O.load_config(first.config) if first.config else ({}, {})
    except (OSError, ValueError) as e:
        print("Error: could not read config: %s" % e, file=sys.stderr)
        return 1
    preset_name = first.preset or cfg_film.pop("preset", None)
    film = {}
    if preset_name:
        if preset_name not in O.PRESETS:
            print("Error: unknown preset %r (have: %s)" % (preset_name, ", ".join(sorted(O.PRESETS))), file=sys.stderr)
            return 1
        film.update(O.PRESETS[preset_name]["film"])
        opt_layers.append(O.PRESETS[preset_name]["opt"])
    film.update(cfg_film)
    opt_layers.append(cfg_opt)
    allowed_film = {x.dest for x in ap._actions}
    unknown = sorted(set(film) - allowed_film)
    if unknown:
        print("Error: unknown [film] setting: %s" % ", ".join(unknown), file=sys.stderr)
        return 1
    ap.set_defaults(**film)
    a = ap.parse_args(argv)
    try:
        opt_layers.append(O.parse_assignments(a.set))
        merged = O.resolve(*opt_layers)
    except ValueError as e:
        print("Error: %s" % e, file=sys.stderr)
        return 1

    if not a.blend:
        from .server import serve
        serve(port=a.port, open_browser=not a.no_browser, blender=a.blender)
        return 0

    steps_path = a.steps
    named = [x for x in a.blend if x.lower().endswith(STEPS_EXT)]
    if named:                                 # both files named or dropped together
        if len(named) > 1 or a.steps:
            print("Error: give one step log only", file=sys.stderr)
            return 1
        steps_path = named[0]
    inputs = collect_inputs([x for x in a.blend if not x.lower().endswith(STEPS_EXT)])
    if steps_path and len(inputs) > 1:
        print("Error: a step log describes one .blend; give one input with it", file=sys.stderr)
        return 1
    if not inputs:
        print("Error: no .blend files found in %s" % ", ".join(a.blend), file=sys.stderr)
        return 1

    fmt = a.format
    if fmt is None and a.output and len(inputs) == 1:
        ext = os.path.splitext(a.output)[1].lower().lstrip(".")
        fmt = ext if ext in FORMATS else None
    fmt = fmt or "mp4"

    failures = 0
    for blend in inputs:
        try:
            w, h = parse_res(a.res, parse_aspect(a.aspect))
            # a short film should not spend most of its time on the finale
            showcase = a.showcase if a.showcase is not None else (
                min(20.0, max(6.0, a.duration * 0.34)) if a.duration > 0 else 20.0)
            s = Settings(width=w, height=h, fps=a.fps, speed=a.speed, mice=a.mice, fmt=fmt, quality=a.quality,
                         showcase=showcase, duration=a.duration, seed=a.seed, sound=not a.no_sound,
                         soundtrack=a.soundtrack, lighting=a.lighting, device=a.device, gpu=a.gpu,
                         workers=a.workers, threads=a.threads, opt=dict(merged), steps=steps_path or "")
            stem = os.path.splitext(os.path.abspath(blend))[0]
            default = stem + "_mousestorm" + ("" if fmt == "png" else "." + fmt)
            if len(inputs) > 1 and a.output:
                out = os.path.join(a.output, os.path.basename(default))
            else:
                out = a.output or default
            r = run(blend, out, s, blender=a.blender, keep_work=a.keep_work, use_cache=not a.no_cache, storyboard=a.storyboard)
        except (ValueError, FileNotFoundError, MousestormError) as e:
            print("Error (%s): %s" % (os.path.basename(blend), e), file=sys.stderr)
            failures += 1
            continue
        except Cancelled:
            return 130
        if r.get("storyboard"):
            print("\nStoryboard:    %s" % r["storyboard"])
            print("Film would be: %.1f s at x%.2f, %d mice, %s operations" % (r["duration"], r["speed"], r["mice"], format(r["ops"], ",")))
            continue
        print("\nFilm:          %s" % r["video"])
        for label, key in (("Stills", "stills"), ("Contact sheet", "sheet"), ("Poster", "poster")):
            if r.get(key):
                print("%-14s %s" % (label + ":", r[key]))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
