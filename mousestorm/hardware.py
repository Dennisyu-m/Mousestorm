# SPDX-License-Identifier: GPL-3.0-or-later
"""What this computer has, and what of it is free to use."""
import json
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
BUSY_UTILISATION = 30          # percent
BUSY_MEMORY = 0.4              # share of the card already in use
MIN_FREE_GB = 2.0              # Cycles needs at least this much room on the card
RAM_RESERVE_GB = 2.0           # left for the system and everything else that is open
WORKER_BASE_GB = 2.6           # measured: interpreter, compiled code, fonts, beauty frames


def cpu_name():
    """A readable processor name, or an empty string."""
    try:
        if sys.platform == "win32":
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as k:
                return " ".join(winreg.QueryValueEx(k, "ProcessorNameString")[0].split())
        if sys.platform == "darwin":
            return subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string"], capture_output=True, text=True, timeout=10).stdout.strip()
        with open("/proc/cpuinfo", encoding="utf-8", errors="replace") as f:
            for line in f:
                if line.startswith("model name"):
                    return " ".join(line.split(":", 1)[1].split())
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    return ""


def available_ram_gb():
    """Physical memory free right now, or None if this system will not say."""
    try:
        if sys.platform == "win32":
            import ctypes

            class Status(ctypes.Structure):
                _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong), ("total", ctypes.c_ulonglong), ("avail", ctypes.c_ulonglong),
                            ("page_total", ctypes.c_ulonglong), ("page_avail", ctypes.c_ulonglong), ("virt_total", ctypes.c_ulonglong),
                            ("virt_avail", ctypes.c_ulonglong), ("ext", ctypes.c_ulonglong)]
            st = Status()
            st.length = ctypes.sizeof(Status)
            return st.avail / 2 ** 30 if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)) else None
        if sys.platform == "darwin":
            out = subprocess.run(["vm_stat"], capture_output=True, text=True, timeout=10).stdout
            pages = sum(int(m) for m in re.findall(r"Pages (?:free|inactive|speculative):\s+(\d+)", out))
            return pages * os.sysconf("SC_PAGE_SIZE") / 2 ** 30
        with open("/proc/meminfo", encoding="utf-8") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) / 2 ** 20
    except (OSError, ValueError, AttributeError, subprocess.SubprocessError):
        pass
    return None


def safe_workers(want, free_gb, width, height, ss):
    """How many frame workers fit in memory, so that a small machine slows down instead of failing."""
    if free_gb is None:
        return want
    each = WORKER_BASE_GB + width * height * ss * ss * 48 / 1e9
    return max(1, min(want, int((free_gb - RAM_RESERVE_GB) / each)))


# ------------------------------------------------------------------- GPUs
_gpus = {}


def list_gpus(blender):
    """Every GPU Cycles can render on, as Blender reports it. Takes a few seconds, so the answer is kept."""
    if blender in _gpus:
        return _gpus[blender]
    found = []
    try:
        out = subprocess.run([blender, "-b", "--factory-startup", "--disable-autoexec", "-P", os.path.join(HERE, "bscripts", "devices.py")],
                             capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120, creationflags=NO_WINDOW).stdout
        line = next((x for x in out.splitlines() if x.startswith("MOUSESTORM_DEVICES ")), None)
        found = json.loads(line.split(" ", 1)[1]) if line else []
    except (OSError, ValueError, subprocess.SubprocessError):
        found = []
    _gpus[blender] = found
    return found


def bus_key(text):
    """The bus:device part of a PCI address, which Cycles ids and nvidia-smi both contain."""
    m = re.search(r"([0-9a-fA-F]{2}):([0-9a-fA-F]{2})(?:\.[0-9a-fA-F])?(?:_|$)", text or "")
    return (m.group(1) + ":" + m.group(2)).lower() if m else None


def parse_smi(text):
    """{bus key: (used MiB, total MiB, utilisation %)} from nvidia-smi's CSV output."""
    out = {}
    for line in text.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        try:
            out[bus_key(parts[0])] = (float(parts[1]), float(parts[2]), float(parts[3]))
        except (IndexError, ValueError):
            continue
    return out


def gpu_load(samples=3):
    """How busy each NVIDIA card is. A few readings, since one can land between two bursts of work."""
    merged = {}
    for n in range(samples):
        try:
            text = subprocess.run(["nvidia-smi", "--query-gpu=pci.bus_id,memory.used,memory.total,utilization.gpu",
                                   "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=15, creationflags=NO_WINDOW).stdout
        except (OSError, subprocess.SubprocessError):
            return {}
        for key, (used, total, util) in parse_smi(text).items():
            old = merged.get(key, (0.0, total, 0.0))
            merged[key] = (max(old[0], used), total, max(old[2], util))
        if n < samples - 1:
            time.sleep(0.2)
    return merged


def is_busy(stats, need_gb=MIN_FREE_GB):
    used, total, util = stats
    return util >= BUSY_UTILISATION or used > BUSY_MEMORY * total or (total - used) / 1024.0 < need_gb


def pick(gpus, load, need_gb=MIN_FREE_GB):
    """Chooses the card automatic mode renders on: returns (gpu or None, reason).
    A card someone else is using is left alone, so a film can run beside a training job."""
    if not gpus:
        return None, "no GPU that Cycles can use"
    known = [(load.get(bus_key(g["id"])), g) for g in gpus]
    free = [(s, g) for s, g in known if s is not None and not is_busy(s, need_gb)]
    if free:
        stats, g = max(free, key=lambda x: x[0][1] - x[0][0])
        return g, "%s is idle" % g["name"]
    unknown = [g for s, g in known if s is None]
    if unknown:                 # not an NVIDIA card: nothing can say how busy it is
        return unknown[0], "%s found" % unknown[0]["name"]
    return None, "every GPU is busy"


def resolve_device(settings, blender, log):
    """Turns device "auto", or a GPU number, into the actual choice. Returns True if it was automatic."""
    automatic = settings.device == "auto"
    if automatic:
        chosen, why = pick(list_gpus(blender), gpu_load())
        if chosen is not None:
            settings.device, settings.gpu = "gpu", chosen["id"]
            log("extract", 0.0, "Rendering on the GPU: %s" % why)
        else:
            settings.device, settings.gpu = "cpu", ""
            log("extract", 0.0, "Rendering on the CPU: %s" % why)
    elif settings.device == "gpu" and str(settings.gpu).isdigit():
        gpus = list_gpus(blender)
        n = int(settings.gpu)
        if not 1 <= n <= len(gpus):
            raise ValueError("There is no GPU %d; this machine has %d. See 'mousestorm devices'." % (n, len(gpus)))
        settings.gpu = gpus[n - 1]["id"]
    return automatic


def describe(blender):
    """Prints what was found. Used by 'mousestorm devices'."""
    print("CPU     %s, %d threads" % (cpu_name() or "unknown", os.cpu_count() or 0))
    free = available_ram_gb()
    print("Memory  %s free" % ("%.1f GB" % free if free is not None else "unknown"))
    gpus = list_gpus(blender) if blender else []
    load = gpu_load()
    for n, g in enumerate(gpus, 1):
        s = load.get(bus_key(g["id"]))
        state = "unknown load" if s is None else ("busy" if is_busy(s) else "idle") + ", %d of %d MiB, %d%%" % s
        print("GPU %d   %s (%s), %s" % (n, g["name"], g["backend"], state))
    if not gpus:
        print("GPU     none that Cycles can use")
    chosen, why = pick(gpus, load)
    print("Auto    %s (%s)" % ("GPU %d" % (gpus.index(chosen) + 1) if chosen else "CPU", why))
