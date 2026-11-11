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
    r = Legolizer(msgs.append).run(LegoSettings(input=str(parts["plate"]), size_mode="scale", size=1.0, unit="plate"))
    assert r.stats["bricks"] == 0 and any("thin walls" in m for m in msgs)
    r = Legolizer().run(LegoSettings(input=str(parts["plate"]), size_mode="scale", size=1.0, unit="plate",
                                     thin_walls=True))
    assert r.stats["bricks"] > 0 and r.stats["layers"] == 1 and r.stats["cells"] == 80 and r.stats["floating"] == 0


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
