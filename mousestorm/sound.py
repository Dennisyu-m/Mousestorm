# SPDX-License-Identifier: GPL-3.0-or-later
"""The soundtrack, generated from the plan. Nothing here is sampled.

Every operation in the film is a real click, panned to where its mouse is on
screen and voiced by what it does; at scale they fuse into something like rain
on a tin roof. Underneath, a procedural score builds with the work, lands on
the first frame of the finale, and resolves over the contact sheet.
"""
import wave

import numpy as np

from .options import items_of
from .plan import segment, show_schedule

SR = 44100
LUFS_TARGET = -16.0
MAX_VOICED = 60000


def midi(m):
    return 440.0 * 2.0 ** ((m - 69) / 12.0)


# ------------------------------------------------------------ click voices
def _tick(rng, ms, tone, decay, body=0.55, noise=0.3):
    n = int(SR * ms / 1000)
    t = np.arange(n) / SR
    w = rng.standard_normal(n)
    w = w - np.concatenate(([0.0], w[:-1])) * 0.92
    out = (body * np.sin(2 * np.pi * tone * t) + noise * w) * np.exp(-t * decay)
    return out / max(float(np.abs(out).max()), 1e-6)


def _press(rng, tone, release=0.34):
    """Button down, then a quieter button up a few tens of milliseconds later."""
    down = _tick(rng, 14, tone, 900.0)
    up = _tick(rng, 12, tone * 1.28, 1200.0) * release
    gap = int(SR * rng.uniform(0.03, 0.06))
    out = np.zeros(gap + len(up))
    out[:len(down)] += down
    out[gap:] += up
    return out


def _swish(rng, ms, smooth):
    n = int(SR * ms / 1000)
    w = rng.standard_normal(n + smooth * 8)
    a = np.convolve(w, np.ones(smooth) / smooth, mode="same")
    b = np.convolve(w, np.ones(smooth * 8) / (smooth * 8), mode="same")
    band = (a - b)[smooth * 4:smooth * 4 + n]
    env = np.sin(np.linspace(0.0, np.pi, n)) ** 1.6
    out = band * env
    return out / max(float(np.abs(out).max()), 1e-6)


def _voices(rng):
    return {
        0: [_press(rng, float(f)) for f in (2300, 2900, 3400, 2600)],      # interface clicks
        1: [_press(rng, float(f), 0.5) for f in (1750, 2050)],             # adding things
        2: [_tick(rng, 30, float(f), 260.0, 0.8, 0.12) for f in (620, 780, 910)],   # grabbing
        3: [_swish(rng, 90, 3) * 0.7 for _ in range(4)],                   # sculpt and edit strokes
        4: [_swish(rng, 180, 9) * 0.6 for _ in range(3)],                  # painting
        5: [_tick(rng, 8, float(f), 1400.0, 0.5, 0.2) * 0.5 for f in (5200, 6100)],  # render tiles
    }


def _clicks(pl, rng, n):
    bus = np.zeros((n, 2), np.float32)
    rip = pl.agenda_ripples
    rip = rip[rip[:, 0] < pl.show0]
    if len(rip) == 0:
        return bus
    gain = 1.0
    if len(rip) > MAX_VOICED:
        keep = np.sort(rng.choice(len(rip), MAX_VOICED, replace=False))
        gain = float(np.sqrt(len(rip) / MAX_VOICED))
        rip = rip[keep]
    voices = _voices(rng)
    rate = len(rip) / max(pl.show0, 1e-3)
    gain *= 0.7 / np.sqrt(1.0 + rate / 12.0)   # incoherent sources add as a root
    bake = pl.bake_n
    for t0, cur, kind in rip:
        bank = voices[int(kind)]
        s = bank[int(rng.integers(len(bank)))]
        i = int(t0 * SR + rng.uniform(0, 60))
        end = min(i + len(s), n)
        ln = end - i
        if ln <= 0:
            continue
        j = min(int(t0 * 120.0), bake - 1)
        pan = float(np.clip(pl.cpos[int(cur), j, 0] / pl.ui_w, 0.0, 1.0)) * 0.76 + 0.12
        amp = gain * float(rng.uniform(0.45, 1.0))
        bus[i:end, 0] += s[:ln] * amp * float(np.cos(pan * np.pi / 2))
        bus[i:end, 1] += s[:ln] * amp * float(np.sin(pan * np.pi / 2))
    # the wash of many small movements, following how busy the swarm is
    edges = np.linspace(0, pl.show0, 240)
    hist, _ = np.histogram(rip[:, 0], bins=edges)
    env = np.interp(np.arange(n) / SR, edges[:-1], hist / max(hist.max(), 1))
    env[int(pl.show0 * SR):] = 0.0
    k = int(0.002 * SR) | 1
    bed = np.convolve(rng.standard_normal(n), np.ones(k) / k, mode="same") * env * 0.09
    bus[:, 0] += bed.astype(np.float32)
    bus[:, 1] += np.roll(bed, 311).astype(np.float32)
    return bus


