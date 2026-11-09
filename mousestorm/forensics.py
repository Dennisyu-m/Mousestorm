# SPDX-License-Identifier: GPL-3.0-or-later
"""Names the modelling work from evidence in the finished mesh.

A .blend keeps the result and not the history, so what the mice do has to be
inferred. Nothing here picks a name at random: a part is matched against the
real primitives, and every patch is named after the displacement that the film
really plays on it (source shape → final shape), with the values measured.
A name is an inference, and it is true of what is on screen.
"""
import hashlib
import math
import re
from dataclasses import dataclass, field

import numpy as np

TOL = 0.012                  # on coordinates normalised to the bounding box
NEGLIGIBLE = 0.0012          # displacement below this share of the object does not count as work
ICO_SIZES = {12: 1, 42: 2, 162: 3, 642: 4, 2562: 5, 10242: 6, 40962: 7}
HINTS = {"cube": "cube", "sphere": "uv", "icosphere": "ico", "cylinder": "cylinder", "cone": "cone", "torus": "torus",
         "plane": "plane", "grid": "grid", "circle": "circle", "suzanne": "monkey"}
NON_MESH = {"CURVE": ("Add Curve", "curve.primitive_bezier_curve_add()"),
            "SURFACE": ("Add Surface", "surface.primitive_nurbs_surface_surface_add()"),
            "META": ("Add Metaball", "object.metaball_add(type='BALL')"),
            "FONT": ("Add Text", "object.text_add()")}
PRIMITIVE_LABEL = {"cube": "Add Cube", "uv": "Add UV Sphere", "ico": "Add Ico Sphere", "cylinder": "Add Cylinder",
                   "cone": "Add Cone", "torus": "Add Torus", "plane": "Add Plane", "grid": "Add Grid", "circle": "Add Circle",
                   "monkey": "Add Monkey"}


@dataclass
class Fit:
    shape: str
    label: str
    api: str
    exact: bool
    source: np.ndarray                           # the starting shape, same vertex order and space as the input
    extras: list = field(default_factory=list)   # (label, api, chip name, count): loop cuts and the like


def _near(a, v, tol=TOL):
    return np.abs(a - v) < tol


def _levels(x, tol=TOL):
    """How many distinct values, within tolerance."""
    return int(np.sum(np.diff(np.sort(x)) > tol)) + 1


def _g(x):
    return "%.4g" % float(x)


def hint_of(names):
    """Blender's automatic mesh names survive renaming the object, so they are direct testimony."""
    for name in names:
        m = re.fullmatch(r"([A-Za-z]+)(?:\.\d+)?", name or "")
        if m and m.group(1).lower() in HINTS:
            return HINTS[m.group(1).lower()]
    return None


def shape_key(local, tris):
    """Same shape in the object's own space: the later copies of it were duplicated, not rebuilt."""
    span = max(float(np.ptp(local, axis=0).max()), 1e-12)
    q = np.round((local - local.min(0)) / span * 4096.0).astype(np.int32)
    h = hashlib.blake2b(digest_size=16)
    h.update(np.array([len(local), len(tris)], np.int64).tobytes())
    h.update(q.tobytes())
    h.update(np.asarray(tris, np.int32).tobytes())
    return h.digest()


# -------------------------------------------------------------- exact matches
def _radials(q, co, a):
    """Distance from the axis, measured two ways: against the bounding box (survives
    elliptical meshes) and around the centroid (survives odd vertex counts)."""
    i, j = [k for k in range(3) if k != a]
    yield np.hypot(q[:, i], q[:, j])
    r = np.hypot(co[:, i] - co[:, i].mean(), co[:, j] - co[:, j].mean())
    yield r / max(float(r.max()), 1e-12)


