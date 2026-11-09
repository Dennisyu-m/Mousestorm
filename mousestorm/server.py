# SPDX-License-Identifier: GPL-3.0-or-later
"""The drag-and-drop app: a small local web server (stdlib only).

Binds to 127.0.0.1 and refuses requests with a foreign Host header or Origin,
so a web page you happen to have open cannot drive it.
"""
import json
import mimetypes
import os
import queue
import re
import shutil
import threading
import time
import traceback
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

from . import hardware
from . import options as O
from . import steps as step_log
from .cli import parse_aspect, parse_res
from .plan import LIGHTINGS, QUALITY, SOUNDTRACKS, Settings
from .render import FORMATS, Cancelled, MousestormError, find_blender, run

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.environ.get("MOUSESTORM_OUTPUT") or os.path.join(os.path.dirname(HERE), "output")
BLEND_MAGIC = (b"BLENDER", b"\x1f\x8b", b"\x28\xb5\x2f\xfd")
STAGES = ["extract", "plan", "beauty", "frames", "done"]

JOBS, UPLOADS, STEP_LOGS = {}, {}, {}
LOCK = threading.Lock()
WORK = queue.Queue()
BLENDER = None


def safe_stem(name):
    stem = os.path.splitext(os.path.basename(name))[0]
    stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", stem).strip(". ")
    return stem[:80] or "scene"


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


DEVICES = dict(state="idle", gpus=[])
DEVICE_LOCK = threading.Lock()


def probe_devices():
    """Blender takes a few seconds to answer, so this runs once, on the side."""
    try:
        gpus = hardware.list_gpus(find_blender(BLENDER))
    except FileNotFoundError:
        gpus = []
    DEVICES.update(state="ready", gpus=gpus)


def devices():
    with DEVICE_LOCK:
        if DEVICES["state"] == "idle":
            DEVICES["state"] = "probing"
            threading.Thread(target=probe_devices, daemon=True).start()
    out = dict(DEVICES, cpu=dict(name=hardware.cpu_name(), threads=os.cpu_count() or 4), ram=hardware.available_ram_gb(), auto=None)
    if DEVICES["state"] == "ready":
        load = hardware.gpu_load()
        chosen, why = hardware.pick(DEVICES["gpus"], load)
        out["auto"] = dict(gpu=chosen["id"] if chosen else "", why=why)
        out["busy"] = [g["id"] for g in DEVICES["gpus"] if hardware.bus_key(g["id"]) in load and hardware.is_busy(load[hardware.bus_key(g["id"])])]
    return out


def settings_from(body):
    fmt = body.get("format", "mp4")
    if fmt not in FORMATS:
        fmt = "mp4"
    res_name = body.get("resolution", "1080p")
    if res_name not in ("720p", "1080p", "1440p", "4k"):
        res_name = "1080p"
    aspect = body.get("aspect", "16:9")
    if aspect not in ("16:9", "9:16", "1:1"):
        aspect = "16:9"
    res = parse_res(res_name, parse_aspect(aspect))
    quality = body.get("quality", "standard")
    if quality not in QUALITY:
        quality = "standard"
    lighting = body.get("lighting", "auto")
    if lighting not in LIGHTINGS:
        lighting = "auto"
    soundtrack = body.get("soundtrack", "score")
    if soundtrack not in SOUNDTRACKS:
        soundtrack = "score"
    duration = float(clamp(float(body.get("duration", 0) or 0), 0, 600))
    showcase = float(clamp(float(body.get("showcase", 20)), 6, 90))
    if duration > 0:     # a short film should not spend most of its time on the finale
        showcase = min(showcase, max(6.0, duration * 0.34))
    device = body.get("device", "auto")
    if device not in ("auto", "cpu", "gpu"):
        device = "auto"
    gpu = body.get("gpu") or ""
    if not any(d["id"] == gpu for d in DEVICES["gpus"]):
        gpu = ""
    workers = threads = 0
    if body.get("cpu_share") is not None:
        workers = threads = max(1, round((os.cpu_count() or 4) * clamp(float(body["cpu_share"]), 5, 100) / 100.0))
    layers = []
    preset = O.PRESETS.get(body.get("preset") or "")
    if preset:
        layers.append(preset["opt"])
    adv = body.get("advanced") or {}
    if not isinstance(adv, dict):
        raise ValueError("advanced must be an object")
    # options the app does not show (file paths) cannot be set through it
    layers.append({k: v for k, v in adv.items() if k in O.OPTS and O.OPTS[k].web})
    opt = O.resolve(*layers)
    return Settings(
        opt=opt, width=res[0], height=res[1], fps=int(clamp(int(body.get("fps", 60)), 12, 120)),
        speed=float(clamp(float(body.get("speed", 1.0)), 0.1, 40.0)), duration=duration,
        mice=int(clamp(int(body.get("mice", 0)), 0, 512)), fmt=fmt, quality=quality,
        showcase=showcase, soundtrack=soundtrack, lighting=lighting, device=device, gpu=gpu, workers=workers, threads=threads)