# ------------------------------------------------------------------- score
# build chords, finale chords, the resolving chord, kick on/off, hats on/off, arpeggio density
MOODS = {
    "dusk": ([(45, (60, 64, 67, 71)), (41, (60, 64, 65, 69)), (48, (59, 64, 67, 74)), (43, (59, 62, 64, 69))],
             [(41, (57, 60, 64, 67)), (48, (55, 60, 64, 74)), (45, (55, 60, 64, 71)), (43, (59, 62, 67, 69))],
             (48, (55, 60, 62, 64, 67)), True, True, 1.0),
    "bright": ([(48, (60, 64, 67, 72)), (43, (59, 62, 67, 71)), (45, (60, 64, 69, 72)), (41, (60, 65, 69, 72))],
               [(41, (60, 65, 69, 72)), (48, (60, 64, 67, 74)), (43, (59, 62, 67, 74)), (48, (60, 64, 67, 72))],
               (48, (55, 60, 64, 67, 72)), True, True, 0.8),
    "drive": ([(45, (57, 60, 64, 69)), (45, (57, 60, 64, 72)), (41, (57, 60, 65, 69)), (43, (55, 59, 62, 67))],
              [(45, (57, 60, 64, 69)), (41, (57, 60, 65, 69)), (48, (55, 60, 64, 67)), (43, (55, 59, 62, 67))],
              (45, (52, 57, 60, 64, 69)), True, True, 1.3),
    "calm": ([(48, (64, 67, 71, 74)), (45, (64, 67, 69, 72)), (41, (64, 65, 69, 72)), (43, (62, 67, 69, 71))],
             [(41, (64, 65, 69, 72)), (48, (64, 67, 71, 74)), (45, (64, 67, 69, 72)), (43, (62, 67, 69, 71))],
             (48, (55, 64, 67, 71, 74)), False, False, 0.45),
}


def _pad(notes, n, bright, rng):
    """Detuned additive voices; `bright` (0..1, per sample) opens the harmonics."""
    t = np.arange(n) / SR
    dark = 2.4 * (1.0 - bright)
    out = np.zeros(n)
    for m in notes:
        for det in (-0.0028, 0.0031):
            f = midi(m) * (1.0 + det)
            phase = float(rng.uniform(0, 6.28))
            for k in range(1, 6):
                out += np.sin(2 * np.pi * f * k * t + phase * k) * np.exp(-(k - 1) * dark) / k
    return out / (len(notes) * 2)


def _env_ad(n, attack, release):
    env = np.ones(n)
    a, r = min(n, int(attack * SR)), min(n, int(release * SR))
    if a:
        env[:a] = np.sin(np.linspace(0, np.pi / 2, a)) ** 2
    if r:
        env[-r:] *= np.cos(np.linspace(0, np.pi / 2, r)) ** 2
    return env


def _place(bus, i, s, gl, gr):
    if i >= len(bus) or i < 0:
        return
    m = min(len(s), len(bus) - i)
    bus[i:i + m, 0] += s[:m] * gl
    bus[i:i + m, 1] += s[:m] * gr


def _kick(rng):
    t = np.arange(int(0.22 * SR)) / SR
    return np.sin(2 * np.pi * (52.0 * t + 1.9 * (1.0 - np.exp(-t * 42.0)))) * np.exp(-t * 13.0)


def _hat(rng):
    n = int(0.05 * SR)
    w = rng.standard_normal(n)
    w = w - np.concatenate(([0.0], w[:-1]))
    return w * np.exp(-np.arange(n) / SR * 120.0) * 0.5