def _exact_cube(q, n, h):
    box = np.abs(q).max(1)
    if n < 8 or not _near(box, 1.0).all() or int(np.all(_near(np.abs(q), 1.0), axis=1).sum()) != 8:
        return None
    lv = [_levels(q[:, a]) for a in range(3)]
    extras = []
    if lv[0] == lv[1] == lv[2] and lv[0] > 2:
        extras.append(("Subdivide", "mesh.subdivide(number_cuts=%d)" % (lv[0] - 2), "Cuts", lv[0] - 2))
    elif sum(lv) > 6:
        cuts = sum(lv) - 6
        extras.append(("Loop Cut", 'mesh.loopcut_slide(MESH_OT_loopcut={"number_cuts":%d})' % cuts, "Cuts", cuts))
    return "cube", "mesh.primitive_cube_add(size=%s)" % _g(2 * h.max()), extras


def _exact_sphere(q, co, n, npoly, h, hint):
    if n < 12:
        return None
    r = np.linalg.norm(co - co.mean(0), axis=1)
    if not (_near(np.linalg.norm(q, axis=1), 1.0).all() or _near(r / max(float(r.max()), 1e-12), 1.0).all()):
        return None
    if n in ICO_SIZES and hint in (None, "ico"):
        return "ico", "mesh.primitive_ico_sphere_add(subdivisions=%d, radius=%s)" % (ICO_SIZES[n], _g(h.max())), []
    z = q[:, 2]
    rings = _levels(z) - 1
    segments = (n - 2) / max(rings - 1, 1)
    single_poles = int(_near(z, float(z.max())).sum()) == 1 and int(_near(z, float(z.min())).sum()) == 1
    if rings >= 3 and segments == int(segments) and segments >= 3 and single_poles:
        return "uv", "mesh.primitive_uv_sphere_add(segments=%d, ring_count=%d, radius=%s)" % (segments, rings, _g(h.max())), []
    return None


def _exact_cylinder(q, co, h):
    for a in (2, 0, 1):
        z = q[:, a]
        i = [k for k in range(3) if k != a][0]
        for rad in _radials(q, co, a):
            rim = _near(rad, 1.0)
            if not (rim | (_near(np.abs(z), 1.0) & (rad < 3 * TOL))).all():
                continue
            ring = int((rim & _near(z, z[rim].min())).sum())
            levels = _levels(z[rim])
            if ring < 5 or levels < 2:
                continue
            extras = []
            if levels > 2:
                extras.append(("Loop Cut", 'mesh.loopcut_slide(MESH_OT_loopcut={"number_cuts":%d})' % (levels - 2), "Cuts", levels - 2))
            return "cylinder", "mesh.primitive_cylinder_add(vertices=%d, radius=%s, depth=%s)" % (ring, _g(h[i]), _g(2 * h[a])), extras
    return None


def _exact_cone(q, co, h):
    for a in (2, 0, 1):
        i = [k for k in range(3) if k != a][0]
        for sign in (1.0, -1.0):
            z = sign * q[:, a]
            for rad in _radials(q, co, a):
                top, bottom = _near(z, 1.0), _near(z, -1.0)
                body = rad > 3 * TOL
                if not (body | top | bottom).all() or not bottom.any():
                    continue
                r1 = float(rad[bottom].max())
                r2 = float(rad[top].max()) if top.any() else 0.0
                want = r1 + (r2 - r1) * (z + 1.0) * 0.5
                ring = int((bottom & body & _near(rad, r1)).sum())
                if r2 < 0.97 * r1 and ring >= 5 and _near(rad[body], want[body]).all():
                    return "cone", "mesh.primitive_cone_add(vertices=%d, radius1=%s, radius2=%s, depth=%s)" % (
                        ring, _g(h[i] * r1), _g(h[i] * r2), _g(2 * h[a])), []
    return None