def worker():
    while True:
        job = WORK.get()
        try:
            if job["cancel"].is_set():
                raise Cancelled()
            run_job(job)
        except Cancelled:
            job.update(state="cancelled", msg="Stopped", discard=True)
        except (MousestormError, ValueError, FileNotFoundError) as e:
            job.update(state="error", error=str(e))
        except Exception:
            job.update(state="error", error=traceback.format_exc())
        finally:
            src = job.get("source_path")
            if src and os.path.isfile(src):
                try:
                    os.remove(src)
                except OSError:
                    pass
            # a process that was just killed can hold its folder open for a moment
            for _ in range(10 if job.get("discard") else 0):
                shutil.rmtree(job["dir"], ignore_errors=True)
                if not os.path.exists(job["dir"]):
                    break
                time.sleep(0.5)


def run_job(job):
    job["state"] = "running"
    out_dir = job["dir"]
    s = job["settings"]
    stem = safe_stem(job["name"])
    target = os.path.join(out_dir, stem if s.fmt == "png" else "%s.%s" % (stem, s.fmt))
    work = os.path.join(out_dir, "work")

    def log(stage, frac, msg):
        job.update(stage=stage, frac=float(frac), msg=msg)
        job["fracs"][stage] = float(frac)

    result = run(job["source_path"], target, s, blender=BLENDER, log=log, work=work, cancel=job["cancel"],
                 storyboard=job.get("storyboard", False))
    if job.get("storyboard"):
        job.update(state="done", frac=1.0, stage="done", result=dict(
            storyboard=os.path.basename(result["storyboard"]), duration=result["duration"], mice=result["mice"],
            ops=result["ops"], speed=result["speed"], folder=out_dir))
        return
    stills = sorted(os.listdir(result["stills"])) if result["stills"] and os.path.isdir(result["stills"]) else []
    job.update(state="done", frac=1.0, stage="done", result=dict(
        video=os.path.basename(result["video"]),
        poster=os.path.basename(result["poster"]) if result.get("poster") else None,
        is_video=s.fmt in ("mp4", "webm", "mov"),
        fmt=s.fmt,
        stills_dir=os.path.basename(result["stills"]) if result["stills"] else None,
        stills=stills,
        sheet=os.path.basename(result["sheet"]) if result["sheet"] else None,
        duration=result["duration"], mice=result["mice"], ops=result["ops"], speed=result["speed"],
        aspect=s.width / s.height, folder=out_dir))


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _allowed(self):
        host = (self.headers.get("Host") or "").split(":")[0].lower()
        origin = self.headers.get("Origin")
        if origin is not None and urlparse(origin).hostname not in ("127.0.0.1", "localhost"):
            self.send_error(403, "Forbidden origin")
            return False
        if host in ("127.0.0.1", "localhost"):
            return True
        self.send_error(403, "Forbidden host")
        return False

    def _json(self, obj, code=200):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _file(self, path, ctype=None, cache=False):
        if not os.path.isfile(path):
            return self.send_error(404)
        size = os.path.getsize(path)
        ctype = ctype or mimetypes.guess_type(path)[0] or "application/octet-stream"
        start, end, code = 0, size - 1, 200
        rng = self.headers.get("Range")
        if rng:
            m = re.match(r"bytes=(\d*)-(\d*)", rng)
            if m and (m.group(1) or m.group(2)):
                if m.group(1):
                    start = int(m.group(1))
                    end = int(m.group(2)) if m.group(2) else size - 1
                else:
                    start = max(0, size - int(m.group(2)))
                end = min(end, size - 1)
                code = 206
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        if code == 206:
            self.send_header("Content-Range", "bytes %d-%d/%d" % (start, end, size))
        if not cache:
            self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            with open(path, "rb") as f:
                f.seek(start)
                left = end - start + 1
                while left > 0:
                    chunk = f.read(min(left, 1 << 20))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    left -= len(chunk)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    # ------------------------------------------------------------------ GET
    def do_GET(self):
        if not self._allowed():
            return
        url = urlparse(self.path)
        p = unquote(url.path)
        if p in ("/", "/index.html"):
            return self._file(os.path.join(HERE, "web", "index.html"), "text/html; charset=utf-8")
        if p in ("/fonts/Inter.ttf", "/fonts/JetBrainsMono.ttf"):
            return self._file(os.path.join(HERE, "assets", "fonts", os.path.basename(p)), "font/ttf", cache=True)
        if p == "/api/info":
            try:
                blender = find_blender(BLENDER)
            except FileNotFoundError:
                blender = None
            return self._json(dict(blender=blender, cores=os.cpu_count(), output=ROOT))
        if p == "/api/devices":
            return self._json(devices())
        if p == "/api/options":
            return self._json(O.as_json())
        m = re.match(r"^/api/job/([0-9a-f]{12})$", p)
        if m:
            job = JOBS.get(m.group(1))
            if not job:
                return self._json(dict(error="no such job"), 404)
            return self._json({k: job.get(k) for k in ("state", "stage", "frac", "msg", "fracs", "error", "result", "name")})
        m = re.match(r"^/api/preview/([0-9a-f]{12})$", p)
        if m:
            job = JOBS.get(m.group(1))
            if not job:
                return self.send_error(404)
            return self._file(os.path.join(job["dir"], "work", "preview.jpg"), "image/jpeg")
        m = re.match(r"^/files/([0-9a-f]{12})/(.+)$", p)
        if m:
            job = JOBS.get(m.group(1))
            if not job or not job.get("result"):
                return self.send_error(404)
            r = job["result"]
            parts = m.group(2).split("/")
            allowed = {r.get("video"), r.get("sheet"), r.get("poster"), r.get("storyboard")} - {None}
            path = None
            if len(parts) == 1 and parts[0] in allowed:
                path = os.path.join(job["dir"], parts[0])
            elif len(parts) == 2 and r.get("stills_dir") and parts[0] == r["stills_dir"] and parts[1] in r["stills"]:
                path = os.path.join(job["dir"], parts[0], parts[1])
            if not path:
                return self.send_error(404)
            return self._file(path, cache=True)
        self.send_error(404)

    # ----------------------------------------------------------------- POST
    def do_POST(self):
        if not self._allowed():
            return
        url = urlparse(self.path)
        if url.path == "/api/upload":
            return self.upload(parse_qs(url.query))
        if url.path == "/api/steps":
            return self.steps()
        if url.path == "/api/render":
            length = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
                settings = settings_from(body)
            except (ValueError, TypeError, AttributeError):
                return self._json(dict(error="Invalid settings"), 400)
            if body.get("steps"):
                settings.step_log = STEP_LOGS.get(body["steps"])
                if settings.step_log is None:
                    return self._json(dict(error="Step log not found"), 400)
            up = UPLOADS.pop(body.get("upload"), None)
            if up is None:
                return self._json(dict(error="Upload not found"), 400)
            jid = uuid.uuid4().hex[:12]
            preview = bool(body.get("storyboard"))
            d = os.path.join(ROOT, "_previews" if preview else "", "%s_%s" % (safe_stem(up["name"]), jid[:6]))
            os.makedirs(d, exist_ok=True)
            src = os.path.join(d, safe_stem(up["name"]) + ".blend")
            shutil.move(up["path"], src)
            keep = bool(body.get("storyboard"))
            if keep:        # the file stays around so a real render can follow its preview
                UPLOADS[body.get("upload")] = dict(up, path=up["path"] + ".keep")
                shutil.copyfile(src, up["path"] + ".keep")
            job = dict(id=jid, name=up["name"], dir=d, source_path=src, settings=settings, cancel=threading.Event(), storyboard=keep,
                       state="queued", stage="extract", frac=0.0, msg="Queued", fracs={s: 0.0 for s in STAGES},
                       result=None, error=None)
            JOBS[jid] = job
            WORK.put(job)
            return self._json(dict(job=jid))
        m = re.match(r"^/api/job/([0-9a-f]{12})/cancel$", url.path)
        if m:
            job = JOBS.get(m.group(1))
            if not job:
                return self._json(dict(error="no such job"), 404)
            job["cancel"].set()
            return self._json(dict(ok=True))
        self.send_error(404)

    def steps(self):
        """An optional .msteps file, checked as soon as it is dropped."""
        length = int(self.headers.get("Content-Length") or 0)
        if length > step_log.MAX_BYTES:
            return self._json(dict(error="That step log is too large"), 400)
        try:
            log = step_log.parse(json.loads(self.rfile.read(length).decode("utf-8-sig")))
        except (ValueError, UnicodeDecodeError) as e:
            return self._json(dict(error=str(e)), 400)
        uid = uuid.uuid4().hex
        STEP_LOGS[uid] = log
        return self._json(dict(steps=uid, count=len(log["steps"]), objects=len({st["object"] for st in log["steps"]})))

    def upload(self, query):
        name = (query.get("name") or ["scene.blend"])[0]
        if not name.lower().endswith((".blend", ".blend1")):
            return self._json(dict(error="Please drop a .blend file"), 400)
        length = int(self.headers.get("Content-Length") or 0)
        os.makedirs(os.path.join(ROOT, "_uploads"), exist_ok=True)
        uid = uuid.uuid4().hex
        path = os.path.join(ROOT, "_uploads", uid + ".blend")
        left, head = length, b""
        with open(path, "wb") as f:
            while left > 0:
                chunk = self.rfile.read(min(left, 1 << 20))
                if not chunk:
                    break
                if not head:
                    head = chunk[:16]
                f.write(chunk)
                left -= len(chunk)
        if left or not head.startswith(BLEND_MAGIC):
            os.remove(path)
            return self._json(dict(error="That does not look like a .blend file"), 400)
        UPLOADS[uid] = dict(path=path, name=os.path.basename(name), size=length)
        return self._json(dict(upload=uid, name=os.path.basename(name), size=length))


def serve(port=7878, open_browser=True, blender=None):
    global BLENDER
    BLENDER = blender
    os.makedirs(ROOT, exist_ok=True)
    # uploads that were dropped but never started do not outlive a restart
    shutil.rmtree(os.path.join(ROOT, "_uploads"), ignore_errors=True)
    shutil.rmtree(os.path.join(ROOT, "_previews"), ignore_errors=True)
    threading.Thread(target=worker, daemon=True).start()
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = "http://127.0.0.1:%d/" % port
    print("Mousestorm is running at %s  (Ctrl+C to quit)" % url)
    print("Films are saved to %s" % ROOT)
    if open_browser:
        threading.Timer(0.6, webbrowser.open, args=(url,)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
