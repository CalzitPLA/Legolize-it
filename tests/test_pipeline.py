import ast
import csv
import json
from dataclasses import replace

import numpy as np
import pytest

from lego_engine import LegoSettings, Legolizer
from lego_engine.cli import main as cli_main
from lego_engine.export_stl import write_stl_stack
from lego_engine.mesh_io import find_freecadcmd
from lego_engine.palette import PALETTE


def test_run_caches_the_voxelization(parts):
    lz = Legolizer()
    s = LegoSettings(input=str(parts["sphere"]), size=10, unit="plate")
    r1 = lz.run(s)
    r2 = lz.run(replace(s, threshold=0.7))
    assert r2.timings["voxelize_s"] == r1.timings["voxelize_s"]          # the same cached fraction
    assert r2.stats["cells"] < r1.stats["cells"]
    assert r1.stats["stable"]


def test_settings_round_trip():
    s = LegoSettings(input="x.stl", size=12.5, unit="plate", sizes=((1, 2), (2, 4)), shell=2)
    assert LegoSettings.from_dict(json.loads(json.dumps(s.to_dict()))) == s


def test_colours_come_from_a_coloured_input(parts):
    r = Legolizer().run(LegoSettings(input=str(parts["two"]), size_mode="scale", size=1.0, colour_mode="source"))
    assert r.source_colours is not None
    red, blue = np.array(PALETTE["red"]), np.array(PALETTE["blue"])
    checked = 0
    for b, c in zip(r.model.bricks, r.colours()):
        if b.i + b.w <= 2:                                   # wholly in x < 16: the red box
            assert np.allclose(c, red)
            checked += 1
        elif b.i >= 2:                                       # wholly in x >= 16: the blue box
            assert np.allclose(c, blue)
            checked += 1
    assert checked >= 2


def test_an_empty_result_says_why(parts):
    msgs = []
    s = LegoSettings(input=str(parts["plate"]), size_mode="scale", size=1.0, unit="plate", thin_walls="off")
    r = Legolizer(msgs.append).run(s)
    assert r.stats["bricks"] == 0 and any("thin walls" in m for m in msgs)


@pytest.mark.parametrize("mode", ("auto", "all"))
def test_a_thin_sheet_becomes_one_layer_of_bricks(parts, mode):
    r = Legolizer().run(LegoSettings(input=str(parts["plate"]), size_mode="scale", size=1.0, unit="plate",
                                     thin_walls=mode))
    assert r.stats["layers"] == 1 and r.stats["cells"] == 80 and r.stats["floating"] == 0


def test_auto_thin_walls_leave_solid_parts_alone(parts):
    lz = Legolizer()
    for name in ("sphere", "L"):
        on = lz.run(LegoSettings(input=str(parts[name]), size=12, unit="plate", thin_walls="auto"))
        off = lz.run(LegoSettings(input=str(parts[name]), size=12, unit="plate", thin_walls="off"))
        assert (on.occupancy == off.occupancy).all(), name
        assert on.stats["thin_cells"] == 0


def test_auto_thin_walls_keep_a_fin_on_its_body(tmp_path):
    """A 40 mm block with a 1 mm fin sticking out 40 mm: the fin fills no cell, auto adds it, attached to the block."""
    import trimesh
    block = trimesh.creation.box((40, 40, 38.4))
    block.apply_translation((20, 20, 19.2))
    fin = trimesh.creation.box((41, 1, 38.4))
    fin.apply_translation((59.5, 20, 19.2))
    path = tmp_path / "fin.stl"
    trimesh.boolean.union([block, fin], engine="manifold").export(path)
    lz = Legolizer()
    off = lz.run(LegoSettings(input=str(path), size_mode="scale", size=1.0, thin_walls="off"))
    auto = lz.run(LegoSettings(input=str(path), size_mode="scale", size=1.0, thin_walls="auto"))
    assert off.occupancy[5:, :, :].sum() == 0                      # the fin is lost without thin walls
    assert auto.occupancy[5:, 2, :].all()                          # with auto it is there, cell column y = 2
    assert auto.stats["groups"] == 1 and auto.stats["floating"] == 0


def test_old_results_with_a_bool_still_load():
    assert LegoSettings.from_dict({"thin_walls": True}).thin_walls == "all"
    assert LegoSettings.from_dict({"thin_walls": False}).thin_walls == "off"


def test_stl_stack(parts, tmp_path):
    r = Legolizer().run(LegoSettings(input=str(parts["L"]), size_mode="scale", size=1.0))
    m = write_stl_stack(r, tmp_path / "stack", "both")
    layers, bricks = m["files"]["layers"], m["files"]["bricks"]
    assert len(layers) == 3 and all(x["watertight"] for x in layers)
    assert all((tmp_path / "stack" / x["file"]).is_file() for x in layers + bricks)
    assert sum(b["count"] for b in bricks) == r.stats["bricks"]
    rows = list(csv.DictReader(open(tmp_path / "stack" / "parts.csv")))
    assert len(rows) == r.stats["bricks"]
    assert (tmp_path / "stack" / "assembly.stl").is_file() and (tmp_path / "stack" / "README.txt").is_file()


def test_cli_writes_the_result(parts, tmp_path, capsys):
    assert cli_main([str(parts["L"]), "--scale", "1", "--out", str(tmp_path), "--quiet"]) == 0
    summary = json.loads(capsys.readouterr().out)
    result = json.loads((tmp_path / "result.json").read_text())
    assert summary["bricks"] == len(result["bricks"]) == 3
    assert result["grid"]["shape"] == [4, 2, 3] and len(result["colours"]) == 3


@pytest.mark.skipif(find_freecadcmd() is None, reason="needs freecadcmd")
def test_freecad_document(parts, tmp_path):
    from lego_engine.export_freecad import write_fcstd
    r = Legolizer().run(LegoSettings(input=str(parts["L"]), size_mode="scale", size=1.0))
    info = write_fcstd(r, tmp_path / "L.FCStd")
    assert info["bricks"] == 3 and (tmp_path / "L.FCStd").stat().st_size > 1000
    macro = (tmp_path / "L_colours.FCMacro").read_text()
    ast.parse(macro)
    assert '"Brick00002"' in macro and "INPUT_TRANSPARENCY = 80" in macro