def _exact_torus(q, co, n, h):
    if n < 24:
        return None
    for a in (2, 0, 1):
        i = [k for k in range(3) if k != a][0]
        for rad in _radials(q, co, a):
            major = (float(rad.max()) + float(rad.min())) * 0.5
            minor = (float(rad.max()) - float(rad.min())) * 0.5
            if minor < 0.01 or float(rad.min()) < 0.02:
                continue
            if not _near(np.hypot(rad - major, q[:, a] * minor), minor, 2 * TOL).all():
                continue
            ring = _levels(np.arctan2(q[:, a] * minor, rad - major), 0.01)
            segs = n / max(ring, 1)
            i_, j_ = [b for b in range(3) if b != a]
            around = _levels(np.arctan2(q[:, j_], q[:, i_]), 0.01)
            if segs != int(segs) or segs < 6 or ring < 3 or around != int(segs) or float(rad.min()) < 0.15:
                continue
            return "torus", "mesh.primitive_torus_add(major_segments=%d, minor_segments=%d, major_radius=%s, minor_radius=%s)" % (
                segs, ring, _g(h[i] * major), _g(h[i] * minor)), []
    return None


def _exact_flat(q, n, h, flat):
    a = int(np.flatnonzero(flat)[0])
    i, j = [k for k in range(3) if k != a]
    if n == 4 and _near(np.abs(q[:, [i, j]]), 1.0).all():
        return "plane", "mesh.primitive_plane_add(size=%s)" % _g(2 * h[[i, j]].max()), []
    rad = np.hypot(q[:, i], q[:, j])
    ring = int(_near(rad, 1.0).sum())
    if n >= 5 and ring >= n - 1 and (rad[~_near(rad, 1.0)] < 3 * TOL).all():
        return "circle", "mesh.primitive_circle_add(vertices=%d, radius=%s)" % (ring, _g(h[i])), []
    lx, ly = _levels(q[:, i]), _levels(q[:, j])
    if lx * ly == n and n > 4:
        return "grid", "mesh.primitive_grid_add(x_subdivisions=%d, y_subdivisions=%d, size=%s)" % (lx - 1, ly - 1, _g(2 * h[[i, j]].max())), []
    return None


def _exact(q, co, n, npoly, h, flat, hint):
    if hint == "monkey" or (n == 507 and npoly == 500):
        return "monkey", "mesh.primitive_monkey_add()", []
    if flat.sum() == 1:
        return _exact_flat(q, n, h, flat)
    if flat.any():
        return None
    return (_exact_cube(q, n, h) or _exact_cylinder(q, co, h) or _exact_cone(q, co, h)
            or _exact_sphere(q, co, n, npoly, h, hint) or _exact_torus(q, co, n, h))


# ----------------------------------------------- the closest primitive to start from
def _gauge(q, shape, axis, sign=1.0):
    """A distance-like function that equals 1 on the surface of the primitive."""
    if shape in ("cube", "plane"):
        return np.abs(q).max(1)
    if shape in ("uv", "ico"):
        return np.linalg.norm(q, axis=1)
    i, j = [k for k in range(3) if k != axis]
    r = np.hypot(q[:, i], q[:, j])
    z = sign * q[:, axis]
    if shape == "cylinder":
        return np.maximum(r, np.abs(z))
    return np.maximum(2.0 * r + z, -z)                 # cone, apex at +z


def _heightfield(q, axis):
    """A regular lattice with displaced height is a grid that has been pulled about."""
    i, j = [k for k in range(3) if k != axis]
    n = len(q)
    if n < 25 or _levels(q[:, i], 1e-4) * _levels(q[:, j], 1e-4) != n:
        return False
    return len(np.unique(np.round(q[:, [i, j]] * 4096.0), axis=0)) == n


def _ellipsoid(co, keep, c, hh):
    """Axis-aligned ellipsoid through the points that are still on the original surface.
    Unlike a bounding box this holds when part of the sphere has been pushed away."""
    x = (co[keep] - c) / hh
    A = np.concatenate([x * x, x], axis=1)
    try:
        v = np.linalg.lstsq(A, np.ones(len(x)), rcond=None)[0]
    except np.linalg.LinAlgError:
        return None
    if not np.isfinite(v).all() or (v[:3] <= 1e-9).any():
        return None
    centre = -v[3:] / (2.0 * v[:3])
    r2 = (1.0 + float((v[:3] * centre * centre).sum())) / v[:3]
    if (r2 <= 0).any():
        return None
    return c + centre * hh, np.sqrt(r2) * hh


