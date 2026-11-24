"""Details: tiles, rods, bars on clips (lego_engine.details)."""
import numpy as np
import pytest
import trimesh

from lego_engine import LegoSettings, Legolizer
from lego_engine.analysis import connectivity
from lego_engine.details import DETAILS, apply, bar_pieces, parse_details
from lego_engine.geometry import manifold_mesh, piece_print_solid, piece_solid
from lego_engine.layout import check_cover, layout_connected
from lego_engine.shapes import BAR_LENGTHS, TILE_SIZES, TYPES, cell_props
from lego_engine.system import DEFAULT_SIZES, BrickSystem


def test_parse_details():
    assert parse_details("all") == DETAILS
    assert parse_details("bars, tiles") == ("tiles", "bars")
    assert parse_details("none") == ()
    with pytest.raises(ValueError):
        parse_details("rods, gears")


@pytest.mark.parametrize("key", [f"tile_{L}x{W}" for L, W in TILE_SIZES] + [f"bar_{L}" for L in BAR_LENGTHS])
def test_detail_solids_and_studs(key):
    """Every tile and bar is one closed solid in both turns; a tile has seats and no studs, a bar a stud and a seat
    at each end only."""
    t = TYPES[key]
    for rot in t.rotations:
        v, f = manifold_mesh(piece_solid(key, rot, 8.0, 3.2, 0.1))
        m = trimesh.Trimesh(v, f)
        assert m.is_watertight and m.volume > 0
        w, d = t.footprint(rot)
        lo, hi = m.bounds
        assert np.allclose(hi[:2] - lo[:2], [w * 8 - 0.2, d * 8 - 0.2], atol=0.05)
        studs, seats = cell_props(key, rot)
        if t.form == "tile":
            assert not studs.any() and seats.all()
        else:
            ends = np.zeros((w, d), bool)
            ends.flat[0] = ends.flat[-1] = True
            assert (studs == ends).all() and (seats == ends).all()
    assert piece_print_solid(key, 3.2, BrickSystem()).volume() > 0


def _posts_and_line(L=5):
    """Two posts three plates high and a free run of L cells from the top of one to the top of the other."""
    occ = np.zeros((L, 3, 3), bool)
    occ[0, 1, :] = occ[L - 1, 1, :] = True
    occ[:, 1, 2] = True
    return occ


def test_a_bar_spans_two_posts_and_holds():
    occ = _posts_and_line(5)
    pieces = bar_pieces(occ)
    assert [p[0].kind for p in pieces] == ["bar_5"]
    b = pieces[0][0]
    assert (b.layer, b.i, b.j, b.w, b.d) == (2, 0, 1, 5, 1)
    model = layout_connected(occ, DEFAULT_SIZES, pieces=pieces)
    assert not check_cover(model, occ)
    con = connectivity(model)
    assert con["groups"] == 1 and not con["floating"]


def test_no_bar_where_the_run_is_not_free_or_not_held():
    occ = _posts_and_line(5)
    occ[2, 0, 2] = True                        # something beside the middle: a wall, not a rod
    assert bar_pieces(occ) == []
    occ = _posts_and_line(5)
    occ[4, 1, :2] = False                      # one end holds on nothing
    assert bar_pieces(occ) == []
    assert bar_pieces(_posts_and_line(6)) == []   # no bar is 6 cells long (3L, 4L, 6L: 4, 5, 7 cells)


def test_tiles_on_tops_and_rods_on_free_columns():
    occ = np.zeros((6, 4, 4), bool)
    occ[0:4, 0:2, 0:3] = True                  # a block three plates high
    occ[5, 3, :] = True                        # a free 1x1 column four high
    model = layout_connected(occ, DEFAULT_SIZES)
    new, counts = apply(model, ("tiles", "rods"), "plate")
    kinds = {(b.layer, b.i, b.j): b.kind for b in new.bricks}
    assert counts["rods"] == 4 and all(kinds[(k, 5, 3)] == "round_1" for k in range(4))
    top = [b for b in new.bricks if b.layer == 2 and b.i < 4]
    assert top and all(b.kind.startswith("tile_") for b in top)
    assert not new.studs[0:4, 0:2, 2].any() and new.studs[0:4, 0:2, 1].all()
    assert counts["tiles"] == len(top)
    before, after = connectivity(model), connectivity(new)
    assert (before["groups"], len(before["floating"])) == (after["groups"], len(after["floating"]))
    _, counts = apply(model, ("tiles",), "brick")
    assert counts["tiles"] == 0                # a tile is a plate: not on brick layers


def test_pipeline_with_details(tmp_path):
    box = trimesh.creation.box(extents=(48.0, 24.0, 12.8))
    box.apply_translation((24.0, 12.0, 6.4))
    path = tmp_path / "box.stl"
    box.export(path)
    r = Legolizer().run(LegoSettings(input=str(path), size_mode="scale", size=1.0, unit="plate",
                                     details=("tiles", "rods", "bars")))
    assert r.stats["details"]["tiles"] > 0
    assert all(b.kind.startswith("tile_") for b in r.model.bricks if b.layer == max(x.layer for x in r.model.bricks))
    assert r.stats["stable"]
    assert LegoSettings.from_dict(r.settings.to_dict()).details == ("tiles", "rods", "bars")


def test_large_top_plates_are_covered_with_tiles_where_the_model_holds():
    from lego_engine.details import tile_cover
    for w, d in ((2, 8), (8, 2), (4, 6), (6, 12), (2, 5), (3, 3)):
        cover = tile_cover(w, d)
        cells = np.zeros((w, d), int)
        for a, c, tw, td in cover:
            cells[a:a + tw, c:c + td] += 1
        assert (cells == 1).all(), (w, d)
    assert tile_cover(2, 4) == []                    # a 2x4 tile exists: no split
    occ = np.zeros((8, 2, 3), bool)
    occ[:, :, 0:2] = True                            # two layers, the top one an 8-long plate across a joint
    occ[:, :, 2] = True
    model = layout_connected(occ, ((1, 1), (1, 2), (2, 4), (2, 8)))
    new, counts = apply(model, ("tiles",), "plate")
    top = [b for b in new.bricks if b.layer == 2]
    assert all(b.kind.startswith("tile_") for b in top) and sum(b.w * b.d for b in top) == 16
    assert not check_cover(new, occ)
    before, after = connectivity(model), connectivity(new)
    assert after["groups"] <= before["groups"] and len(after["floating"]) <= len(before["floating"])
