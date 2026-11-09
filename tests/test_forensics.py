# SPDX-License-Identifier: GPL-3.0-or-later
"""The names in the film must come from the geometry, and the pacing from its size."""
import json

import numpy as np
import pytest

from mousestorm import forensics as F
from mousestorm import hardware as H
from mousestorm import steps as S
from mousestorm.plan import axis_angle, find_subject, op_boost, rot_axis


def ring(n, r, z):
    a = np.arange(n) * 2 * np.pi / n
    return np.stack([r * np.cos(a), r * np.sin(a), np.full(n, z)], 1)


def cube():
    return np.array([[x, y, z] for x in (-1.0, 1.0) for y in (-1.0, 1.0) for z in (-1.0, 1.0)])


def cylinder(n=24, levels=2):
    return np.concatenate([ring(n, 1.0, z) for z in np.linspace(-1.0, 1.0, levels)])


def uv_sphere(segments=16, rings=8):
    pts = [[0.0, 0.0, 1.0], [0.0, 0.0, -1.0]]
    for k in range(1, rings):
        t = k * np.pi / rings
        pts.extend(ring(segments, np.sin(t), np.cos(t)).tolist())
    return np.array(pts)


@pytest.mark.parametrize("name,co,shape", [
    ("cube", cube(), "cube"),
    ("cylinder", cylinder(), "cylinder"),
    ("odd cylinder", cylinder(7), "cylinder"),
    ("cone", np.concatenate([ring(24, 1.0, -1.0), [[0.0, 0.0, 1.0]]]), "cone"),
    ("frustum", np.concatenate([ring(16, 1.0, -1.0), ring(16, 0.4, 1.0)]), "cone"),
    ("sphere", uv_sphere(), "uv"),
    ("plane", np.array([[-1.0, -1.0, 0.0], [1.0, -1.0, 0.0], [1.0, 1.0, 0.0], [-1.0, 1.0, 0.0]]), "plane"),
])
def test_exact_primitives_are_recognised(name, co, shape):
    fit = F.fit_primitive(co)
    assert fit.exact and fit.shape == shape, name
    assert np.allclose(fit.source, co)


def test_a_capless_cylinder_is_not_a_sphere():
    """Both ends sit at the same distance from the centre, which once fooled the sphere test."""
    assert F.fit_primitive(cylinder(32)).shape == "cylinder"


def test_a_scaled_box_is_still_a_box():
    assert F.fit_primitive(cube() * np.array([4.0, 0.5, 2.0])).shape == "cube"


def test_loop_cuts_are_counted_from_the_rings():
    fit = F.fit_primitive(cylinder(16, 5))
    assert fit.shape == "cylinder"
    assert [(e[0], e[3]) for e in fit.extras] == [("Loop Cut", 3)]


def test_a_real_operator_name_is_believed_for_an_edited_mesh():
    co = uv_sphere() * (1.0 + 0.2 * np.sin(uv_sphere()[:, 0:1] * 9.0))
    assert F.fit_primitive(co, names=("Cube.004",)).shape == "cube"


def test_other_names_prove_nothing():
    assert F.hint_of(("Cube_roof",)) is None
    assert F.hint_of(("Tower",)) is None
    assert F.hint_of(("Icosphere.012",)) == "ico"


def test_other_object_types():
    fit = F.fit_primitive(cube(), kind="FONT")
    assert fit.exact and fit.label == "Add Text"


def test_duplicate_shapes_share_a_key():
    tris = np.array([[0, 1, 2], [1, 2, 3]])
    co = cube()[:4]
    assert F.shape_key(co, tris) == F.shape_key(co.copy(), tris)
    other = co.copy()
    other[0, 0] += 0.2
    assert F.shape_key(co, tris) != F.shape_key(other, tris)


# ---------------------------------------------------------------- patch names
def grid(n=21):
    x, y = np.meshgrid(np.linspace(-1, 1, n), np.linspace(-1, 1, n), indexing="ij")
    co = np.stack([x.ravel(), y.ravel(), np.zeros(n * n)], 1)
    tris = []
    for i in range(n - 1):
        for j in range(n - 1):
            a, b, c, d = i * n + j, (i + 1) * n + j, (i + 1) * n + j + 1, i * n + j + 1
            tris += [[a, b, c], [a, c, d]]
    return co, np.array(tris)


def test_a_change_too_small_to_see_is_not_an_operation():
    co, tris = grid()
    f = F.measure(co, co + np.array([0, 0, 1e-7]), tris, np.zeros(len(co), np.int64), 1)
    assert F.name_edit(f, 0, 2.8) is None


