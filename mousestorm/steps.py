# SPDX-License-Identifier: GPL-3.0-or-later
"""The optional step log (.msteps): what an AI, or a person, says it did.

Most films do not need one: without it the operations are inferred from the
geometry (see forensics.py). With one, the film uses the recorded names, order
and values. The format is documented in docs/STEPS_FORMAT.md.
"""
import json
import os

FORMAT = "mousestorm.steps"
VERSION = 1
EXTENSION = ".msteps"
MAX_BYTES = 16 * 1024 * 1024
MAX_STEPS = 50000

# op id: (label, default call, cursor icon, name of the value)
EDIT_OPS = {
    "extrude": ("Extrude Region", "mesh.extrude_region_move()", "move", "Offset"),
    "inset": ("Inset Faces", "mesh.inset()", "scale", "Thickness"),
    "bevel": ("Bevel", "mesh.bevel()", "scale", "Width"),
    "loop_cut": ("Loop Cut", "mesh.loopcut_slide()", "cross", "Cuts"),
    "subdivide": ("Subdivide", "mesh.subdivide()", "arrow", "Cuts"),
    "knife": ("Knife Topology", "mesh.knife_tool()", "knife", "Cuts"),
    "bridge": ("Bridge Edge Loops", "mesh.bridge_edge_loops()", "pointer", "Cuts"),
    "merge": ("Merge by Distance", "mesh.remove_doubles()", "arrow", "Distance"),
    "shrink_fatten": ("Shrink/Fatten", "transform.shrink_fatten()", "move", "Offset"),
    "smooth": ("Smooth Vertices", "mesh.vertices_smooth()", "arrow", "Factor"),
    "edit": (None, "", "arrow", "Value"),
}
SCULPT = "sculpt"
TRANSFORMS = {"move": ("Move", "transform.translate()"), "rotate": ("Rotate", "transform.rotate()"),
              "scale": ("Resize", "transform.resize()")}
CREATION = ("add", "duplicate")
OTHER = ("modifier",)
ALL_OPS = set(EDIT_OPS) | {SCULPT} | set(TRANSFORMS) | set(CREATION) | set(OTHER)
PRIMITIVES = {"cube": "Add Cube", "uv_sphere": "Add UV Sphere", "ico_sphere": "Add Ico Sphere", "cylinder": "Add Cylinder",
              "cone": "Add Cone", "torus": "Add Torus", "plane": "Add Plane", "grid": "Add Grid", "circle": "Add Circle",
              "monkey": "Add Monkey", "curve": "Add Curve", "text": "Add Text", "metaball": "Add Metaball", "empty_mesh": "Add Mesh"}
# which inferred names count as the same kind of work as a logged operation
FAMILY = {"Extrude Region": "extrude", "Bevel": "bevel", "Inset Faces": "inset", "Shrink/Fatten": "shrink_fatten"}


def _text(step, key, limit):
    value = step.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("%s must be text" % key)
    value = " ".join(value.split())
    if not value:
        return None
    if len(value) > limit:
        raise ValueError("%s is longer than %d characters" % (key, limit))
    return value


def _clean(step):
    if not isinstance(step, dict):
        raise ValueError("must be an object")
    op = step.get("op")
    if op not in ALL_OPS:
        raise ValueError('"op" must be one of: %s' % ", ".join(sorted(ALL_OPS)))
    out = dict(op=op)
    out["object"] = _text(step, "object", 256)
    if not out["object"]:
        raise ValueError('needs "object", the name of the Blender object')
    for key, limit in (("label", 60), ("call", 240), ("value_name", 24), ("primitive", 32), ("from", 256), ("brush", 40), ("modifier", 80)):
        v = _text(step, key, limit)
        if v:
            out[key] = v
    if op == "edit" and "label" not in out:
        raise ValueError('op "edit" needs a "label" saying what was done')
    if "primitive" in out and out["primitive"] not in PRIMITIVES:
        raise ValueError('"primitive" must be one of: %s' % ", ".join(sorted(PRIMITIVES)))
    value = step.get("value")
    if value is not None:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError('"value" must be a number')
        out["value"] = value
    where = step.get("where")
    if where is not None:
        box = where.get("bbox") if isinstance(where, dict) else None
        try:
            lo, hi = [[float(x) for x in corner] for corner in box]
        except (TypeError, ValueError):
            raise ValueError('"where" must be {"bbox": [[x, y, z], [x, y, z]]}') from None
        if len(lo) != 3 or len(hi) != 3:
            raise ValueError('"where" must be {"bbox": [[x, y, z], [x, y, z]]}')
        out["bbox"] = ([min(a, b) for a, b in zip(lo, hi)], [max(a, b) for a, b in zip(lo, hi)])
    return out


