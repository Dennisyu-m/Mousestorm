# SPDX-License-Identifier: GPL-3.0-or-later
"""CPU-only visibility-buffer rasterizer, compiled with numba.

Deliberately no OpenGL: the tool must not touch the GPU, and must behave the
same on a headless box as on a workstation. Geometry goes to a triangle-id +
depth buffer first; each covered pixel is then shaded exactly once, so cost is
bounded by resolution and heavy AI meshes stay cheap.
"""
import math

import numpy as np
from numba import njit

BAYER = np.array([[0, 8, 2, 10], [12, 4, 14, 6], [3, 11, 1, 9], [15, 7, 13, 5]],
                 dtype=np.float32) / 16.0 + 1.0 / 32.0


@njit(cache=True, nogil=True, fastmath=True, error_model="numpy")
def to_view(P, ranges, vofs, eye, R, V):
    """World → camera space for every visible vertex range."""
    for r in range(ranges.shape[0]):
        for i in range(vofs[r, 0], vofs[r, 1]):
            x = P[i, 0] - eye[0]
            y = P[i, 1] - eye[1]
            z = P[i, 2] - eye[2]
            V[i, 0] = x * R[0, 0] + y * R[0, 1] + z * R[0, 2]
            V[i, 1] = x * R[1, 0] + y * R[1, 1] + z * R[1, 2]
            V[i, 2] = x * R[2, 0] + y * R[2, 1] + z * R[2, 2]


@njit(cache=True, nogil=True, fastmath=True, error_model="numpy", inline="always")
def _fill(x0, y0, d0, x1, y1, d1, x2, y2, d2, ti, W, H, Z, ID, alpha, bayer):
    minx = max(int(math.floor(min(x0, x1, x2))), 0)
    maxx = min(int(math.ceil(max(x0, x1, x2))), W - 1)
    miny = max(int(math.floor(min(y0, y1, y2))), 0)
    maxy = min(int(math.ceil(max(y0, y1, y2))), H - 1)
    if minx > maxx or miny > maxy:
        return
    area = (x1 - x0) * (y2 - y0) - (x2 - x0) * (y1 - y0)
    if abs(area) < 1e-9:
        return
    ia = 1.0 / area
    dw0x = -(y2 - y1) * ia
    dw1x = -(y0 - y2) * ia
    dw2x = -(y1 - y0) * ia
    sx = minx + 0.5
    for y in range(miny, maxy + 1):
        py = y + 0.5
        w0 = ((x2 - x1) * (py - y1) - (y2 - y1) * (sx - x1)) * ia
        w1 = ((x0 - x2) * (py - y2) - (y0 - y2) * (sx - x2)) * ia
        w2 = ((x1 - x0) * (py - y0) - (y1 - y0) * (sx - x0)) * ia
        for x in range(minx, maxx + 1):
            if w0 >= 0.0 and w1 >= 0.0 and w2 >= 0.0:
                d = w0 * d0 + w1 * d1 + w2 * d2
                if d > Z[y, x]:
                    if alpha >= 1.0 or alpha > bayer[y & 3, x & 3]:
                        Z[y, x] = d
                        ID[y, x] = ti
            w0 += dw0x
            w1 += dw1x
            w2 += dw2x


@njit(cache=True, nogil=True, fastmath=True, error_model="numpy")
def rasterize(V, T, ranges, W, H, tanx, tany, near, ortho, Z, ID, talpha, bayer):
    """Fills Z (larger = closer) and ID. Near-clips so scene-sized floors
    behave, and uses no clipping for orthographic views."""
    hw, hh = W * 0.5, H * 0.5
    fx, fy = hw / tanx, hh / tany
    clip = np.empty((4, 3), dtype=np.float64)
    for r in range(ranges.shape[0]):
        for ti in range(ranges[r, 0], ranges[r, 1]):
            a, b, c = T[ti, 0], T[ti, 1], T[ti, 2]
            alpha = talpha[ti]
            if ortho:
                _fill(hw + V[a, 0] * fx, hh - V[a, 1] * fy, -V[a, 2],
                      hw + V[b, 0] * fx, hh - V[b, 1] * fy, -V[b, 2],
                      hw + V[c, 0] * fx, hh - V[c, 1] * fy, -V[c, 2],
                      ti, W, H, Z, ID, alpha, bayer)
                continue
            z0, z1, z2 = V[a, 2], V[b, 2], V[c, 2]
            inside = (z0 >= near) + (z1 >= near) + (z2 >= near)
            if inside == 0:
                continue
            if inside == 3:
                _fill(hw + V[a, 0] / z0 * fx, hh - V[a, 1] / z0 * fy, 1.0 / z0,
                      hw + V[b, 0] / z1 * fx, hh - V[b, 1] / z1 * fy, 1.0 / z1,
                      hw + V[c, 0] / z2 * fx, hh - V[c, 1] / z2 * fy, 1.0 / z2,
                      ti, W, H, Z, ID, alpha, bayer)
                continue
            n = 0
            idx = (a, b, c)
            for k in range(3):
                i0, i1 = idx[k], idx[(k + 1) % 3]
                za, zb = V[i0, 2], V[i1, 2]
                if za >= near:
                    clip[n, 0], clip[n, 1], clip[n, 2] = V[i0, 0], V[i0, 1], za
                    n += 1
                if (za >= near) != (zb >= near):
                    s = (near - za) / (zb - za)
                    clip[n, 0] = V[i0, 0] + (V[i1, 0] - V[i0, 0]) * s
                    clip[n, 1] = V[i0, 1] + (V[i1, 1] - V[i0, 1]) * s
                    clip[n, 2] = near
                    n += 1
            for k in range(1, n - 1):
                _fill(hw + clip[0, 0] / clip[0, 2] * fx, hh - clip[0, 1] / clip[0, 2] * fy, 1.0 / clip[0, 2],
                      hw + clip[k, 0] / clip[k, 2] * fx, hh - clip[k, 1] / clip[k, 2] * fy, 1.0 / clip[k, 2],
                      hw + clip[k + 1, 0] / clip[k + 1, 2] * fx, hh - clip[k + 1, 1] / clip[k + 1, 2] * fy, 1.0 / clip[k + 1, 2],
                      ti, W, H, Z, ID, alpha, bayer)


