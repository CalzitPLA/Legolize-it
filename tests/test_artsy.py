"""Artsy mode (lego_engine.artsy): reading round bodies, symmetry, cleaning, colour blocking, axle stacks, printed
details, end to end."""
from pathlib import Path

import numpy as np
import pytest
import trimesh

from lego_engine import LegoSettings, Legolizer
from lego_engine.artsy import (block_colours, clean, mirror_score, roundness, sample_surface, stack_parts,
                               symmetrise)
from lego_engine.technic import PARTS


def _cylinder(r=12.0, length=64.0, axis="x", centre=(48.0, 16.0, 26.0)):
    m = trimesh.creation.cylinder(radius=r, height=length, sections=64)
    if axis == "x":
        m.apply_transform(trimesh.transformations.rotation_matrix(np.pi / 2, [0, 1, 0]))
    elif axis == "y":
        m.apply_transform(trimesh.transformations.rotation_matrix(np.pi / 2, [1, 0, 0]))
    m.apply_translation(centre)
    return m


def test_roundness_tells_a_cylinder_from_a_box():
    cyl = _cylinder()
    pts = sample_surface(np.asarray(cyl.vertices), np.asarray(cyl.faces))
    scores = [roundness(pts, a)[0] for a in range(3)]
    assert int(np.argmin(scores)) == 0 and scores[0] < 0.05
    score, line, t0, t1, prof = roundness(pts, 0)
    assert np.allclose(line, (16.0, 26.0), atol=0.5) and abs(t1 - t0 - 64.0) < 1.0
    assert np.allclose(prof[:, 1], 12.0, atol=0.6)
    box = trimesh.creation.box(extents=(60, 30, 10))
    pts = sample_surface(np.asarray(box.vertices), np.asarray(box.faces))
    assert min(roundness(pts, a)[0] for a in range(3)) > 0.12


def test_symmetrise_makes_a_noisy_shape_symmetric():
    rng = np.random.default_rng(1)
    f = np.zeros((10, 6, 4), np.float32)
    f[2:8, 1:5, :3] = 1.0
    f[2, 1, 0] = 0.0                                   # a nick on one side only
    f += (rng.random(f.shape) < 0.02) * 0.6            # specks
    assert mirror_score(f, 0) < 1.0
    g, _t = symmetrise(f, None, [0])
    assert mirror_score(g, 0) == pytest.approx(1.0)


def test_clean_removes_stray_cells_and_fills_pinholes():
    occ = np.zeros((8, 8, 6), bool)
    occ[1:7, 1:7, 0:5] = True
    occ[3, 3, 2] = False                               # a pinhole inside
    occ[7, 7, 5] = True                                # a stray cell touching nothing by a face
    out = clean(occ)
    assert out[3, 3, 2] and not out[7, 7, 5]
    assert out[1:7, 1:7, 0:5].all()


def test_colour_blocking_takes_a_speck_into_its_surroundings():
    labels = np.full((6, 6, 1), 3, np.int32)
    labels[2, 2, 0] = 7                                # one cell of another colour
    occ = np.ones_like(labels, bool)
    out = block_colours(labels, occ)
    assert (out == 3).all()


def test_stack_parts_few_thick_parts_no_overlaps_upright_on_layers():
    prof = np.c_[np.arange(1, 64, 2.0), np.full(32, 12.0), np.zeros(32)]
    parts = stack_parts(prof, ["grey"] * 32, False)
    assert [k for k, _o, _c in parts] == ["rim_30x20"] * 3
    prof = np.c_[np.arange(1, 80, 2.0), np.r_[np.full(20, 15.0), np.full(20, 8.0)], np.zeros(40)]
    for upright in (False, True):
        parts = stack_parts(prof, ["red"] * 20 + ["tan"] * 20, upright, snap=3.2 if upright else 0.0)
        ends = [o + PARTS[k].length for k, o, _c in parts]
        assert all(parts[q + 1][1] >= ends[q] - 1e-6 for q in range(len(parts) - 1))
        if upright:
            assert all(abs(o / 3.2 - round(o / 3.2)) < 1e-6 for _k, o, _c in parts)
            assert all(k in ("round_plate_2x2", "round_brick_2x2", "round_brick_2x2_grille", "round_plate_4x4",
                             "round_brick_4x4", "round_plate_6x6") for k, _o, _c in parts)