def test_lifting_a_flat_patch_is_a_move_and_the_value_is_measured():
    co, tris = grid()
    f = F.measure(co, co + np.array([0, 0, 0.3]), tris, np.zeros(len(co), np.int64), 1)
    op = F.name_edit(f, 0, 2.8)
    assert op["label"] == "Move"
    assert op["chip"][1] == pytest.approx(0.3)


def test_a_raised_block_with_walls_is_an_extrusion():
    co, tris = grid()
    dst = co.copy()
    inside = (np.abs(co[:, 0]) < 0.45) & (np.abs(co[:, 1]) < 0.45)
    dst[inside, 2] = 0.5
    f = F.measure(co, dst, tris, inside.astype(np.int64), 2)
    assert F.name_edit(f, 1, 2.8)["label"] == "Extrude Region"
    assert F.name_edit(f, 0, 2.8) is None


def test_shrinking_toward_the_centre_on_a_flat_patch_is_an_inset():
    co, tris = grid()
    f = F.measure(co, co * np.array([0.6, 0.6, 1.0]), tris, np.zeros(len(co), np.int64), 1)
    assert F.name_edit(f, 0, 2.8)["label"] == "Inset Faces"


def test_a_twist_is_a_rotation_of_the_measured_angle():
    co, tris = grid()
    R = rot_axis(np.array([0.0, 0.0, 1.0]), np.radians(25.0))
    f = F.measure(co, co @ R.T, tris, np.zeros(len(co), np.int64), 1)
    op = F.name_edit(f, 0, 2.8)
    assert op["label"] == "Rotate"
    assert abs(op["chip"][1]) == pytest.approx(25.0, abs=4.0)


def test_sculpt_names_follow_the_direction_of_the_displacement():
    co, tris = grid()
    rise = np.exp(-(co[:, 0] ** 2 + co[:, 1] ** 2) / 0.4)
    up = F.measure(co, co + np.stack([0 * rise, 0 * rise, 0.1 * rise], 1), tris, np.zeros(len(co), np.int64), 1)
    assert F.name_sculpt(up, 0, 2.8)["label"] in ("Draw", "Inflate")
    groove = np.exp(-(co[:, 0] / 0.05) ** 2)
    down = F.measure(co, co - np.stack([0 * groove, 0 * groove, 0.1 * groove], 1), tris, np.zeros(len(co), np.int64), 1)
    op = F.name_sculpt(down, 0, 2.8)
    assert op["label"] == "Crease" and "INVERT" in op["api"]


# -------------------------------------------------------------------- pacing
def test_large_work_keeps_the_film_speed():
    assert op_boost([0.08, 0.5], 0.08, 6.0) == pytest.approx([1.0, 1.0])


def test_the_smallest_work_gets_the_whole_boost():
    assert op_boost([0.0, 0.001, 0.01], 0.08, 6.0) == pytest.approx([6.0, 6.0, 6.0])


def test_pacing_is_monotonic():
    b = op_boost(np.geomspace(1e-4, 1.0, 64), 0.08, 6.0)
    assert (np.diff(b) <= 1e-9).all() and b[0] > b[-1]


def test_a_boost_of_one_switches_the_rule_off():
    assert op_boost([0.0, 0.5], 0.08, 1.0) == pytest.approx([1.0, 1.0])


def test_real_rotation_round_trips():
    axis = np.array([1.0, 2.0, 2.0]) / 3.0
    a, angle = axis_angle(rot_axis(axis, 1.1))
    assert angle == pytest.approx(1.1)
    assert a == pytest.approx(axis)


# ----------------------------------------------------------------- step logs
def doc(*steps):
    return dict(format=S.FORMAT, version=S.VERSION, steps=list(steps))


def test_a_minimal_log_parses():
    log = S.parse(doc(dict(op="add", object="Tower", primitive="cylinder")))
    assert log["steps"][0]["primitive"] == "cylinder"


@pytest.mark.parametrize("bad,why", [
    ({"format": "other", "version": 1, "steps": [1]}, "not a Mousestorm"),
    ({"format": S.FORMAT, "version": 9, "steps": [1]}, "version"),
    ({"format": S.FORMAT, "version": 1, "steps": []}, "non-empty"),
    (doc(dict(op="teleport", object="A")), "step 1"),
    (doc(dict(op="add")), "object"),
    (doc(dict(op="edit", object="A")), "label"),
    (doc(dict(op="add", object="A", primitive="dodecahedron")), "primitive"),
    (doc(dict(op="bevel", object="A", value="wide")), "number"),
    (doc(dict(op="bevel", object="A", where={"bbox": [1, 2]})), "bbox"),
])
def test_bad_logs_say_what_is_wrong(bad, why):
    with pytest.raises(ValueError, match=why):
        S.parse(bad)


