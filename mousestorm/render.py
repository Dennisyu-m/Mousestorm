# SPDX-License-Identifier: GPL-3.0-or-later
"""Pipeline: extract → choreograph → Cycles beauty → frames → encode."""
import hashlib
import json
import multiprocessing as mp
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import uuid
from glob import glob
from multiprocessing import shared_memory

HERE = os.path.dirname(os.path.abspath(__file__))
FORMATS = ("mp4", "webm", "mov", "gif", "webp", "png")
MIN_BLENDER = (4, 2)
from .hardware import available_ram_gb, resolve_device, safe_workers

SCHEMA = 4          # bump when beauty.py changes what it renders
NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


class Cancelled(Exception):
    """The user asked to stop."""


class MousestormError(Exception):
    """A problem worth showing to the user without a traceback."""


# ------------------------------------------------------------ niceness
def be_polite():
    """Stay out of the way of whatever else the machine is doing (e.g. a
    training job). Children inherit this priority."""
    try:
        if sys.platform == "win32":
            import ctypes
            BELOW_NORMAL = 0x00004000
            handle = ctypes.windll.kernel32.GetCurrentProcess()
            ctypes.windll.kernel32.SetPriorityClass(handle, BELOW_NORMAL)
        else:
            os.nice(10)
    except Exception:
        pass


def cpu_plan(workers=0, threads=0):
    total = os.cpu_count() or 4
    w = workers if workers > 0 else max(1, int(total * 0.55))
    th = threads if threads > 0 else max(1, int(total * 0.55))
    return w, th


# ---------------------------------------------------------------- tools
def find_blender(explicit=None):
    candidates = []
    if explicit:
        candidates.append(explicit)
    if os.environ.get("MOUSESTORM_BLENDER"):
        candidates.append(os.environ["MOUSESTORM_BLENDER"])
    w = shutil.which("blender")
    if w:
        candidates.append(w)
    if sys.platform == "win32":
        patterns = [r"C:\Program Files\Blender Foundation\Blender*\blender.exe",
                    *[r"%s:\tools\blender-*\blender.exe" % drive for drive in "CDEF"],
                    os.path.expandvars(r"%LOCALAPPDATA%\Programs\Blender\*\blender.exe")]
    elif sys.platform == "darwin":
        patterns = ["/Applications/Blender.app/Contents/MacOS/Blender"]
    else:
        patterns = ["/usr/bin/blender", "/opt/blender*/blender", os.path.expanduser("~/blender*/blender")]
    for pat in patterns:
        candidates += sorted(glob(pat), reverse=True)
    for c in candidates:
        if c and os.path.isfile(c):
            return c
    raise FileNotFoundError(
        "Blender not found. Install it from blender.org, or set MOUSESTORM_BLENDER / pass --blender PATH.")


def find_ffmpeg():
    w = shutil.which("ffmpeg")
    if w:
        return w
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def blender_env(device):
    env = dict(os.environ)
    if device != "gpu":
        env["CUDA_VISIBLE_DEVICES"] = "-1"  # guarantees no GPU memory is touched
        env["OPTIX_FORCE_DEPRECATED_LAUNCH"] = "0"
    return env


def blender_version(blender):
    """(major, minor, patch) of an executable, or None if it will not say."""
    try:
        out = subprocess.run([blender, "--version"], capture_output=True, text=True, encoding="utf-8",
                             errors="replace", timeout=60, creationflags=NO_WINDOW).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.search(r"Blender\s+(\d+)\.(\d+)(?:\.(\d+))?", out)
    return (int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)) if m else None