@njit(cache=True, nogil=True, fastmath=True, error_model="numpy", inline="always")
def _smooth(a, b, x):
    t = (x - a) / (b - a)
    t = 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)
    return t * t * (3.0 - 2.0 * t)


@njit(cache=True, nogil=True, fastmath=True, error_model="numpy")
def _env(dx, dy, dz, rough):
    """Procedural photo-studio environment. Returns linear RGB radiance for a
    reflection vector; roughness widens the softboxes."""
    up = 0.5 + 0.5 * dz
    r = 0.030 + 0.19 * up * up
    g = 0.034 + 0.21 * up * up
    b = 0.046 + 0.26 * up * up
    soft = 0.09 + rough * 0.75
    # key softbox: upper-left, slightly in front
    k = dx * -0.55 + dy * -0.62 + dz * 0.56
    a = _smooth(0.86 - soft, 0.92 + soft * 0.3, k)
    gain = 7.0 / (1.0 + 7.0 * rough * rough)
    r += a * gain * 1.00
    g += a * gain * 0.95
    b += a * gain * 0.88
    # cool strip on the right
    k = dx * 0.82 + dy * -0.18 + dz * 0.54
    a = _smooth(0.90 - soft, 0.95 + soft * 0.3, k)
    gain *= 0.55
    r += a * gain * 0.62
    g += a * gain * 0.78
    b += a * gain * 1.00
    # warm back kicker
    k = dx * 0.12 + dy * 0.86 + dz * 0.5
    a = _smooth(0.90 - soft, 0.96 + soft * 0.3, k)
    r += a * gain * 0.34
    g += a * gain * 0.24
    b += a * gain * 0.16
    return r, g, b