def test_the_step_at_fault_is_numbered():
    with pytest.raises(ValueError, match="step 2"):
        S.parse(doc(dict(op="add", object="A"), dict(op="nope", object="A")))


def test_text_cannot_break_a_line():
    log = S.parse(doc(dict(op="edit", object="A", label="two\nlines")))
    assert log["steps"][0]["label"] == "two lines"


def test_load_accepts_a_byte_order_mark(tmp_path):
    path = tmp_path / "a.msteps"
    path.write_bytes(b"\xef\xbb\xbf" + json.dumps(doc(dict(op="add", object="A"))).encode())
    assert S.load(str(path))["steps"][0]["object"] == "A"


def test_load_reports_broken_json(tmp_path):
    path = tmp_path / "a.msteps"
    path.write_text("{oops", encoding="utf-8")
    with pytest.raises(ValueError, match="not valid JSON"):
        S.load(str(path))


def test_steps_are_grouped_per_object_in_order():
    log = S.parse(doc(dict(op="add", object="B"), dict(op="add", object="A"), dict(op="bevel", object="B")))
    groups = S.by_object(log)
    assert list(groups) == ["B", "A"]
    assert [st["op"] for st in groups["B"]] == ["add", "bevel"]


def test_a_region_claims_the_patches_inside_it():
    steps = [dict(op="extrude", object="A", bbox=([0, 0, 0], [1, 1, 1])), dict(op="bevel", object="A")]
    points = [("edit", [0.5, 0.5, 0.5]), ("edit", [5.0, 5.0, 5.0])]
    buckets, left = S.split_patches(points, steps)
    assert buckets == [[0], [1]] and left == []


def test_patches_go_to_steps_of_their_kind_first():
    steps = [dict(op="bevel", object="A"), dict(op="extrude", object="A")]
    points = [("extrude", [0.0, 0, 0]), ("bevel", [1.0, 0, 0])]
    buckets, _ = S.split_patches(points, steps)
    assert buckets == [[1], [0]]


def test_every_patch_is_spoken_for():
    steps = [dict(op="bevel", object="A"), dict(op="inset", object="A"), dict(op="smooth", object="A")]
    points = [("edit", [float(k), 0.0, 0.0]) for k in range(10)]
    buckets, left = S.split_patches(points, steps)
    assert not left and sorted(i for b in buckets for i in b) == list(range(10))
    assert all(buckets)


def test_regions_alone_leave_the_rest_to_inference():
    steps = [dict(op="bevel", object="A", bbox=([0, 0, 0], [1, 1, 1]))]
    points = [("edit", [0.5, 0.5, 0.5]), ("edit", [5.0, 5.0, 5.0])]
    assert S.split_patches(points, steps) == ([[0]], [1])


# ------------------------------------------------------------------ hardware
GPU_A = dict(id="CUDA_NVIDIA RTX_0000:00:09_OptiX", name="RTX A", backend="OPTIX")
GPU_B = dict(id="CUDA_NVIDIA RTX_0000:00:0a_OptiX", name="RTX B", backend="OPTIX")


def test_smi_output_is_matched_to_cycles_devices():
    load = H.parse_smi("00000000:00:09.0, 100, 49152, 3\n00000000:00:0A.0, 40000, 49152, 97\n")
    assert H.bus_key(GPU_A["id"]) in load and load[H.bus_key(GPU_B["id"])][2] == 97.0


def test_auto_prefers_the_idle_card():
    load = {H.bus_key(GPU_A["id"]): (34000.0, 49152.0, 95.0), H.bus_key(GPU_B["id"]): (400.0, 49152.0, 0.0)}
    assert H.pick([GPU_A, GPU_B], load)[0] is GPU_B


def test_auto_leaves_a_training_run_alone():
    busy = {H.bus_key(GPU_A["id"]): (34000.0, 49152.0, 95.0), H.bus_key(GPU_B["id"]): (30000.0, 49152.0, 0.0)}
    chosen, why = H.pick([GPU_A, GPU_B], busy)
    assert chosen is None and "busy" in why


def test_a_card_with_no_room_is_not_used():
    load = {H.bus_key(GPU_A["id"]): (2000.0, 3072.0, 0.0)}
    assert H.pick([GPU_A], load)[0] is None


def test_no_gpu_means_cpu():
    assert H.pick([], {})[0] is None