def run_blender(blender, blend, script, args, device, on_line=None, cancel=None):
    cmd = [blender, "-b", "--factory-startup", "--disable-autoexec", blend,
           "--python-exit-code", "9", "-P", os.path.join(HERE, "bscripts", script), "--"] + args
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=blender_env(device),
                            text=True, encoding="utf-8", errors="replace", creationflags=NO_WINDOW)
    done = threading.Event()
    if cancel is not None:
        def watch():
            while not done.wait(0.25):
                if cancel.is_set():
                    proc.kill()
                    return
        threading.Thread(target=watch, daemon=True).start()
    tail = []
    try:
        for line in proc.stdout:
            line = line.rstrip()
            tail.append(line)
            tail = tail[-60:]
            if on_line and "[mousestorm]" in line:
                on_line(line)
        code = proc.wait()
    finally:
        done.set()
    if cancel is not None and cancel.is_set():
        raise Cancelled()
    if code == 3 and script == "extract.py":
        raise MousestormError("This file has no renderable geometry (no visible mesh, curve or text objects).")
    if code != 0:
        text = "\n".join(tail)
        if re.search(r"Cannot read file|Failed to read blend|Unable to open|File format is not supported|not a blend file", text, re.I):
            raise MousestormError("Blender could not open this file. It may be damaged, or saved by a newer Blender than the one installed.")
        raise RuntimeError("Blender failed (%s, exit %d):\n%s" % (script, code, "\n".join(tail[-30:])))


# -------------------------------------------------------------- frame workers
def worker_main(work, shm_name, slot_size, tasks, results):
    be_polite()
    for var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMBA_NUM_THREADS"):
        os.environ[var] = "1"
    shm = None
    try:
        from .frame import FrameRenderer
        shm = shared_memory.SharedMemory(name=shm_name)
        renderer = FrameRenderer(work)
        while True:
            job = tasks.get()
            if job is None:
                break
            frame, slot = job
            a = renderer.frame(frame)
            shm.buf[slot * slot_size:(slot + 1) * slot_size] = a.tobytes()
            results.put(("ok", frame, slot))
    except Exception:
        results.put(("err", traceback.format_exc(), -1))
    finally:
        if shm is not None:
            shm.close()


def fit_long_side(W, H, cap):
    """Scales down to fit, never up, and stays even."""
    k = min(1.0, cap / max(W, H))
    return int(W * k) & ~1, int(H * k) & ~1


def encoder_cmd(ffmpeg, s, out, audio, threads, meta=None):
    W, H, fps = s.width, s.height, s.fps
    opt = s.opt or {}
    base = [ffmpeg, "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgba",
            "-s", "%dx%d" % (W, H), "-r", str(fps), "-i", "-"]
    a_in = ["-i", audio] if audio else []
    if meta and s.fmt in ("mp4", "webm", "mov"):
        a_in += ["-i", meta, "-map_metadata", "2" if audio else "1", "-map", "0:v"] + (["-map", "1:a"] if audio else [])
    tail = ["-threads", str(threads)]
    kbps = "%dk" % int(opt.get("encode.audio_kbps", 192))
    crf_user = int(opt.get("encode.crf", 0))
    if s.fmt == "mp4":
        crf = crf_user or {"draft": 23, "standard": 17, "high": 14}.get(s.quality, 18)
        h265 = opt.get("encode.codec") == "h265"
        v = ["-c:v", "libx265" if h265 else "libx264", "-preset", opt.get("encode.preset", "medium"), "-crf", str(crf),
             "-pix_fmt", "yuv420p", "-movflags", "+faststart"] + (["-tag:v", "hvc1", "-x265-params", "log-level=error"] if h265 else [])
        return base + a_in + v + (["-c:a", "aac", "-b:a", kbps] if audio else []) + tail + [out]
    if s.fmt == "webm":
        v = ["-c:v", "libvpx-vp9", "-crf", str(crf_user or 24), "-b:v", "0", "-row-mt", "1", "-cpu-used", "2", "-pix_fmt", "yuv420p"]
        return base + a_in + v + (["-c:a", "libopus", "-b:a", kbps] if audio else []) + tail + [out]
    if s.fmt == "mov":
        v = ["-c:v", "prores_ks", "-profile:v", "3", "-pix_fmt", "yuv422p10le"]
        return base + a_in + v + (["-c:a", "pcm_s16le"] if audio else []) + tail + [out]
    if s.fmt == "gif":
        vf = "fps=%d,scale=%d:%d:flags=lanczos,split[a][b];[a]palettegen=max_colors=220:stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=3" % (
            (int(opt.get("encode.gif_fps", 24)),) + fit_long_side(W, H, int(opt.get("encode.gif_width", 960))))
        return base + ["-vf", vf, "-loop", "0"] + tail + [out]
    if s.fmt == "webp":
        return base + ["-vf", "fps=30,scale=%d:%d:flags=lanczos" % fit_long_side(W, H, 1280), "-c:v", "libwebp_anim",
                       "-q:v", "86", "-loop", "0"] + tail + [out]
    if s.fmt == "png":
        os.makedirs(out, exist_ok=True)
        return base + [os.path.join(out, "%06d.png")]
    raise ValueError("Unknown format: %s" % s.fmt)