def _pluck(f, ms, decay):
    t = np.arange(int(SR * ms / 1000)) / SR
    return (np.sin(2 * np.pi * f * t) + 0.4 * np.sin(4 * np.pi * f * t) + 0.16 * np.sin(6 * np.pi * f * t)) * np.exp(-t * decay)


def _bell(f, seconds):
    t = np.arange(int(SR * seconds)) / SR
    return (np.sin(2 * np.pi * f * t) * np.exp(-t * 2.6)
            + 0.42 * np.sin(2 * np.pi * f * 2.76 * t) * np.exp(-t * 5.2)
            + 0.2 * np.sin(2 * np.pi * f * 5.4 * t) * np.exp(-t * 9.0))


def _score(pl, rng, n):
    bus = np.zeros((n, 2), np.float64)
    show0, D = pl.show0, pl.show_D
    opt = pl.settings.opt
    up = int(opt["sound.transpose"])
    build, finale, resolve, use_kick, use_hat, density = MOODS[opt["sound.mood"]]
    BUILD = [(r + up, tuple(x + up for x in ch)) for r, ch in build]
    FINALE = [(r + up, tuple(x + up for x in ch)) for r, ch in finale]
    RESOLVE = (resolve[0] + up, tuple(x + up for x in resolve[1]))
    # a whole number of bars before the finale, so that it lands on a downbeat
    bars = max(2, int(round(show0 / (240.0 / float(opt["sound.tempo"])))))
    bar = show0 / bars
    beat = bar / 4.0
    t_axis = np.arange(n) / SR

    model1, shade1 = pl.T["model"][1], pl.T["shade"][1]
    keys_t = [0.0, model1, shade1, show0 - bar, show0, show0 + D * 0.14, show0 + D]
    keys_b = [0.12, 0.55, 0.62, 0.72, 1.0, 0.6, 0.5]
    bright = np.interp(t_axis, keys_t, keys_b)

    # ---- pads, two bars per chord; the crossfade is the release of one and the attack of the next
    k = 0
    i0 = 0
    chord_len = 2 * bar
    while i0 < int(show0 * SR):
        root, notes = BUILD[k % len(BUILD)]
        m = int((chord_len + 0.5) * SR)
        m = min(m, n - i0)
        s = _pad(notes, m, bright[i0:i0 + m], rng) * _env_ad(m, 0.35, 0.5)
        _place(bus, i0, s, 0.5, 0.5)
        k += 1
        i0 += int(chord_len * SR)
    schedule = show_schedule(D, items_of(opt, "show.segments"))
    last = schedule[-1]
    body = last[1] if len(schedule) > 1 else last[2]      # the last section gets the resolving chord
    i_end = int((show0 + body) * SR)
    each = max(1.0, body / len(FINALE))
    for j, (root, notes) in enumerate(FINALE):
        i0 = int((show0 + j * each) * SR)
        m = min(int((each + 0.6) * SR), n - i0)
        if m <= 0:
            break
        s = _pad(notes, m, bright[i0:i0 + m], rng) * _env_ad(m, 0.25, 0.6)
        _place(bus, i0, s, 0.5, 0.5)
        # sits an octave above sub-bass, where phone and laptop speakers can still play it
        low = np.sin(2 * np.pi * midi(root) * np.arange(m) / SR) * _env_ad(m, 0.05, 0.5)
        _place(bus, i0, low, 0.12, 0.12)
    # last chord rings out over the contact sheet
    if i_end < n:
        m = n - i_end
        root, notes = RESOLVE
        s = _pad(notes, m, np.full(m, 0.5), rng) * _env_ad(m, 0.2, 1.1)
        _place(bus, i_end, s, 0.55, 0.55)
        low = np.sin(2 * np.pi * midi(root) * np.arange(m) / SR) * _env_ad(m, 0.05, 1.1)
        _place(bus, i_end, low, 0.12, 0.12)

    # ---- rhythm joins as the work builds, and leaves for the finale
    kick, hat = _kick(rng), _hat(rng)
    hist = pl.hist / max(int(pl.hist.max()), 1)
    busy = lambda t: float(hist[min(len(hist) - 1, int(t / max(show0, 1e-3) * len(hist)))])
    total = int(show0 / beat)
    for b_i in range(total):
        t = b_i * beat
        p = t / max(show0, 1e-3)
        i = int(t * SR)
        chord = BUILD[int(t / chord_len) % len(BUILD)]
        if use_kick and p > 0.08 and (b_i % 2 == 0 or p > 0.55):
            _place(bus, i, kick, 0.62, 0.62)
        if use_hat and p > 0.3:
            _place(bus, int((t + beat * 0.5) * SR), hat, 0.2, 0.26)
        if use_hat and p > 0.55:
            _place(bus, i, hat, 0.16, 0.12)
        # bass on the off-eighths
        if p > 0.14:
            f = midi(chord[0])
            for off in (0.0, 0.5):
                s = _pluck(f, beat * 480, 7.0)
                _place(bus, int((t + off * beat) * SR), s, 0.3, 0.3)
        # arpeggio, as busy as the swarm is
        if p > 0.05:
            lift = 12 if p > 0.5 else 0
            for sixteenth in range(4):
                if rng.random() > (0.28 + 0.62 * busy(t)) * density:
                    continue
                note = chord[1][int(rng.integers(len(chord[1])))] + lift
                s = _pluck(midi(note), 260, 13.0) * float(rng.uniform(0.05, 0.13))
                pan = float(rng.uniform(0.2, 0.8))
                _place(bus, int((t + sixteenth * beat * 0.25) * SR), s, 1.0 - pan, pan)

    # ---- riser into the finale, then the impact of it
    m = min(int(bar * SR), int(show0 * SR))
    i = int(show0 * SR) - m
    w = rng.standard_normal(m)
    u = np.linspace(0.0, 1.0, m)
    w = w - np.concatenate(([0.0], w[:-1])) * 0.85
    _place(bus, i, w * u ** 2.6 * 0.26, 0.5, 0.5)
    i = int(show0 * SR)
    t = np.arange(int(2.6 * SR)) / SR
    k_ = int(0.0016 * SR) | 1
    crash = np.convolve(rng.standard_normal(len(t)), np.ones(k_) / k_, mode="same") * np.exp(-t * 2.2)
    _place(bus, i, crash * 0.9, 0.5, 0.5)
    _place(bus, i, np.sin(2 * np.pi * (46 * t + 2.4 * (1 - np.exp(-t * 26)))) * np.exp(-t * 3.4) * 0.9, 0.6, 0.6)

    # ---- one bell for each photograph
    shoot = segment(schedule, "shoot")
    if shoot:
        each_shot = shoot[1] / max(1, len(pl.stills))
        bells = FINALE[0][1] + FINALE[1][1] + FINALE[2][1]
        for k in range(len(pl.stills)):
            i = int((show0 + shoot[0] + k * each_shot) * SR)
            s = _bell(midi(bells[k % len(bells)] + 12), 2.4) * 0.14
            pan = 0.5 + 0.34 * float(np.sin(k * 2.1))
            _place(bus, i, s, 1.0 - pan, pan)
    return bus