def test_a_card_nothing_can_measure_is_trusted():
    amd = dict(id="HRT_Radeon", name="Radeon", backend="HRT")
    assert H.pick([amd], {})[0] is amd


def test_workers_fit_the_memory():
    assert H.safe_workers(35, 200.0, 1920, 1080, 2) == 35
    assert H.safe_workers(9, 14.0, 1920, 1080, 2) == 4
    assert H.safe_workers(9, 1.0, 1920, 1080, 2) == 1
    assert H.safe_workers(9, None, 1920, 1080, 2) == 9


def test_bigger_frames_need_more_memory_per_worker():
    assert H.safe_workers(64, 64.0, 3840, 2160, 2) < H.safe_workers(64, 64.0, 1280, 720, 2)


# ------------------------------------------------------------------- framing
def box(c, half):
    c, half = np.array(c, float), np.array(half, float)
    return [c - half, c + half]


def areas(boxes):
    e = boxes[:, 1] - boxes[:, 0]
    return 2 * (e[:, 0] * e[:, 1] + e[:, 1] * e[:, 2] + e[:, 0] * e[:, 2])


def house_scene(extra=()):
    parts = [box((x * 2.0, y * 2.0, 1.0), (0.9, 0.9, 1.0)) for x in range(-4, 5) for y in range(-2, 3)]
    return np.array(parts + list(extra))


def test_kilometre_ground_slabs_are_not_the_subject():
    slabs = [box((0, 0, -1), (2000, 2000, 1)), box((-1000, 0, -0.5), (990, 2000, 0.5)), box((1000, 0, -0.5), (990, 2000, 0.5))]
    b = house_scene(slabs)
    keep = find_subject(b, areas(b))
    assert not keep[-3:].any() and keep[:-3].all()


def test_a_tower_on_a_flat_base_stays_in_the_frame():
    b = house_scene([box((0, 0, 30), (1, 1, 25))])
    assert find_subject(b, areas(b)).all()


def test_a_row_of_equal_objects_is_kept_whole():
    b = np.array([box((x * 3.0, 0, 1), (1, 1, 1)) for x in range(30)])
    assert find_subject(b, areas(b)).all()


def test_a_single_object_is_the_subject():
    b = np.array([box((0, 0, 0), (50, 50, 0.01))])
    assert find_subject(b, areas(b)).all()


def test_a_floor_under_a_few_props_is_not_the_subject():
    b = np.array([box((0, 0, -0.1), (40, 40, 0.1)), box((0, 0, 1), (1, 1, 1)), box((3, 0, 1), (1, 1, 1))])
    assert find_subject(b, areas(b)).tolist() == [False, True, True]


def looking_along_y(eye):
    M = np.eye(4)
    M[:3, :3] = np.array([[1, 0, 0], [0, 0, 1], [0, -1, 0]], float).T @ np.eye(3)
    M[:3, 3] = eye
    return dict(matrix=M.tolist(), lens=50.0, sensor=36.0, ortho=False)


def test_the_files_camera_narrows_the_subject():
    trees = [box((x * 4.0, 120.0, 3.0), (1.5, 1.5, 3.0)) for x in range(-8, 9)]
    b = house_scene(trees)
    keep = find_subject(b, areas(b), looking_along_y((0.0, -40.0, 3.0)))
    assert keep[:45].sum() >= 30 and not keep[45:].any()


def test_a_close_up_camera_does_not_decide_the_subject():
    b = house_scene()
    keep = find_subject(b, areas(b), looking_along_y((0.0, -5.5, 1.0)))
    assert keep.all()


def test_a_building_stands_out_from_the_planting_around_it():
    walls = [box((x * 3.0, y * 3.0, 2.0), (1.4, 1.4, 2.0)) for x in range(-3, 4) for y in range(-3, 4)]
    trees = [box((x * 5.0, 60.0 + y * 4.0, 3.0), (1.5, 1.5, 3.0)) for x in range(-5, 6) for y in range(5)]
    b = np.array(walls + trees)
    verts = np.array([24.0] * len(walls) + [4000.0] * len(trees))
    keep = find_subject(b, areas(b), None, verts)
    assert keep[:len(walls)].all() and not keep[len(walls):].any()


def test_narrowing_never_cuts_off_what_rises_above_the_mass():
    walls = [box((x * 3.0, y * 3.0, 2.0), (1.4, 1.4, 2.0)) for x in range(-3, 4) for y in range(-3, 4)]
    b = np.array(walls + [box((0, 0, 40), (0.5, 0.5, 36))])
    verts = np.array([24.0] * len(walls) + [200.0])
    assert find_subject(b, areas(b), None, verts).all()