@njit(cache=True, nogil=True, fastmath=True, error_model="numpy")
def shade(W, H, ID, Z, OUT, NB, cam, ortho, P, T, TN, TUV, TCOL, has_uv, has_col,
          tmat, tpart, tmask, mcol, mpar, mtex, tex, pwire, ppaint, VPAINT, VSEL,
          SZ, shc, gz, grid, pal, flat_mode):
    """Shade every pixel of the visibility buffer, writing linear RGB to OUT
    and the world normal z-component to NB (consumed by the cavity pass)."""
    ex, ey, ez = cam[0], cam[1], cam[2]
    rx, ry, rz = cam[3], cam[4], cam[5]
    ux, uy, uz = cam[6], cam[7], cam[8]
    fx, fy, fz = cam[9], cam[10], cam[11]
    tanx, tany = cam[12], cam[13]
    has_shadow = SZ.shape[0] > 1
    sh_w, sh_h = SZ.shape[1], SZ.shape[0]
    sh_ex, sh_ey, sh_ez = shc[0], shc[1], shc[2]
    sh_half = shc[12]
    sh_fx = sh_w * 0.5 / sh_half
    fxpx = W * 0.5 / tanx
    clay_r, clay_g, clay_b = pal[0], pal[1], pal[2]
    wire_r, wire_g, wire_b = pal[3], pal[4], pal[5]
    sel_r, sel_g, sel_b = pal[6], pal[7], pal[8]
    edge_r, edge_g, edge_b = pal[9], pal[10], pal[11]

    for y in range(H):
        py = 1.0 - (y + 0.5) / H * 2.0
        for x in range(W):
            px = (x + 0.5) / W * 2.0 - 1.0
            sx = px * tanx
            sy = py * tany
            if ortho:
                ox = ex + rx * sx + ux * sy
                oy = ey + ry * sx + uy * sy
                oz = ez + rz * sx + uz * sy
                dx, dy, dz = fx, fy, fz
            else:
                ox, oy, oz = ex, ey, ez
                dx = fx + rx * sx + ux * sy
                dy = fy + ry * sx + uy * sy
                dz = fz + rz * sx + uz * sy
                il = 1.0 / math.sqrt(dx * dx + dy * dy + dz * dz)
                dx *= il
                dy *= il
                dz *= il

            ti = ID[y, x]
            if ti < 0:
                # ---- background: vignette + ground grid + contact shadow
                r0 = sx / tanx * 0.70
                g0 = (py + 0.18) * 0.70
                v = 1.0 - min(1.0, r0 * r0 + g0 * g0)
                br = pal[12] + pal[15] * v
                bg = pal[13] + pal[16] * v
                bb = pal[14] + pal[17] * v
                if grid > 0.0 and abs(dz) > 1e-5:
                    td = (gz - oz) / dz
                    if td > 0.0:
                        hx, hy = ox + dx * td, oy + dy * td
                        pix = (tanx * 2.0 / W * td if not ortho else tanx * 2.0 / W) / max(abs(dz), 0.14)
                        cov = 0.0
                        for g_step, g_str in ((grid, 0.34), (grid * 10.0, 0.75)):
                            if pix < g_step * 0.45:
                                lx = abs(hx / g_step - round(hx / g_step)) * g_step
                                ly = abs(hy / g_step - round(hy / g_step)) * g_step
                                w_px = pix * 1.15
                                c = max(1.0 - lx / w_px, 1.0 - ly / w_px)
                                if c > 0.0:
                                    fade = 1.0 - min(1.0, pix / (g_step * 0.45))
                                    cov = max(cov, c * g_str * fade)
                        dist = math.sqrt((hx - cam[14]) ** 2 + (hy - cam[15]) ** 2)
                        cov *= 1.0 - _smooth(cam[16] * 0.55, cam[16], dist)
                        ax_r = ax_g = 0.0
                        w_px = pix * 1.3
                        if abs(hy) < w_px:
                            ax_r = (1.0 - abs(hy) / w_px) * 0.8
                        if abs(hx) < w_px:
                            ax_g = (1.0 - abs(hx) / w_px) * 0.8
                        fade2 = 1.0 - _smooth(cam[16] * 0.55, cam[16], dist)
                        ax_r *= fade2
                        ax_g *= fade2
                        br += cov * pal[18] + ax_r * 0.17
                        bg += cov * pal[19] + ax_g * 0.17
                        bb += cov * pal[20] + ax_g * 0.022 + ax_r * 0.015
                        if has_shadow:
                            lx0 = hx - sh_ex
                            ly0 = hy - sh_ey
                            lz0 = gz - sh_ez
                            u = lx0 * shc[3] + ly0 * shc[4] + lz0 * shc[5]
                            v2 = lx0 * shc[6] + ly0 * shc[7] + lz0 * shc[8]
                            w = lx0 * shc[9] + ly0 * shc[10] + lz0 * shc[11]
                            occ = 0.0
                            for ix in range(-2, 3):
                                for iy in range(-2, 3):
                                    px2 = int(sh_w * 0.5 + u * sh_fx) + ix * 3
                                    py2 = int(sh_h * 0.5 - v2 * sh_fx) + iy * 3
                                    if 0 <= px2 < sh_w and 0 <= py2 < sh_h:
                                        if SZ[py2, px2] > -w + 1e-3:
                                            occ += 1.0
                            s = 1.0 - occ / 25.0 * 0.72
                            br *= s
                            bg *= s
                            bb *= s
                OUT[y, x, 0], OUT[y, x, 1], OUT[y, x, 2] = br, bg, bb
                NB[y, x] = 0.0
                continue

            # ---- intersect the pixel ray with the triangle: exact,
            # perspective-correct barycentrics regardless of clipping
            i0, i1, i2 = T[ti, 0], T[ti, 1], T[ti, 2]
            ax, ay, az = P[i0, 0], P[i0, 1], P[i0, 2]
            e1x, e1y, e1z = P[i1, 0] - ax, P[i1, 1] - ay, P[i1, 2] - az
            e2x, e2y, e2z = P[i2, 0] - ax, P[i2, 1] - ay, P[i2, 2] - az
            hx2 = dy * e2z - dz * e2y
            hy2 = dz * e2x - dx * e2z
            hz2 = dx * e2y - dy * e2x
            det = e1x * hx2 + e1y * hy2 + e1z * hz2
            if abs(det) < 1e-12:
                b1 = b2 = 1.0 / 3.0
                tt = 0.0
            else:
                ide = 1.0 / det
                sx0, sy0, sz0 = ox - ax, oy - ay, oz - az
                b1 = (sx0 * hx2 + sy0 * hy2 + sz0 * hz2) * ide
                qx = sy0 * e1z - sz0 * e1y
                qy = sz0 * e1x - sx0 * e1z
                qz = sx0 * e1y - sy0 * e1x
                b2 = (dx * qx + dy * qy + dz * qz) * ide
                tt = (e2x * qx + e2y * qy + e2z * qz) * ide
            b1 = max(0.0, min(1.0, b1))
            b2 = max(0.0, min(1.0 - b1, b2))
            b0 = 1.0 - b1 - b2
            wx, wy, wz = ox + dx * tt, oy + dy * tt, oz + dz * tt

            nx = b0 * TN[ti, 0, 0] + b1 * TN[ti, 1, 0] + b2 * TN[ti, 2, 0]
            ny = b0 * TN[ti, 0, 1] + b1 * TN[ti, 1, 1] + b2 * TN[ti, 2, 1]
            nz = b0 * TN[ti, 0, 2] + b1 * TN[ti, 1, 2] + b2 * TN[ti, 2, 2]
            il = 1.0 / max(math.sqrt(nx * nx + ny * ny + nz * nz), 1e-9)
            nx *= il
            ny *= il
            nz *= il
            ndv = -(nx * dx + ny * dy + nz * dz)
            if ndv < 0.0:
                nx, ny, nz, ndv = -nx, -ny, -nz, -ndv
            NB[y, x] = nz

            part = tpart[ti]
            # ---- material
            paint = ppaint[part]
            cr, cg, cb = clay_r, clay_g, clay_b
            metal, rough, emr, emg, emb = 0.0, 0.74, 0.0, 0.0, 0.0
            band = 0.0
            if paint > 0.0:
                sv = b0 * VPAINT[i0] + b1 * VPAINT[i1] + b2 * VPAINT[i2]
                w_band = 0.05
                reveal = 1.0 - _smooth(paint - w_band, paint, sv) if paint < 1.5 else 1.0
                if paint < 1.5:
                    d_b = abs(sv - paint + w_band * 0.5) / w_band
                    if d_b < 1.0:
                        band = (1.0 - d_b) ** 2
                if reveal > 0.0:
                    mi = tmat[ti]
                    if mi >= 0:
                        mr, mg, mb = mcol[mi, 0], mcol[mi, 1], mcol[mi, 2]
                        if has_col and mpar[mi, 6] > 0.5 and TCOL[ti, 0, 0] >= 0.0:
                            mr = b0 * TCOL[ti, 0, 0] + b1 * TCOL[ti, 1, 0] + b2 * TCOL[ti, 2, 0]
                            mg = b0 * TCOL[ti, 0, 1] + b1 * TCOL[ti, 1, 1] + b2 * TCOL[ti, 2, 1]
                            mb = b0 * TCOL[ti, 0, 2] + b1 * TCOL[ti, 1, 2] + b2 * TCOL[ti, 2, 2]
                        if has_uv and mtex[mi, 0] >= 0:
                            uu = b0 * TUV[ti, 0, 0] + b1 * TUV[ti, 1, 0] + b2 * TUV[ti, 2, 0]
                            vv = b0 * TUV[ti, 0, 1] + b1 * TUV[ti, 1, 1] + b2 * TUV[ti, 2, 1]
                            tw, th = mtex[mi, 1], mtex[mi, 2]
                            tx = int((uu - math.floor(uu)) * tw) % tw
                            ty = int((1.0 - (vv - math.floor(vv))) * th) % th
                            o = mtex[mi, 0] + (ty * tw + tx) * 3
                            mr = (tex[o] / 255.0) ** 2.2
                            mg = (tex[o + 1] / 255.0) ** 2.2
                            mb = (tex[o + 2] / 255.0) ** 2.2
                        cr += (mr - cr) * reveal
                        cg += (mg - cg) * reveal
                        cb += (mb - cb) * reveal
                        metal = mpar[mi, 0] * reveal
                        rough += (mpar[mi, 1] - rough) * reveal
                        emr, emg, emb = mpar[mi, 3] * reveal, mpar[mi, 4] * reveal, mpar[mi, 5] * reveal

            # ---- lighting
            if flat_mode:
                l = 0.09 + 0.31 * ndv
                lr = cr * l
                lg = cg * l
                lb = cb * l
            else:
                shad = 1.0
                if has_shadow:
                    lx0 = wx + nx * cam[17] - sh_ex
                    ly0 = wy + ny * cam[17] - sh_ey
                    lz0 = wz + nz * cam[17] - sh_ez
                    u = lx0 * shc[3] + ly0 * shc[4] + lz0 * shc[5]
                    v2 = lx0 * shc[6] + ly0 * shc[7] + lz0 * shc[8]
                    w = lx0 * shc[9] + ly0 * shc[10] + lz0 * shc[11]
                    occ = 0.0
                    for ix in range(-1, 2):
                        for iy in range(-1, 2):
                            px2 = int(sh_w * 0.5 + u * sh_fx) + ix * 2
                            py2 = int(sh_h * 0.5 - v2 * sh_fx) + iy * 2
                            if 0 <= px2 < sh_w and 0 <= py2 < sh_h:
                                if SZ[py2, px2] > -w + cam[18]:
                                    occ += 1.0
                    shad = 1.0 - occ / 9.0

                ldx, ldy, ldz = shc[9], shc[10], shc[11]
                ndl = -(nx * ldx + ny * ldy + nz * ldz)
                wrap = max(0.0, (ndl + 0.18) / 1.18)
                # sky + bounce + key + opposing fill
                hemi = 0.5 + 0.5 * nz
                dr = (0.075 + 0.25 * hemi) + wrap * shad * 0.98
                dg = (0.082 + 0.26 * hemi) + wrap * shad * 0.93
                db = (0.105 + 0.32 * hemi) + wrap * shad * 0.84
                ndf = max(0.0, nx * 0.65 + ny * -0.2 + nz * 0.25)
                dr += ndf * 0.12
                dg += ndf * 0.16
                db += ndf * 0.24

                kd = 1.0 - metal
                lr = cr * dr * kd
                lg = cg * dg * kd
                lb = cb * db * kd

                # image-based specular
                rdn = dx * nx + dy * ny + dz * nz
                rx2 = dx - 2.0 * rdn * nx
                ry2 = dy - 2.0 * rdn * ny
                rz2 = dz - 2.0 * rdn * nz
                er, eg, eb = _env(rx2, ry2, rz2, rough)
                fres = (1.0 - ndv) ** 5
                f0r = 0.04 + (cr - 0.04) * metal
                f0g = 0.04 + (cg - 0.04) * metal
                f0b = 0.04 + (cb - 0.04) * metal
                gloss = (1.0 - rough) * (1.0 if paint > 0.0 else 0.4)
                sp = 0.35 + 0.65 * shad
                lr += er * (f0r + (max(gloss, f0r) - f0r) * fres) * sp
                lg += eg * (f0g + (max(gloss, f0g) - f0g) * fres) * sp
                lb += eb * (f0b + (max(gloss, f0b) - f0b) * fres) * sp
                lr += emr
                lg += emg
                lb += emb

            if band > 0.0:
                lr += edge_r * band * 1.5
                lg += edge_g * band * 1.5
                lb += edge_b * band * 1.5

            # ---- edit-mode overlays
            sel = b0 * VSEL[i0] + b1 * VSEL[i1] + b2 * VSEL[i2]
            wa = pwire[part]
            if sel > 0.002:
                lr += (sel_r * 0.7 - lr) * sel * 0.62
                lg += (sel_g * 0.7 - lg) * sel * 0.62
                lb += (sel_b * 0.7 - lb) * sel * 0.62
                wa = max(wa, sel)
            if wa > 0.002 and tt > 0.0:
                az_v = (ax - ex) * fx + (ay - ey) * fy + (az - ez) * fz
                if ortho or az_v > 1e-4:
                    # distance to each real polygon edge, in pixels
                    sc = 1.0 if ortho else 1.0 / max(tt, 1e-6)
                    best = 1e9
                    mask = tmask[ti]
                    e0 = math.sqrt(e1x * e1x + e1y * e1y + e1z * e1z)
                    e2 = math.sqrt(e2x * e2x + e2y * e2y + e2z * e2z)
                    bx, by, bz = e2x - e1x, e2y - e1y, e2z - e1z
                    e1 = math.sqrt(bx * bx + by * by + bz * bz)
                    cx = e1y * e2z - e1z * e2y
                    cy = e1z * e2x - e1x * e2z
                    cz = e1x * e2y - e1y * e2x
                    twice = math.sqrt(cx * cx + cy * cy + cz * cz)
                    w2p = fxpx * sc
                    if mask & 1:
                        best = min(best, b2 * twice / max(e0, 1e-9) * w2p)
                    if mask & 2:
                        best = min(best, b0 * twice / max(e1, 1e-9) * w2p)
                    if mask & 4:
                        best = min(best, b1 * twice / max(e2, 1e-9) * w2p)
                    # fade dense wires so high-poly meshes do not go solid
                    h_min = min(e0, min(e1, e2)) * w2p
                    dens = _smooth(5.0, 16.0, h_min)
                    cov = 1.0 - _smooth(0.45, 1.65, best)
                    k = cov * dens * wa
                    if k > 0.0:
                        lr += (wire_r + (sel_r - wire_r) * sel - lr) * k
                        lg += (wire_g + (sel_g - wire_g) * sel - lg) * k
                        lb += (wire_b + (sel_b - wire_b) * sel - lb) * k

            OUT[y, x, 0], OUT[y, x, 1], OUT[y, x, 2] = lr, lg, lb