def _robust_fit(co, shape, axis, sign, c, hh, flat):
    """Fits to the points that still lie on the primitive, so that the parts pulled away
    from it do not distort the starting shape."""
    for _ in range(3):
        q = (co - c) / hh
        g = _gauge(q, shape, axis, sign)
        rho = float(np.clip(np.median(g), 0.4, 1.05))
        res = np.abs(g - rho)
        keep = res <= max(2.0 * float(np.median(res)), 0.01)
        if int(keep.sum()) < max(8, int(0.3 * len(co))):
            break
        if shape in ("uv", "ico") and not flat.any():
            fitted = _ellipsoid(co, keep, c, hh)
            if fitted is not None:
                c, hh = fitted
                continue
        lo, hi = co[keep].min(0), co[keep].max(0)
        c = np.where(flat, c, (lo + hi) * 0.5)
        hh = np.where(flat, hh, np.maximum((hi - lo) * 0.5, hh * 1e-3))
    q = (co - c) / hh
    g = _gauge(q, shape, axis, sign)
    rho = float(np.clip(np.median(g), 0.4, 1.05))
    return c, hh, q, g, rho, float(np.sqrt(np.mean((g - rho) ** 2)))


def fit_primitive(co, npoly=0, kind="MESH", names=(), ntri=0):
    """What this object most plausibly started as, and the shape it had when it did."""
    co = np.asarray(co, np.float64)
    if kind in NON_MESH:
        label, api = NON_MESH[kind]
        return Fit(kind.lower(), label, api, True, co.copy())
    lo, hi = co.min(0), co.max(0)
    c, h = (lo + hi) * 0.5, (hi - lo) * 0.5
    span = max(float(h.max()), 1e-12)
    flat = h < span * 1e-3
    hh = np.where(flat, span, h)
    q = (co - c) / hh
    n = len(co)
    hint = hint_of(names)
    found = _exact(q, co, n, npoly, h, flat, hint)
    if found:
        shape, api, extras = found
        return Fit(shape, PRIMITIVE_LABEL[shape], api, True, co.copy(), extras)

    a = int(np.argmin(h))
    lattice = _heightfield(q, a)
    if lattice or hint in ("grid", "plane"):
        s = q.copy()
        s[:, a] = float(np.median(q[:, a]))
        i, j = [k for k in range(3) if k != a]
        if lattice:
            lx, ly = _levels(q[:, i], 1e-4), _levels(q[:, j], 1e-4)
            api = "mesh.primitive_grid_add(x_subdivisions=%d, y_subdivisions=%d, size=%s)" % (lx - 1, ly - 1, _g(2 * h[[i, j]].max()))
            return Fit("grid", "Add Grid", api, False, c + s * hh)
        return Fit("plane", "Add Plane", "mesh.primitive_plane_add(size=%s)" % _g(2 * h[[i, j]].max()), False, c + s * hh)

    axes = [a for a in (2, 0, 1) if not flat[a]]
    if hint in ("cube", "uv", "ico"):
        cands = [(hint, 2, 1.0, 1.0)]
    elif hint == "cylinder":
        cands = [("cylinder", a, 1.0, 1.0) for a in axes]
    elif hint == "cone":
        cands = [("cone", a, s, 1.0) for a in axes for s in (1.0, -1.0)]
    else:
        # a box is where most hand modelling starts, so it wins close calls
        cands = [("cube", 2, 1.0, 0.9), ("uv", 2, 1.0, 1.0)] + [("cylinder", a, 1.0, 1.0) for a in axes]
    best = None
    for shape, axis, sign, bias in cands:
        fit = _robust_fit(co, shape, axis, sign, c, hh, flat)
        if best is None or fit[5] * bias < best[0]:
            best = (fit[5] * bias, shape, axis, fit)
    _, shape, axis, (c, hh, q, g, rho, _res) = best
    h = hh
    if shape == "uv" and n in ICO_SIZES and npoly == ntri:      # an ico sphere is all triangles
        shape = "ico"
    source = c + q * (rho / np.maximum(g, 1e-9))[:, None] * hh
    i = [k for k in range(3) if k != axis][0]
    if shape == "cube":
        api = "mesh.primitive_cube_add(size=%s)" % _g(2 * h.max() * rho)
    elif shape in ("uv", "ico"):
        api = "mesh.primitive_%s_sphere_add(radius=%s)" % ("uv" if shape == "uv" else "ico", _g(h.max() * rho))
    elif shape == "cylinder":
        api = "mesh.primitive_cylinder_add(radius=%s, depth=%s)" % (_g(h[i] * rho), _g(2 * h[axis] * rho))
    else:
        api = "mesh.primitive_cone_add(radius1=%s, depth=%s)" % (_g(h[i] * rho), _g(2 * h[axis] * rho))
    return Fit(shape, PRIMITIVE_LABEL[shape], api, False, source)