def write_chapters(work, pl):
    """FFMETADATA with one chapter per phase of the film, so players can jump around."""
    opt = pl.settings.opt
    if not opt.get("encode.chapters", True):
        return None

    def esc(s):
        return re.sub(r"([=;#\\\n])", r"\\\1", str(s))

    marks = [("Modelling", pl.T["model"][0]), ("Shading", pl.T["shade"][0]), ("Lighting", pl.T["light"][0]), ("Rendering", pl.T["render"][0])]
    if not opt["pace.shading"]:
        marks = [m for m in marks if m[0] != "Shading"]
    if not opt["pace.lighting"]:
        marks = [m for m in marks if m[0] != "Lighting"]
    if not opt["pace.render"]:
        marks = [m for m in marks if m[0] != "Rendering"]
    names = dict(hero="Hero shot", turntable="Turntable", shoot="Photo shoot", poster="Swarm poster", sheet="Contact sheet")
    marks += [(names[n], pl.show0 + t0) for n, t0, _ in pl.schedule]
    title = opt["title.text"] or os.path.splitext(pl.source)[0]
    lines = [";FFMETADATA1", "title=" + esc(title), "comment=Made with Mousestorm", ""]
    for i, (name, t0) in enumerate(marks):
        end = marks[i + 1][1] if i + 1 < len(marks) else pl.duration
        if end - t0 < 0.05:
            continue
        lines += ["[CHAPTER]", "TIMEBASE=1/1000", "START=%d" % int(t0 * 1000), "END=%d" % int(end * 1000), "title=" + esc(name), ""]
    path = os.path.join(work, "chapters.txt")
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines))
    return path