def parse(doc):
    """Checks a decoded document and returns the steps. Raises ValueError with a useful message."""
    if not isinstance(doc, dict) or doc.get("format") != FORMAT:
        raise ValueError('this is not a Mousestorm step log (expected "format": "%s")' % FORMAT)
    if doc.get("version") != VERSION:
        raise ValueError("unsupported step log version %r (this Mousestorm reads version %d)" % (doc.get("version"), VERSION))
    raw = doc.get("steps")
    if not isinstance(raw, list) or not raw:
        raise ValueError('"steps" must be a non-empty list')
    if len(raw) > MAX_STEPS:
        raise ValueError("too many steps (the limit is %d)" % MAX_STEPS)
    steps = []
    for n, step in enumerate(raw, 1):
        try:
            steps.append(_clean(step))
        except ValueError as e:
            raise ValueError("step %d: %s" % (n, e)) from None
    return dict(file=doc.get("file") if isinstance(doc.get("file"), str) else None,
                generator=doc.get("generator") if isinstance(doc.get("generator"), str) else None, steps=steps)


def load(path):
    if os.path.getsize(path) > MAX_BYTES:
        raise ValueError("%s is too large for a step log" % os.path.basename(path))
    try:
        with open(path, encoding="utf-8-sig") as f:
            doc = json.load(f)
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise ValueError("%s is not valid JSON: %s" % (os.path.basename(path), e)) from None
    try:
        return parse(doc)
    except ValueError as e:
        raise ValueError("%s: %s" % (os.path.basename(path), e)) from None


def by_object(log):
    """{object name: its steps in order}, keeping the order objects first appear in."""
    out = {}
    if log:
        for step in log["steps"]:
            out.setdefault(step["object"], []).append(step)
    return out


def split_patches(points, steps):
    """Shares an object's patches among its logged edit steps. points: (family, centre);
    steps: the logged edit steps. Returns (one index list per step, indices left over).

    A step with a "where" takes what lies inside it. The rest goes first to steps of
    the same kind of work, then evenly across the object in step order."""
    buckets = [[] for _ in steps]
    left = []
    for idx, (_, centre) in enumerate(points):
        for s, step in enumerate(steps):
            if "bbox" in step and all(a <= c <= b for a, c, b in zip(step["bbox"][0], centre, step["bbox"][1])):
                buckets[s].append(idx)
                break
        else:
            left.append(idx)
    free = [s for s, step in enumerate(steps) if "bbox" not in step]
    if not free:
        return buckets, left
    rest = []
    turn = {}
    for idx in left:
        family = points[idx][0]
        same = [s for s in free if steps[s]["op"] == family]
        if same:
            k = turn.get(family, 0)
            buckets[same[k % len(same)]].append(idx)
            turn[family] = k + 1
        else:
            rest.append(idx)
    if rest:
        centres = [points[i][1] for i in rest]
        axis = max(range(3), key=lambda a: max(c[a] for c in centres) - min(c[a] for c in centres))
        rest.sort(key=lambda i: points[i][1][axis])
        for n, idx in enumerate(rest):
            buckets[free[n * len(free) // len(rest)]].append(idx)
    return buckets, []