def _run(tmp_path, meshes, **kw):
    path = tmp_path / "part.stl"
    trimesh.util.concatenate(meshes).export(path)
    return Legolizer().run(LegoSettings(input=str(path), size_mode="scale", size=1.0, style="artsy", **kw))


def test_a_lying_cylinder_becomes_an_axle_stack_that_holds(tmp_path):
    base = trimesh.creation.box(bounds=[[0, 0, 0], [96, 32, 9.6]])
    r = _run(tmp_path, [base, _cylinder()], unit="plate")
    assert len(r.model.assemblies) == 1
    a = r.model.assemblies[0]
    assert a.axis == 0 and len(a.supports) == 2 and a.support_layers == 3
    assert r.stats["stable"] and r.stats["craft"]["technic_parts"] >= 4
    keys = [k for k, _o, _c in a.parts]
    assert len(keys) <= 6                              # a few thick parts, not dozens of discs


def test_an_upright_column_becomes_a_stack_held_by_its_studs(tmp_path):
    base = trimesh.creation.box(bounds=[[0, 0, 0], [64, 64, 9.6]])
    col = trimesh.creation.cylinder(radius=8.0, height=48.0, sections=64)
    col.apply_translation((32.0, 32.0, 9.6 + 24.0))
    top = trimesh.creation.box(bounds=[[8, 8, 57.6], [56, 56, 67.2]])
    r = _run(tmp_path, [base, col, top], unit="plate")
    ups = [a for a in r.model.assemblies if a.axis == 2]
    assert ups and all(k.startswith("round_") for k, _o, _c in ups[0].parts)
    assert r.stats["stable"]


def test_a_small_knob_is_printed(tmp_path):
    block = trimesh.creation.box(bounds=[[0, 0, 0], [48, 32, 19.2]])
    knob = trimesh.creation.icosphere(subdivisions=2, radius=4.5)
    knob.apply_translation((24.0, 16.0, 19.2 + 3.5))
    r = _run(tmp_path, [block, knob], unit="brick", artsy_axles=False)
    assert r.stats["craft"]["printed"] >= 1


def test_a_part_that_is_one_stack_has_no_bricks_and_still_exports(tmp_path):
    col = trimesh.creation.cylinder(radius=8.0, height=48.0, sections=64)
    col.apply_translation((0.0, 0.0, 24.0))
    cap = trimesh.creation.cylinder(radius=20.0, height=9.6, sections=64)
    cap.apply_translation((0.0, 0.0, 52.8))
    r = _run(tmp_path, [col, cap], unit="plate")
    assert r.model.assemblies and not r.model.empty and r.stats["stable"]
    if r.model.bricks:
        pytest.skip("the part got bricks too: nothing special to test")
    from lego_engine.export_manual import write_manual
    from lego_engine.export_stl import write_stl_stack
    write_stl_stack(r, tmp_path / "stl", "layers")
    assert (tmp_path / "stl" / "bought.csv").read_text().count("\n") >= 3      # header, axle, parts
    man = write_manual(r, tmp_path / "manual", pdf=False)
    tex = Path(man["tex"]).read_text()
    assert "the whole model" in tex and "Technic" in tex


def test_a_stack_only_result_json_gives_its_parts_to_the_shop(tmp_path):
    col = trimesh.creation.cylinder(radius=8.0, height=48.0, sections=64)
    col.apply_translation((0.0, 0.0, 24.0))
    cap = trimesh.creation.cylinder(radius=20.0, height=9.6, sections=64)
    cap.apply_translation((0.0, 0.0, 52.8))
    r = _run(tmp_path, [col, cap], unit="plate")
    if r.model.bricks:
        pytest.skip("the part got bricks too: nothing special to test")
    r.save_json(tmp_path / "result.json")
    from lego_engine.shop import needs_from_json
    got = needs_from_json(tmp_path / "result.json")
    assert got and all(unit == "technic" for unit, _p, _c in got)