def encode_film(work, pl, out, audio, log, cancel=None):
    s = pl.settings
    ffmpeg = find_ffmpeg()
    W, H = s.width, s.height
    n_frames = int(pl.duration * s.fps)
    workers, _ = cpu_plan(s.workers, s.threads)
    if s.workers <= 0:                      # the number nobody asked for must fit in this machine's memory
        workers = safe_workers(workers, available_ram_gb(), s.width, s.height, s.ss)
    workers = max(1, min(workers, n_frames))
    log("frames", 0.0, "Rendering %d frames on %d CPU workers" % (n_frames, workers))

    slot_size = W * H * 4
    n_slots = max(4, workers * 2)
    shm = shared_memory.SharedMemory(create=True, size=slot_size * n_slots, name="ms_" + uuid.uuid4().hex[:12])
    ctx = mp.get_context("spawn")
    tasks, results = ctx.Queue(), ctx.Queue()
    procs = [ctx.Process(target=worker_main, args=(work, shm.name, slot_size, tasks, results), daemon=True)
             for _ in range(workers)]
    for p in procs:
        p.start()

    cmd = encoder_cmd(ffmpeg, s, out, audio, max(4, min(16, workers)), write_chapters(work, pl))
    errlog = tempfile.TemporaryFile()
    ff = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=errlog, creationflags=NO_WINDOW)

    def ffmpeg_failed():
        errlog.seek(0)
        msg = errlog.read().decode("utf-8", "replace").strip()
        return RuntimeError("ffmpeg stopped (exit %s):\n%s" % (ff.poll(), msg[-2000:] or "(no message)"))

    next_assign, next_write = 0, 0
    free = list(range(n_slots))
    ready = {}
    t_start = time.time()
    t_preview = 0.0
    view = memoryview(shm.buf)
    try:
        while next_write < n_frames:
            while free and next_assign < n_frames:
                tasks.put((next_assign, free.pop()))
                next_assign += 1
            while True:
                if cancel is not None and cancel.is_set():
                    raise Cancelled()
                try:
                    kind, a, b = results.get(timeout=1.0)
                    break
                except queue.Empty:
                    # a process killed by the OS never reports anything; do not wait for it forever
                    dead = [p for p in procs if not p.is_alive()]
                    if dead:
                        raise RuntimeError("A frame worker died (exit code %s). The machine may be out of memory: "
                                           "try fewer workers with --workers, or a lower resolution." % dead[0].exitcode)
                    if ff.poll() is not None:
                        raise ffmpeg_failed()
            if kind == "err":
                raise RuntimeError("Frame worker failed:\n" + a)
            ready[a] = b
            while next_write in ready:
                slot = ready.pop(next_write)
                try:
                    ff.stdin.write(view[slot * slot_size:(slot + 1) * slot_size])
                except OSError:
                    ff.wait()
                    raise ffmpeg_failed()
                if time.time() - t_preview > 0.6:
                    t_preview = time.time()
                    from PIL import Image
                    raw = bytes(view[slot * slot_size:(slot + 1) * slot_size])
                    Image.frombuffer("RGBA", (W, H), raw, "raw", "RGBA", 0, 1).convert("RGB").resize(
                        (640, 360)).save(os.path.join(work, "preview.tmp.jpg"), quality=74)
                    os.replace(os.path.join(work, "preview.tmp.jpg"), os.path.join(work, "preview.jpg"))
                free.append(slot)
                next_write += 1
                if next_write % 30 == 0 or next_write == n_frames:
                    elapsed = time.time() - t_start
                    log("frames", next_write / n_frames, "%d / %d frames · %.1f fps" % (
                        next_write, n_frames, next_write / max(elapsed, 1e-3)))
        ff.stdin.close()
        ff.wait()
        if ff.returncode:
            raise ffmpeg_failed()
    finally:
        finished = next_write >= n_frames
        for _ in procs:
            tasks.put(None)
        for p in procs:
            p.join(timeout=8 if finished else 0.2)
            if p.is_alive():
                p.terminate()
                p.join(timeout=5)
        view.release()
        shm.close()
        try:
            shm.unlink()
        except FileNotFoundError:
            pass
        if ff.poll() is None:
            ff.kill()
            ff.wait()
        errlog.close()
        tasks.cancel_join_thread()
        results.cancel_join_thread()