def _load_track(path, n):
    """The user's own music, decoded by ffmpeg, looped or cut to the film."""
    import subprocess
    from .render import MousestormError, find_ffmpeg
    if not __import__("os").path.isfile(path):
        raise MousestormError("Music file not found: %s" % path)
    r = subprocess.run([find_ffmpeg(), "-v", "error", "-i", path, "-vn", "-f", "f32le", "-ac", "2", "-ar", str(SR), "-"],
                       capture_output=True)
    pcm = np.frombuffer(r.stdout, np.float32)
    if r.returncode or len(pcm) < 2:
        raise MousestormError("Could not read the music file: %s" % path)
    x = pcm.reshape(-1, 2).astype(np.float64)
    if len(x) < n:
        x = np.concatenate([x] * (n // len(x) + 1))
    return x[:n]


def _shutters(pl, rng, n):
    bus = np.zeros((n, 2), np.float32)
    shoot = segment(show_schedule(pl.show_D, items_of(pl.settings.opt, "show.segments")), "shoot")
    if not shoot:
        return bus
    each = shoot[1] / max(1, len(pl.stills))
    a = _tick(rng, 42, 1100.0, 90.0)
    b = _tick(rng, 42, 860.0, 90.0)
    shutter = np.zeros(len(a) + 380)
    shutter[:len(a)] += a * 0.8
    shutter[380:380 + len(b)] += b * 0.5
    start = pl.show0 + shoot[0]
    for k in range(len(pl.stills)):
        i = int((start + k * each) * SR)
        if i + len(shutter) < n:
            bus[i:i + len(shutter), 0] += shutter.astype(np.float32) * 0.55
            bus[i:i + len(shutter), 1] += shutter.astype(np.float32) * 0.55
    return bus


# ----------------------------------------------------------------- loudness
def lufs(x):
    """Integrated loudness after ITU-R BS.1770 (frequency-domain K-weighting,
    400 ms gated blocks). Accurate to a fraction of a LU for this material."""
    f = np.fft.rfftfreq(len(x), 1.0 / SR)
    r2 = np.maximum(f / 38.0, 1e-9) ** 2
    hp2 = r2 * r2 / ((r2 - 1.0) ** 2 + r2 * 4.0)    # 2nd order high-pass, Q = 0.5
    g2 = 10.0 ** (4.0 / 10.0)
    s2 = (f / 2700.0) ** 2                          # corner set so 997 Hz reads +0.69 dB, as in the standard
    shelf2 = (1.0 + g2 * s2) / (1.0 + s2)           # high shelf, +4 dB
    h = np.sqrt(hp2 * shelf2)
    weighted = [np.fft.irfft(np.fft.rfft(x[:, c]) * h, len(x)) for c in range(x.shape[1])]
    block, hop = int(0.4 * SR), int(0.1 * SR)
    if len(x) < block:
        return -70.0
    sq = np.cumsum(np.sum(np.stack(weighted, 1) ** 2, axis=1))
    sq = np.concatenate(([0.0], sq))
    e = (sq[block::hop] - sq[:-block:hop]) / block
    loud = -0.691 + 10.0 * np.log10(np.maximum(e, 1e-12))
    keep = loud > -70.0
    if not keep.any():
        return -70.0
    gate = -0.691 + 10.0 * np.log10(float(e[keep].mean())) - 10.0
    keep &= loud > gate
    return float(-0.691 + 10.0 * np.log10(max(float(e[keep].mean()), 1e-12)))


def master(bus, target=LUFS_TARGET, ceiling=0.89):
    """Brings the mix to a loudness target, then rounds off whatever exceeds
    the ceiling. Soft-limits only near the top so quiet passages stay clean."""
    out = bus.astype(np.float64)
    for _ in range(3):         # the limiter pulls loudness down, so converge on it
        gain = 10.0 ** ((target - lufs(np.clip(out, -1.5, 1.5))) / 20.0)
        out *= min(gain, 40.0)
        over = np.abs(out) > ceiling * 0.7
        knee = ceiling * 0.7
        room = ceiling - knee
        out[over] = np.sign(out[over]) * (knee + room * np.tanh((np.abs(out[over]) - knee) / room))
    return out


def synth(pl, path):
    rng = np.random.default_rng(pl.settings.seed)
    n = int(pl.duration * SR)
    clicks = _clicks(pl, rng, n).astype(np.float64)
    shutters = _shutters(pl, rng, n).astype(np.float64)
    opt = pl.settings.opt
    target = float(opt["sound.loudness"])
    if pl.settings.soundtrack == "score":
        score = _load_track(opt["sound.track"], n) if opt["sound.track"] else _score(pl, rng, n)
        # stems are levelled separately, so a sparse scene and a dense one mix alike
        peak = np.sqrt(float(np.mean(clicks[:int(pl.show0 * SR)] ** 2))) + 1e-9
        clicks *= 0.05 / peak * float(opt["sound.clicks"])
        score *= 0.11 / (np.sqrt(float(np.mean(score ** 2))) + 1e-9) * float(opt["sound.music"])
        bus = score + clicks + shutters * 0.32
    else:
        peak = float(np.abs(clicks).max()) + 1e-9
        bus = clicks / peak + shutters * 0.7

    tail = min(int(1.2 * SR), n)
    bus[-tail:] *= np.linspace(1, 0, tail)[:, None]
    out = master(bus, target if pl.settings.soundtrack == "score" else target - 4.0)
    out[-tail:] *= np.linspace(1, 0, tail)[:, None] ** 0.5
    pcm = (np.clip(out, -1, 1) * 32767).astype(np.int16)
    with wave.open(path, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())