# ------------------------------------------------------------ measuring patches
def measure(src, dst, tris, labels, k, shrink_free=False):
    """Per-patch description of the motion from src to dst, as arrays of length k.
    shrink_free removes the overall offset along the normals, which is what a
    smoothed surface differs from the original by before any detail is considered."""
    src, dst = np.asarray(src, np.float64), np.asarray(dst, np.float64)
    tris = np.asarray(tris, np.int64)
    n = len(dst)
    cr = np.cross(dst[tris[:, 1]] - dst[tris[:, 0]], dst[tris[:, 2]] - dst[tris[:, 0]])
    twice = np.linalg.norm(cr, axis=1)
    area = twice * 0.5
    fn = cr / np.maximum(twice, 1e-30)[:, None]
    flat_idx = tris.ravel()
    w = np.bincount(flat_idx, weights=np.repeat(area / 3.0, 3), minlength=n)
    vn = np.stack([np.bincount(flat_idx, weights=np.repeat(cr[:, c], 3), minlength=n) for c in range(3)], 1)
    vn /= np.maximum(np.linalg.norm(vn, axis=1), 1e-30)[:, None]
    tl = labels[tris[:, 0]]

    def vsum(x):
        return np.bincount(labels, weights=w * x, minlength=k)

    def tsum(x):
        return np.bincount(tl, weights=area * x, minlength=k)

    A = np.maximum(vsum(np.ones(n)), 1e-30)
    d = dst - src
    if shrink_free:
        d = d - vn * float(np.median((d * vn).sum(1)))
    size = np.linalg.norm(d, axis=1)
    swept = vsum(size)
    sum_d = np.stack([vsum(d[:, c]) for c in range(3)], 1)
    mean_d = sum_d / A[:, None]
    mag = swept / A
    uni = np.linalg.norm(sum_d, axis=1) / np.maximum(swept, 1e-30)
    along = (d * vn).sum(1)
    dn = vsum(along) / A
    rms_dn = np.sqrt(vsum(along ** 2) / A)
    spread = np.sqrt(np.maximum(vsum(size ** 2) / A - mag ** 2, 0.0)) / np.maximum(mag, 1e-30)
    peak = np.zeros(k)
    np.maximum.at(peak, labels, np.abs(along))

    # the rigid part of the motion removed, what remains around the patch centre
    centre_src = np.stack([vsum(src[:, c]) for c in range(3)], 1) / A[:, None]
    centre_dst = np.stack([vsum(dst[:, c]) for c in range(3)], 1) / A[:, None]
    rest = d - mean_d[labels]
    r = src - centre_src[labels]
    rl = np.linalg.norm(r, axis=1)
    rhat = r / np.maximum(rl, 1e-30)[:, None]
    rest_mag = vsum(np.linalg.norm(rest, axis=1))
    rad = vsum((rest * rhat).sum(1)) / np.maximum(rest_mag, 1e-30)

    normal = np.stack([tsum(fn[:, c]) for c in range(3)], 1)
    tA = np.maximum(tsum(np.ones(len(tris))), 1e-30)
    flat = np.linalg.norm(normal, axis=1) / tA
    axis = normal / np.maximum(np.linalg.norm(normal, axis=1), 1e-30)[:, None]
    twist = vsum((np.cross(rhat, rest) * axis[labels]).sum(1)) / np.maximum(rest_mag, 1e-30)
    mean_r = vsum(rl) / A
    angle = np.degrees(vsum((np.cross(r, rest) * axis[labels]).sum(1) / np.maximum(rl ** 2, 1e-30)) / A)
    r_dst = np.linalg.norm(dst - centre_dst[labels], axis=1)
    factor = vsum(r_dst) / np.maximum(vsum(rl), 1e-30)

    scr = np.cross(src[tris[:, 1]] - src[tris[:, 0]], src[tris[:, 2]] - src[tris[:, 0]])
    s_n = np.stack([np.bincount(tl, weights=scr[:, c], minlength=k) for c in range(3)], 1)
    s_a = np.maximum(np.bincount(tl, weights=np.linalg.norm(scr, axis=1), minlength=k), 1e-30)
    flat_src = np.linalg.norm(s_n, axis=1) / s_a

    u = sum_d / np.maximum(np.linalg.norm(sum_d, axis=1), 1e-30)[:, None]
    cosd = np.abs((fn * u[tl]).sum(1))
    cap = tsum((cosd > 0.9).astype(np.float64)) / tA
    wall = tsum((cosd < 0.3).astype(np.float64)) / tA

    # how differently the neighbouring patches move: alike means one wide, smooth deformation
    ta, tb = tl, labels[tris[:, 1]]
    tc = labels[tris[:, 2]]
    pa = np.concatenate([ta, tb, tc, ta, tb, tc])
    pb = np.concatenate([tb, tc, ta, tc, ta, tb])
    cross = pa != pb
    near_d = np.stack([np.bincount(pa[cross], weights=mean_d[pb[cross], c], minlength=k) for c in range(3)], 1)
    near_n = np.bincount(pa[cross], minlength=k).astype(np.float64)
    rel = np.linalg.norm(mean_d - near_d / np.maximum(near_n, 1.0)[:, None], axis=1) / np.maximum(np.linalg.norm(mean_d, axis=1), 1e-30)
    rel[near_n == 0] = 1.0

    # chamfers: a long crease bordered by a thin strip on one side and a broad face on the other
    ed = np.sort(np.vstack([tris[:, [0, 1]], tris[:, [1, 2]], tris[:, [2, 0]]]), axis=1)
    tid = np.tile(np.arange(len(tris)), 3)
    key = ed[:, 0] * (n + 1) + ed[:, 1]
    order = np.argsort(key, kind="stable")
    same = key[order[1:]] == key[order[:-1]]
    e1, e2 = order[:-1][same], order[1:][same]
    t1, t2 = tid[e1], tid[e2]
    length = np.linalg.norm(dst[ed[e1, 0]] - dst[ed[e1, 1]], axis=1)
    dih = np.arccos(np.clip((fn[t1] * fn[t2]).sum(1), -1.0, 1.0))
    sides = np.stack([np.linalg.norm(dst[tris[:, a]] - dst[tris[:, (a + 1) % 3]], axis=1) for a in range(3)], 1)
    altitude = twice / np.maximum(sides.max(1), 1e-30)
    thin, broad = np.minimum(altitude[t1], altitude[t2]), np.maximum(altitude[t1], altitude[t2])
    is_cham = (dih > 0.2) & (dih < 1.35) & (length > 2.0 * thin) & (thin < 0.45 * broad)
    el = tl[t1]
    creases = np.bincount(el, weights=length * (dih > 0.03), minlength=k)
    cham_len = np.bincount(el, weights=length * is_cham, minlength=k)
    bevel = cham_len / np.maximum(creases, 1e-30)
    width = np.bincount(el, weights=thin * length * is_cham, minlength=k) / np.maximum(cham_len, 1e-30)

    return dict(area=A, swept=swept, mag=mag, uni=uni, dn=dn, rms_dn=rms_dn, spread=spread, peak=peak, rad=rad, twist=twist,
                angle=angle, factor=factor, flat=flat, flat_src=flat_src, cap=cap, wall=wall, bevel=bevel, width=width,
                radius=np.sqrt(A / math.pi), mean_r=mean_r, direction=u, rel=rel)