# ---------------------------------------------------------------- caching
def cache_root():
    if os.environ.get("MOUSESTORM_CACHE"):
        return os.environ["MOUSESTORM_CACHE"]
    if sys.platform == "win32":
        return os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "Mousestorm", "cache")
    if sys.platform == "darwin":
        return os.path.expanduser("~/Library/Caches/Mousestorm")
    return os.path.join(os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"), "mousestorm")


def file_digest(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def beauty_key(blend, shots, version):
    """What the Cycles shots depend on, and nothing else: re-filming at another
    speed or with more mice can reuse them."""
    relevant = {k: shots.get(k) for k in ("width", "height", "samples", "turn_samples", "device", "rig", "stills", "turntable",
                                          "exposure", "denoise", "bounces", "view_transform")}
    text = json.dumps([SCHEMA, version, file_digest(blend), relevant], sort_keys=True)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:24]


def prune_cache(root, keep, limit_gb=None):
    """Oldest first, until the cache fits. Never touches the entry in use."""
    limit = float(os.environ.get("MOUSESTORM_CACHE_GB") or 6.0) if limit_gb is None else limit_gb
    entries = []
    for name in os.listdir(root):
        path = os.path.join(root, name)
        if not os.path.isdir(path):
            continue
        size = sum(os.path.getsize(os.path.join(a, f)) for a, _, fs in os.walk(path) for f in fs)
        entries.append((os.path.getmtime(path), size, path))
    total = sum(e[1] for e in entries)
    for _, size, path in sorted(entries):
        if total <= limit * 1024 ** 3:
            break
        if os.path.normcase(path) != os.path.normcase(keep):
            shutil.rmtree(path, ignore_errors=True)
            total -= size


def check_disk(path, s, duration):
    """Refuses a job that would obviously run out of room, before hours are spent on it."""
    free = shutil.disk_usage(path).free
    per_frame = s.width * s.height * (3.2 if s.fmt == "png" else 0.4 if s.fmt == "mov" else 0.0)
    need = 3e8 + per_frame * int(duration * s.fps) + s.width * s.height * 0.9 * 110
    if free < need:
        raise MousestormError("Not enough disk space: about %.1f GB needed, %.1f GB free at %s."
                              % (need / 1e9, free / 1e9, path))


# -------------------------------------------------------------- the pipeline
def resolve_output(out, fmt):
    """Keeps the file extension and the format in agreement."""
    if fmt == "png":
        return out[:-4] if out.lower().endswith(".png") else out
    root, ext = os.path.splitext(out)
    return out if ext.lower() == "." + fmt else (root if ext.lower().lstrip(".") in FORMATS else out) + "." + fmt


def run(blend, out, settings, blender=None, log=None, keep_work=False, work=None, cancel=None, use_cache=True,
        storyboard=False):
    from .plan import normalized
    be_polite()
    log = log or (lambda stage, frac, msg: print("[%s %5.1f%%] %s" % (stage, frac * 100, msg), flush=True))
    settings.fmt = settings.fmt.lower()
    if settings.fmt not in FORMATS:
        raise ValueError("format must be one of %s" % ", ".join(FORMATS))
    normalized(settings)
    blend = os.path.abspath(blend)
    out = resolve_output(os.path.abspath(out), settings.fmt)
    if not os.path.isfile(blend):
        raise FileNotFoundError("File not found: %s" % blend)
    out_dir = out if settings.fmt == "png" else os.path.dirname(out)
    os.makedirs(out_dir or ".", exist_ok=True)
    blender = find_blender(blender)
    version = blender_version(blender)
    if version is not None and version[:2] < MIN_BLENDER:
        raise MousestormError("Blender %d.%d is too old; Mousestorm needs %d.%d or newer." % (*version[:2], *MIN_BLENDER))
    work = work or tempfile.mkdtemp(prefix="mousestorm-", dir=out_dir)
    os.makedirs(work, exist_ok=True)
    try:
        if storyboard:
            return _storyboard(blend, out, work, settings, blender, log, keep_work, cancel)
        return _pipeline(blend, out, out_dir, work, settings, blender, log, keep_work, cancel, use_cache, version)
    except BaseException:
        if not keep_work:
            _remove_work(work)
        raise


def _remove_work(work):
    import gc
    for delay in (0.0, 0.3, 1.0):
        gc.collect()
        time.sleep(delay)
        shutil.rmtree(work, ignore_errors=True)
        if not os.path.exists(work):
            return


def _storyboard(blend, out, work, settings, blender, log, keep_work, cancel):
    """A six-panel preview of the build, in seconds: no Cycles, no encoding.
    It is the quick way to judge options before committing to a full render."""
    from .plan import build_plan
    from .plan_finish import save as save_plan
    from .scene import SceneData
    log("extract", 0.0, "Reading %s" % os.path.basename(blend))
    run_blender(blender, blend, "extract.py", [work], settings.device, cancel=cancel)
    log("plan", 0.0, "Choreographing the swarm")
    d = SceneData(work)
    pl, arrays = build_plan(d, settings)
    save_plan(pl, arrays, work)
    from PIL import Image, ImageDraw
    from .frame import FrameRenderer
    fr = FrameRenderer(work)
    build_end = pl.T["render"][0]
    marks = [("%d%%" % int(k * 100), build_end * k) for k in (0.08, 0.3, 0.55, 0.78)]
    marks += [("Shading", (pl.T["shade"][0] + pl.T["shade"][1]) / 2), ("Lighting", (pl.T["light"][0] + pl.T["light"][1]) / 2)]
    marks = sorted(marks[:4] + marks[4:], key=lambda m: m[1])
    tiles = []
    for j, (_, t) in enumerate(marks):
        log("frames", j / len(marks), "Storyboard %d / %d" % (j + 1, len(marks)))
        a = fr.frame(int(t * settings.fps))
        tiles.append(Image.fromarray(a[..., :3].copy()))
    w, h = tiles[0].size
    cols, rows = (3, 2) if w >= h else (6, 1)
    gap, head = 12, 56
    sheet = Image.new("RGB", (cols * w + (cols + 1) * gap, rows * h + (rows + 1) * gap + head), (12, 13, 16))
    draw = ImageDraw.Draw(sheet)
    draw.text((gap, 16), "%s  |  %d mice  |  %s ops  |  x%.2f  |  film %.1f s" % (
        os.path.basename(blend), pl.n_mice, format(pl.n_ops, ","), settings.speed, pl.duration), fill=(235, 238, 245))
    for k, im in enumerate(tiles):
        row, col = divmod(k, cols)
        x, y = gap + col * (w + gap), head + gap + row * (h + gap)
        sheet.paste(im, (x, y))
    base = out if settings.fmt == "png" else os.path.splitext(out)[0]
    path = base + "_storyboard.png"
    sheet.save(path)
    log("done", 1.0, "Storyboard ready")
    info = dict(video=None, storyboard=path, stills=None, sheet=None, duration=pl.duration, mice=pl.n_mice, ops=pl.n_ops, speed=settings.speed)
    fr = d = None
    if not keep_work:
        _remove_work(work)
    return info


def write_report(path, pl, d, settings, t0):
    totals = d.doc["totals"]
    kinds = {}
    for k in pl.op_kind:
        kinds[k] = kinds.get(k, 0) + 1
    names = {}
    for label in pl.op_label:
        names[label] = names.get(label, 0) + 1
    report = dict(mousestorm=__import__("mousestorm").__version__, source=pl.source, objects=len(d.parts), vertices=totals["verts"],
                  polygons=totals["polys"], materials=len(d.materials), mice=pl.n_mice, operations=pl.n_ops, operations_by_kind=kinds,
                  operations_by_name=dict(sorted(names.items(), key=lambda kv: -kv[1])), operations_at_film_speed=int((pl.op_boost < 1.05).sum()),
                  step_log=pl.step_report,
                  speed=settings.speed, film_seconds=pl.duration, width=settings.width, height=settings.height, fps=settings.fps,
                  format=settings.fmt, quality=settings.quality, phases={k: list(v) for k, v in pl.T.items()},
                  finale=[dict(segment=n, start=pl.show0 + s, seconds=l) for n, s, l in pl.schedule],
                  wall_clock_minutes=(time.time() - t0) / 60.0, options=settings.opt)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)


