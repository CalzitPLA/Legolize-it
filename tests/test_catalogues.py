"""The parts catalogue (lego_engine.catalogues): the makers' ranges, single parts switched off, and the engine
following them (axles, axle stacks, tiles, the command line)."""
import json

import numpy as np

from lego_engine.artsy import stack_parts
from lego_engine.catalogues import NOTICE, all_ids, available, parse_parts, restrict
from lego_engine.details import tile_cover
from lego_engine.technic import axles_for


def test_ranges_are_facts_per_unit_and_restrict_leaves_out_the_rest():
    lego_plates, lego_bricks = available("lego", "plate"), available("lego", "brick")
    assert "8x8" in lego_plates and "8x8" not in lego_bricks and "1x16" in lego_bricks
    assert "gear_24" in lego_plates and "curve_2x1" not in lego_plates      # shaped pieces: this program's, printed
    bb = available("bluebrixx", "plate")
    assert "2x4" in bb and "gear_24" not in bb and "tile_2x2" not in bb      # not in the data: see the website
    assert available("standard", "plate") == set(all_ids("plate"))
    sizes, off = restrict("lego", "plate", {"4x8", "gear_24"})
    assert (4, 8) not in sizes and (8, 8) in sizes and (1, 1) in sizes
    assert "gear_24" in off and "curve_2x1" in off and "gear_16" not in off and "4x8" not in off
    assert restrict("standard", "plate", {"1x1"})[0][0] == (1, 1)            # 1x1 always: it fills any cell
    assert parse_parts("gear_24, tile_4x2") == ("gear_24", "tile_4x2") and parse_parts("none") == ()
    assert "trademarks of the LEGO Group" in NOTICE


def test_axles_without_the_connector_or_some_lengths():
    assert axles_for(14) == ["axle_12", "axle_joiner_2", "axle_2"] or "axle_joiner_2" in axles_for(14)
    assert axles_for(14, off={"axle_joiner_2"}) == ["axle_16"]
    assert axles_for(40, off={"axle_joiner_2"}) == []                        # nothing spans it alone
    assert axles_for(8, off={"axle_8"}) != ["axle_8"] and axles_for(8, off={"axle_8"})
    assert axles_for(5, off={f"axle_{n}" for n in (2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 16, 32)}) == []


def test_stack_parts_and_tiles_leave_out_what_is_off():
    prof = np.c_[np.arange(1, 64, 2.0), np.full(32, 12.0), np.zeros(32)]
    assert {k for k, _o, _c in stack_parts(prof, ["grey"] * 32, False)} == {"rim_30x20"}
    other = {k for k, _o, _c in stack_parts(prof, ["grey"] * 32, False, off={"rim_30x20"})}
    assert other and "rim_30x20" not in other
    assert tile_cover(2, 4) == []                                            # a tile 2x4 of its own
    cover = tile_cover(2, 4, off={"tile_4x2"})
    assert cover and sum(w * d for _a, _b, w, d in cover) == 8


def test_the_command_line_takes_a_range_and_parts_off(tmp_path):
    from lego_engine.cli import main
    import trimesh
    path = tmp_path / "block.stl"
    trimesh.creation.box(bounds=[[0, 0, 0], [64, 32, 9.6]]).export(path)
    out = tmp_path / "out"
    assert main([str(path), "--scale", "1", "--unit", "plate", "--range", "bluebrixx", "--parts-off", "2x8",
                 "--stl", "none", "--out", str(out), "--quiet"]) == 0
    s = json.loads((out / "result.json").read_text())["settings"]
    assert s["parts_range"] == "bluebrixx" and [2, 8] not in s["sizes"] and [8, 8] not in s["sizes"]
    assert "gear_24" in s["parts_off"]


def test_the_notice_carries_the_trademark_symbols_also_in_latex():
    from lego_engine.export_manual import tex_escape
    assert "LEGO®" in NOTICE and "Technic™" in NOTICE and "BlueBrixx™" in NOTICE
    tex = tex_escape(NOTICE)
    assert r"\textregistered{}" in tex and r"\texttrademark{}" in tex and "®" not in tex