def _edit(label, api, icon, chip_name, value):
    return dict(label=label, api=api, icon=icon, chip=(chip_name, float(value)))


def name_edit(f, i, diag):
    """The Edit Mode operation whose effect is the measured motion of patch i."""
    mag = float(f["mag"][i])
    if mag < diag * NEGLIGIBLE:
        return None
    dn, uni, fat = float(f["dn"][i]), float(f["uni"][i]), abs(float(f["dn"][i])) / max(mag, 1e-30)
    size = float(f["radius"][i])
    if f["bevel"][i] > 0.35 and mag < 0.25 * size:
        width = float(f["width"][i])
        return _edit("Bevel", "mesh.bevel(offset=%s)" % _g(width), "scale", "Width", width)
    if uni > 0.65:
        offset = float(mag) * (1.0 if dn >= 0 else -1.0)
        if f["cap"][i] > 0.12 and f["wall"][i] > 0.12:
            return _edit("Extrude Region", "mesh.extrude_region_move(TRANSFORM_OT_translate={\"value\":(%s)})" % _vec(f["direction"][i] * offset),
                         "move", "Offset", offset)
        if f["spread"][i] > 0.45 or f["rel"][i] < 0.6:
            return _edit("Proportional Move", "transform.translate(use_proportional_edit=True, proportional_size=%s)" % _g(size),
                         "move", "Size", size)
        return _edit("Move", "transform.translate(value=(%s))" % _vec(f["direction"][i] * mag), "move", "Distance", mag)
    if fat > 0.7:
        return _edit("Shrink/Fatten", "transform.shrink_fatten(value=%s)" % _g(dn), "move", "Offset", dn)
    if abs(f["rad"][i]) > 0.6:
        factor = float(f["factor"][i])
        if f["flat"][i] > 0.97 and factor < 1.0:
            return _edit("Inset Faces", "mesh.inset(thickness=%s)" % _g(mag), "scale", "Thickness", mag)
        return _edit("Resize", "transform.resize(value=(%s, %s, %s))" % (_g(factor), _g(factor), _g(factor)), "scale", "Factor", factor)
    if abs(f["twist"][i]) > 0.55:
        angle = float(f["angle"][i])
        return _edit("Rotate", "transform.rotate(value=%s)" % _g(math.radians(angle)), "rotate", "Angle", angle)
    return _edit("Proportional Move", "transform.translate(use_proportional_edit=True, proportional_size=%s)" % _g(size),
                 "move", "Size", size)