@njit(cache=True, nogil=True, fastmath=True, error_model="numpy")
def cavity(OUT, Z, ID, ortho, strength):
    """Screen-space cavity: the second difference of depth along four axes.
    Concave creases darken, convex ridges catch a faint highlight — the look
    of a modelling viewport, at ~2 lookups per sample and no normal pass."""
    H, W = Z.shape
    steps = ((1, 0), (0, 1), (1, 1), (1, -1))
    for y in range(4, H - 4):
        for x in range(4, W - 4):
            if ID[y, x] < 0:
                continue
            zc = Z[y, x]
            if zc <= 0.0 and not ortho:
                continue
            dc = 1.0 / zc if not ortho else -zc
            acc = 0.0
            for rad in (2, 4):
                for sx, sy in steps:
                    x1, y1 = x + sx * rad, y + sy * rad
                    x2, y2 = x - sx * rad, y - sy * rad
                    if ID[y1, x1] < 0 or ID[y2, x2] < 0:
                        continue
                    z1, z2 = Z[y1, x1], Z[y2, x2]
                    if not ortho and (z1 <= 0.0 or z2 <= 0.0):
                        continue
                    d1 = 1.0 / z1 if not ortho else -z1
                    d2 = 1.0 / z2 if not ortho else -z2
                    curv = (d1 + d2 - 2.0 * dc) / max(dc, 1e-6)
                    acc += max(-0.012, min(0.012, curv))
            k = 1.0 + acc * strength
            k = 0.32 if k < 0.32 else (1.45 if k > 1.45 else k)
            OUT[y, x, 0] *= k
            OUT[y, x, 1] *= k
            OUT[y, x, 2] *= k