def _pipeline(blend, out, out_dir, work, settings, blender, log, keep_work, cancel, use_cache, version):
    t0 = time.time()

    workers, threads = cpu_plan(settings.workers, settings.threads)
    automatic_device = resolve_device(settings, blender, log)
    log("extract", 0.0, "Reading %s" % os.path.basename(blend))
    run_blender(blender, blend, "extract.py", [work], settings.device, cancel=cancel)
    log("extract", 1.0, "Scene extracted")

    log("plan", 0.0, "Choreographing the swarm")
    from .plan import build_plan
    from .plan_finish import save as save_plan
    from .scene import SceneData
    d = SceneData(work)
    pl, arrays = build_plan(d, settings)
    check_disk(out_dir, settings, pl.duration)
    log("plan", 1.0, "%d mice · %s operations · ×%.2f · %.1f s" % (pl.n_mice, format(pl.n_ops, ","), settings.speed, pl.duration))

    shots = dict(width=settings.width, height=settings.height, threads=threads,
                 samples=pl.q["stills"], turn_samples=pl.q["turn"], device=settings.device, gpu=settings.gpu,
                 exposure=float(settings.opt["look.exposure"]), denoise=bool(settings.opt["render.denoise"]),
                 bounces=int(settings.opt["render.bounces"]), view_transform=settings.opt["look.view_transform"],
                 rig=pl.rig, stills=pl.stills, turntable=pl.turntable)
    beauty_dir = work
    cache_dir = None
    if use_cache:
        try:
            root = cache_root()
            os.makedirs(root, exist_ok=True)
            cache_dir = os.path.join(root, beauty_key(blend, json.loads(json.dumps(shots)), version))
            os.makedirs(cache_dir, exist_ok=True)
            beauty_dir = cache_dir
        except OSError:
            cache_dir, beauty_dir = None, work
    shots["out"] = beauty_dir
    pl.beauty_dir = beauty_dir
    save_plan(pl, arrays, work)
    with open(os.path.join(work, "shots.json"), "w", encoding="utf-8") as f:
        json.dump(shots, f, ensure_ascii=False)

    def beauty_line(line):
        m = re.search(r"beauty (\d+)/(\d+)", line)
        if m:
            done, total = int(m.group(1)), int(m.group(2))
            log("beauty", done / total, "Cycles (%s) %d / %d" % (settings.device.upper(), done, total))

    log("beauty", 0.0, "Rendering photo-real shots with Cycles (%s)" % settings.device.upper())
    try:
        run_blender(blender, blend, "beauty.py", [work], settings.device, on_line=beauty_line, cancel=cancel)
    except MousestormError:
        if not (automatic_device and settings.device == "gpu"):
            raise
        # most often the card ran out of memory; the film is still worth making
        log("beauty", 0.0, "The GPU could not finish; rendering on the CPU instead")
        settings.device, settings.gpu = "cpu", ""
        shots.update(device="cpu", gpu="")
        with open(os.path.join(work, "shots.json"), "w", encoding="utf-8") as f:
            json.dump(shots, f, ensure_ascii=False)
        run_blender(blender, blend, "beauty.py", [work], "cpu", on_line=beauty_line, cancel=cancel)
    if cache_dir:
        os.utime(cache_dir)
        prune_cache(cache_root(), cache_dir)

    audio = None
    if settings.sound and settings.fmt in ("mp4", "webm", "mov"):
        from .sound import synth
        log("frames", 0.0, "Composing the soundtrack")
        audio = os.path.join(work, "sound.wav")
        synth(pl, audio)

    encode_film(work, pl, out, audio, log, cancel)

    # deliverables next to the film
    base = out if settings.fmt == "png" else os.path.splitext(out)[0]
    opt = settings.opt
    stills_out = None
    if opt["output.stills"]:
        stills_out = base + "_stills"
        os.makedirs(stills_out, exist_ok=True)
        for i, shot in enumerate(pl.stills):
            shutil.copyfile(os.path.join(beauty_dir, "stills", "%02d.png" % i),
                            os.path.join(stills_out, "%02d_%s.png" % (i + 1, shot["name"])))
    from .frame import FrameRenderer
    fr = FrameRenderer(work)
    sheet = poster = None
    if opt["output.sheet"]:
        sheet = base + "_contact_sheet.png"
        fr.show.export_sheet(sheet)
    if opt["output.poster"]:
        poster = base + "_poster.png"
        fr.show.export_poster(poster)
    if opt["output.report"]:
        write_report(base + "_report.json", pl, d, settings, t0)
    log("done", 1.0, "Finished in %.1f min" % ((time.time() - t0) / 60.0))
    video_info = dict(video=out, stills=stills_out, sheet=sheet, poster=poster, duration=pl.duration, mice=pl.n_mice, ops=pl.n_ops,
                      speed=settings.speed)
    fr = d = None   # drop memory-mapped arrays, Windows cannot delete mapped files
    if not keep_work:
        _remove_work(work)
    return video_info