def _vec(v):
    return ", ".join(_g(x) for x in v)


def name_sculpt(f, i, diag):
    """The sculpt brush whose effect is the measured displacement over the smoothed surface."""
    mag = float(f["mag"][i])
    if mag < diag * NEGLIGIBLE:
        return None
    radius, dn = float(f["radius"][i]), float(f["dn"][i])
    strength = min(1.0, mag / max(radius, 1e-30))
    gained_flat = float(f["flat"][i]) > float(f["flat_src"][i]) + 0.01
    sharp = float(f["peak"][i]) > 2.2 * max(abs(dn), mag)
    if float(f["uni"][i]) > 0.6 and abs(dn) < 0.4 * mag:
        brush, icon = "Grab", "grab"
    elif abs(dn) < 0.25 * mag:
        brush, icon = "Draw Sharp", "brush"
    elif dn > 0:
        icon = "brush"
        brush = "Clay Strips" if gained_flat else ("Inflate" if abs(dn) > 0.85 * float(f["rms_dn"][i]) else "Draw")
    else:
        icon = "brush"
        brush = "Scrape" if gained_flat else ("Crease" if sharp else "Draw")
    mode = "INVERT" if dn < 0 and brush in ("Draw", "Inflate", "Crease") else "NORMAL"
    return dict(label=brush, api="sculpt.brush_stroke(mode='%s', strength=%s)" % (mode, _g(strength)), icon=icon,
                chip=("Radius", radius))