@njit(cache=True, nogil=True, fastmath=True, error_model="numpy")
def resolve(OUT, SS, W, H, RGBA, exposure):
    """Box downsample + filmic tone curve + sRGB, into an RGBA8 target."""
    iss = 1.0 / (SS * SS)
    for y in range(H):
        for x in range(W):
            r = g = b = 0.0
            for j in range(SS):
                for i in range(SS):
                    r += OUT[y * SS + j, x * SS + i, 0]
                    g += OUT[y * SS + j, x * SS + i, 1]
                    b += OUT[y * SS + j, x * SS + i, 2]
            r, g, b = r * iss * exposure, g * iss * exposure, b * iss * exposure
            for c in range(3):
                v = r if c == 0 else (g if c == 1 else b)
                v = max(0.0, v)
                v = (v * (2.51 * v + 0.03)) / (v * (2.43 * v + 0.59) + 0.14)
                v = 1.0 if v > 1.0 else v
                v = 12.92 * v if v < 0.0031308 else 1.055 * v ** (1.0 / 2.4) - 0.055
                RGBA[y, x, c] = int(v * 255.0 + 0.5)
            RGBA[y, x, 3] = 255


@njit(cache=True, nogil=True, fastmath=True, error_model="numpy")
def morph(P_out, P0, PS, PF, v0, nv, PA, PB, uA, uB, sA, sB, VSEL, has_b):
    """Vertex positions for a part being modelled: box → blockout → final,
    driven per-patch so each mouse moves only its own region."""
    for i in range(nv):
        vi = v0 + i
        a = PA[vi]
        u = uA[a]
        if has_b:
            b = PB[vi]
            w = uB[b]
            for c in range(3):
                x = P0[vi, c] + (PS[vi, c] - P0[vi, c]) * u
                P_out[vi, c] = x + (PF[vi, c] - x) * w
            VSEL[vi] = max(sA[a], sB[b])
        else:
            for c in range(3):
                P_out[vi, c] = P0[vi, c] + (PF[vi, c] - P0[vi, c]) * u
            VSEL[vi] = sA[a]


@njit(cache=True, nogil=True, fastmath=True, error_model="numpy")
def apply_transform(P, v0, nv, pivot0, pivot1, R, s):
    """Rigid object-level move: scale & rotate around the final pivot, then
    translate to where the mouse is currently dragging it."""
    for i in range(v0, v0 + nv):
        x = (P[i, 0] - pivot0[0]) * s
        y = (P[i, 1] - pivot0[1]) * s
        z = (P[i, 2] - pivot0[2]) * s
        P[i, 0] = pivot1[0] + R[0, 0] * x + R[0, 1] * y + R[0, 2] * z
        P[i, 1] = pivot1[1] + R[1, 0] * x + R[1, 1] * y + R[1, 2] * z
        P[i, 2] = pivot1[2] + R[2, 0] * x + R[2, 1] * y + R[2, 2] * z


@njit(cache=True, nogil=True, fastmath=True, error_model="numpy")
def recompute_normals(P, T, TN, t0, nt, v0, nv, smooth, VN):
    """Normals for geometry that is mid-deformation."""
    if smooth:
        for i in range(v0, v0 + nv):
            VN[i, 0] = VN[i, 1] = VN[i, 2] = 0.0
    for ti in range(t0, t0 + nt):
        a, b, c = T[ti, 0], T[ti, 1], T[ti, 2]
        ux, uy, uz = P[b, 0] - P[a, 0], P[b, 1] - P[a, 1], P[b, 2] - P[a, 2]
        vx, vy, vz = P[c, 0] - P[a, 0], P[c, 1] - P[a, 1], P[c, 2] - P[a, 2]
        nx = uy * vz - uz * vy
        ny = uz * vx - ux * vz
        nz = ux * vy - uy * vx
        if smooth:
            for k in (a, b, c):
                VN[k, 0] += nx
                VN[k, 1] += ny
                VN[k, 2] += nz
        else:
            il = 1.0 / max(math.sqrt(nx * nx + ny * ny + nz * nz), 1e-12)
            for k in range(3):
                TN[ti, k, 0] = nx * il
                TN[ti, k, 1] = ny * il
                TN[ti, k, 2] = nz * il
    if smooth:
        for ti in range(t0, t0 + nt):
            for k in range(3):
                vi = T[ti, k]
                nx, ny, nz = VN[vi, 0], VN[vi, 1], VN[vi, 2]
                il = 1.0 / max(math.sqrt(nx * nx + ny * ny + nz * nz), 1e-12)
                TN[ti, k, 0] = nx * il
                TN[ti, k, 1] = ny * il
                TN[ti, k, 2] = nz * il


# ---------------------------------------------------------- view interpolation
@njit(cache=True, nogil=True, fastmath=True, error_model="numpy", inline="always")
def _warp_fetch(C, Z, W, H, ux, uy, want_surface, zexp, strict=True):
    """Depth-aware bilinear lookup in a source view. Only texels that see the
    same thing the target pixel sees are used, so an object never smears over
    what is behind it. Returns premultiplied r, g, b and the total weight."""
    fx = ux - 0.5
    fy = uy - 0.5
    ix = int(math.floor(fx))
    iy = int(math.floor(fy))
    if ix < 0 or iy < 0 or ix >= W - 1 or iy >= H - 1:
        return 0.0, 0.0, 0.0, 0.0
    ax = fx - ix
    ay = fy - iy
    r = 0.0
    g = 0.0
    b = 0.0
    w = 0.0
    tol = zexp * 0.018
    if not want_surface and strict:
        # A texel beside an object carries some of its colour (the render is
        # anti-aliased); copied elsewhere that shows up as a floating speck.
        if ix < 1 or iy < 1 or ix >= W - 2 or iy >= H - 2:
            return 0.0, 0.0, 0.0, 0.0
        for j in range(-1, 3):
            for i in range(-1, 3):
                if Z[iy + j, ix + i] > 0.0:
                    return 0.0, 0.0, 0.0, 0.0
    for j in range(2):
        wy = ay if j else 1.0 - ay
        for i in range(2):
            z = Z[iy + j, ix + i]
            if want_surface:
                ok = z > 0.0 and abs(1.0 / z - zexp) <= tol
            else:
                ok = z <= 0.0
            if ok:
                k = (ax if i else 1.0 - ax) * wy
                r += k * C[iy + j, ix + i, 0]
                g += k * C[iy + j, ix + i, 1]
                b += k * C[iy + j, ix + i, 2]
                w += k
    return r, g, b, w


@njit(cache=True, nogil=True, fastmath=True, error_model="numpy")
def backdrop_rows(C, Z, W, H, RC):
    """Mean colour of the uncovered pixels in each row, for backdrops that are
    fixed to the screen. RC[y, 3] is the number of pixels that went into it."""
    for y in range(H):
        r = 0.0
        g = 0.0
        b = 0.0
        n = 0
        for x in range(W):
            if Z[y, x] <= 0.0:
                r += C[y, x, 0]
                g += C[y, x, 1]
                b += C[y, x, 2]
                n += 1
        k = 1.0 / max(n, 1)
        RC[y, 0] = r * k
        RC[y, 1] = g * k
        RC[y, 2] = b * k
        RC[y, 3] = n


@njit(cache=True, nogil=True, fastmath=True, error_model="numpy")
def view_interp(W, H, SS, ZT, ZA, ZB, CA, CB, RA, RB, cT, cA, cB, tanx, tany, floor_z, has_floor,
                screen_bg, fr, OUT):
    """Synthesises the view from camera cT out of two rendered views A and B.

    The scene's exact geometry is known, so instead of a cross-dissolve (which
    leaves a double image) every target sample is traced to its point in the
    world and looked up in both renders. cX = eye, right, up, forward.
    ZT is the target depth at SS× resolution; ZA, ZB match the renders.
    screen_bg: the backdrop is fixed to the screen (studio rig) rather than to
    the world, so it must not be reprojected.
    """
    hw = W * 0.5
    hh = H * 0.5
    inv = 1.0 / SS
    norm = 1.0 / (SS * SS)
    ga = 1.0 - fr
    for y in range(H):
        for x in range(W):
            ar = 0.0
            ag = 0.0
            ab = 0.0
            for j in range(SS):
                for i in range(SS):
                    px = x + (i + 0.5) * inv
                    py = y + (j + 0.5) * inv
                    ndx = (px - hw) / hw * tanx
                    ndy = (hh - py) / hh * tany
                    dx = cT[9] + cT[3] * ndx + cT[6] * ndy
                    dy = cT[10] + cT[4] * ndx + cT[7] * ndy
                    dz = cT[11] + cT[5] * ndx + cT[8] * ndy
                    zt = ZT[y * SS + j, x * SS + i]
                    surface = zt > 0.0
                    infinite = False
                    if surface:
                        z = 1.0 / zt
                    elif has_floor and dz < -1e-6 and (floor_z - cT[2]) / dz > 0.0:
                        z = (floor_z - cT[2]) / dz
                    else:
                        z = 1.0
                        infinite = True
                    if infinite and screen_bg:
                        r0, g0, b0, w0 = _warp_fetch(CA, ZA, W, H, px, py, False, 1.0)
                        r1, g1, b1, w1 = _warp_fetch(CB, ZB, W, H, px, py, False, 1.0)
                        den = ga * w0 + fr * w1
                        if den > 1e-3:
                            ar += (ga * r0 + fr * r1) / den
                            ag += (ga * g0 + fr * g1) / den
                            ab += (ga * b0 + fr * b1) / den
                        else:
                            ar += ga * RA[y, 0] + fr * RB[y, 0]
                            ag += ga * RA[y, 1] + fr * RB[y, 1]
                            ab += ga * RA[y, 2] + fr * RB[y, 2]
                        continue
                    if infinite:
                        qx, qy, qz = dx, dy, dz
                        ox, oy, oz = 0.0, 0.0, 0.0
                    else:
                        qx, qy, qz = cT[0] + dx * z, cT[1] + dy * z, cT[2] + dz * z
                        ox, oy, oz = 1.0, 1.0, 1.0
                    floor_hit = not surface and not infinite
                    # ---- into view A
                    vx = qx - cA[0] * ox
                    vy = qy - cA[1] * oy
                    vz = qz - cA[2] * oz
                    za = vx * cA[9] + vy * cA[10] + vz * cA[11]
                    r0 = g0 = b0 = w0 = 0.0
                    if za > 1e-9:
                        ux = hw + (vx * cA[3] + vy * cA[4] + vz * cA[5]) / za / tanx * hw
                        uy = hh - (vx * cA[6] + vy * cA[7] + vz * cA[8]) / za / tany * hh
                        r0, g0, b0, w0 = _warp_fetch(CA, ZA, W, H, ux, uy, surface, za)
                    # ---- into view B
                    vx = qx - cB[0] * ox
                    vy = qy - cB[1] * oy
                    vz = qz - cB[2] * oz
                    za = vx * cB[9] + vy * cB[10] + vz * cB[11]
                    r1 = g1 = b1 = w1 = 0.0
                    if za > 1e-9:
                        ux = hw + (vx * cB[3] + vy * cB[4] + vz * cB[5]) / za / tanx * hw
                        uy = hh - (vx * cB[6] + vy * cB[7] + vz * cB[8]) / za / tany * hh
                        r1, g1, b1, w1 = _warp_fetch(CB, ZB, W, H, ux, uy, surface, za)
                    den = ga * w0 + fr * w1
                    if den <= 1e-3 and floor_hit:
                        # right against an object: the contact shadow lives here, so
                        # settle for a near texel rather than the far backdrop colour
                        r0, g0, b0, w0 = _warp_fetch(CA, ZA, W, H, px, py, False, 1.0, False)
                        r1, g1, b1, w1 = _warp_fetch(CB, ZB, W, H, px, py, False, 1.0, False)
                        den = ga * w0 + fr * w1
                    if den > 1e-3:
                        ar += (ga * r0 + fr * r1) / den
                        ag += (ga * g0 + fr * g1) / den
                        ab += (ga * b0 + fr * b1) / den
                    elif infinite and RA[y, 3] + RB[y, 3] > 0.0:
                        ar += ga * RA[y, 0] + fr * RB[y, 0]
                        ag += ga * RA[y, 1] + fr * RB[y, 1]
                        ab += ga * RA[y, 2] + fr * RB[y, 2]
                    else:        # hidden from both: a plain dissolve is all that is left
                        ar += ga * CA[y, x, 0] + fr * CB[y, x, 0]
                        ag += ga * CA[y, x, 1] + fr * CB[y, x, 1]
                        ab += ga * CA[y, x, 2] + fr * CB[y, x, 2]
            OUT[y, x, 0] = int(ar * norm + 0.5)
            OUT[y, x, 1] = int(ag * norm + 0.5)
            OUT[y, x, 2] = int(ab * norm + 0.5)
            OUT[y, x, 3] = 255


# ------------------------------------------------------------- finishing
@njit(cache=True, nogil=True, fastmath=True, error_model="numpy")
def bloom_map(RGBA, W, H, step, threshold, B, passes=3):
    """Bright parts of the frame, shrunk and box-blurred. B is (h, w, 3)."""
    bh, bw = B.shape[0], B.shape[1]
    for y in range(bh):
        for x in range(bw):
            r = g = b = 0.0
            for j in range(step):
                for i in range(step):
                    yy, xx = min(y * step + j, H - 1), min(x * step + i, W - 1)
                    r += RGBA[yy, xx, 0]
                    g += RGBA[yy, xx, 1]
                    b += RGBA[yy, xx, 2]
            k = 1.0 / (step * step * 255.0)
            r, g, b = r * k, g * k, b * k
            lum = 0.2126 * r + 0.7152 * g + 0.0722 * b
            w = max(0.0, lum - threshold) / max(1.0 - threshold, 1e-4)
            B[y, x, 0], B[y, x, 1], B[y, x, 2] = r * w, g * w, b * w
    tmp = np.empty((bh, bw, 3), np.float32)
    rad = 2
    norm = 1.0 / (rad * 2 + 1)
    for _ in range(passes):
        for y in range(bh):
            for x in range(bw):
                for c in range(3):
                    s = 0.0
                    for k in range(-rad, rad + 1):
                        s += B[y, min(bw - 1, max(0, x + k)), c]
                    tmp[y, x, c] = s * norm
        for y in range(bh):
            for x in range(bw):
                for c in range(3):
                    s = 0.0
                    for k in range(-rad, rad + 1):
                        s += tmp[min(bh - 1, max(0, y + k)), x, c]
                    B[y, x, c] = s * norm


@njit(cache=True, nogil=True, fastmath=True, error_model="numpy")
def finish_frame(RGBA, W, H, LUT, sat, strength, VIG, GRAIN, grain_amt, ox, oy, B, bloom, step):
    """Grade, bloom, vignette and grain, in place. strength ramps it all in."""
    gh, gw = GRAIN.shape[0], GRAIN.shape[1]
    bh, bw = B.shape[0], B.shape[1]
    inv = 1.0 / step
    for y in range(H):
        for x in range(W):
            r = float(LUT[RGBA[y, x, 0], 0])
            g = float(LUT[RGBA[y, x, 1], 1])
            b = float(LUT[RGBA[y, x, 2], 2])
            lum = 0.2126 * r + 0.7152 * g + 0.0722 * b
            r = lum + (r - lum) * sat
            g = lum + (g - lum) * sat
            b = lum + (b - lum) * sat
            if bloom > 0.0:
                fx = (x + 0.5) * inv - 0.5
                fy = (y + 0.5) * inv - 0.5
                ix = int(fx) if fx > 0.0 else 0
                iy = int(fy) if fy > 0.0 else 0
                ix1 = min(ix + 1, bw - 1)
                iy1 = min(iy + 1, bh - 1)
                ix = min(ix, bw - 1)
                iy = min(iy, bh - 1)
                ax = max(0.0, min(1.0, fx - ix))
                ay = max(0.0, min(1.0, fy - iy))
                for c in range(3):
                    v = ((B[iy, ix, c] * (1 - ax) + B[iy, ix1, c] * ax) * (1 - ay)
                         + (B[iy1, ix, c] * (1 - ax) + B[iy1, ix1, c] * ax) * ay) * bloom * 255.0
                    if c == 0:
                        r = 255.0 - (255.0 - r) * (255.0 - min(v, 255.0)) / 255.0
                    elif c == 1:
                        g = 255.0 - (255.0 - g) * (255.0 - min(v, 255.0)) / 255.0
                    else:
                        b = 255.0 - (255.0 - b) * (255.0 - min(v, 255.0)) / 255.0
            vg = VIG[y, x]
            r *= vg
            g *= vg
            b *= vg
            if grain_amt > 0.0:
                n = GRAIN[(y + oy) & (gh - 1), (x + ox) & (gw - 1)]
                lum = (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255.0
                k = grain_amt * 34.0 * (0.35 + 0.65 * (1.0 - abs(lum * 2.0 - 1.0)))
                r += n * k
                g += n * k
                b += n * k
            for c in range(3):
                src = float(RGBA[y, x, c])
                v = r if c == 0 else (g if c == 1 else b)
                v = src + (v - src) * strength
                RGBA[y, x, c] = int(0.0 if v < 0.0 else (255.0 if v > 255.0 else v) + 0.5)


@njit(cache=True, nogil=True, fastmath=True, error_model="numpy")
def splat_paths(POS, COL, end, stride, scale, W, H, BUF, gain):
    """Light-painting: adds every mouse's path to a float image.
    POS is (mice, samples, 2); COL is (samples, 3)."""
    n_mice, n = POS.shape[0], POS.shape[1]
    end = min(end, n)
    for m in range(n_mice):
        x0 = POS[m, 0, 0] * scale
        y0 = POS[m, 0, 1] * scale
        for i in range(stride, end, stride):
            x1 = POS[m, i, 0] * scale
            y1 = POS[m, i, 1] * scale
            dx = x1 - x0
            dy = y1 - y0
            steps = int(max(abs(dx), abs(dy))) + 1
            # energy per pixel falls as a stroke gets faster, as with a real long exposure
            e = gain / (1.0 + steps * 0.05)
            if x0 > -300 and x1 > -300:
                for s in range(steps):
                    u = s / steps
                    px = int(x0 + dx * u)
                    py = int(y0 + dy * u)
                    if 0 <= px < W and 0 <= py < H:
                        BUF[py, px, 0] += COL[i, 0] * e
                        BUF[py, px, 1] += COL[i, 1] * e
                        BUF[py, px, 2] += COL[i, 2] * e
            x0, y0 = x1, y1


@njit(cache=True, nogil=True, fastmath=True, error_model="numpy")
def tonemap_paths(BUF, W, H, base, RGBA, exposure):
    """Soft saturation of the accumulated paths over a solid base colour.
    On a dark base they add up as light. On a light base they are ink: the coverage
    pulls the paper toward the stroke's own hue, so an orange path stays orange."""
    ink = base[0] + base[1] + base[2] >= 1.5
    for y in range(H):
        for x in range(W):
            peak = max(BUF[y, x, 0], BUF[y, x, 1], BUF[y, x, 2])
            cover = 1.0 - math.exp(-peak * exposure * 3.5) if ink else 0.0
            for c in range(3):
                if ink:
                    tint = BUF[y, x, c] / peak if peak > 1e-9 else 1.0
                    v = base[c] * (1.0 - cover) + 0.72 * tint * tint * cover
                else:
                    v = base[c] + (1.0 - base[c]) * (1.0 - math.exp(-BUF[y, x, c] * exposure))
                v = 12.92 * v if v < 0.0031308 else 1.055 * v ** (1.0 / 2.4) - 0.055
                RGBA[y, x, c] = int(max(0.0, min(255.0, v * 255.0 + 0.5)))
            RGBA[y, x, 3] = 255
