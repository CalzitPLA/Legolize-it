"""Shaped and special pieces, the stud fit, the colour patterns."""
import numpy as np
import pytest
import trimesh

from lego_engine import LegoSettings, Legolizer
from lego_engine.geometry import manifold_mesh, piece_solid, stacking_check, special_print_solid, to_trimesh
from lego_engine.layout import check_cover
from lego_engine.palette import FADES, PALETTE, brick_colours
from lego_engine.shapes import CATALOGUE, TYPES, cell_props, mask, parse_families
from lego_engine.system import BrickSystem

ALL = ("slope", "curve", "round", "dome", "inverted")


@pytest.mark.parametrize("key", ["slope_2x1", "curve_3x1", "quarter_2", "dome_2", "icurve_2x1", "islope_2x2"])
def test_the_fit_sees_the_shape_that_is_built(key):
    """The fit's sample masks and the solids drawn, printed and modelled are one shape, in every turn."""
    t = TYPES[key]
    s = 5
    q = (np.arange(s) + 0.5) / s
    for rot in t.rotations:
        w, d = t.footprint(rot)
        m = trimesh.Trimesh(*manifold_mesh(piece_solid(key, rot, 8.0, 9.6, 0.0, studs=False)))
        X, Y, Z = np.meshgrid((np.arange(w)[:, None] + q).ravel() * 8, (np.arange(d)[:, None] + q).ravel() * 8,
                              q * 9.6, indexing="ij")
        inside = m.contains(np.c_[X.ravel(), Y.ravel(), Z.ravel()]).reshape(X.shape)
        assert (inside == mask(key, rot, s)).mean() > 0.99, (key, rot)


def test_studs_and_seats():
    studs, seats = cell_props("slope_2x1", 0)
    assert studs.tolist() == [[True], [False]] and seats.all()           # a stud on the high cell only
    studs, seats = cell_props("curve_2x1", 0)
    assert not studs.any() and seats.all()                               # nothing sits on a curve
    studs, seats = cell_props("icurve_2x1", 0)
    assert studs.all() and not seats.any()                               # it hangs from above
    for t in CATALOGUE:                                                  # every piece can hold or be held somewhere
        st, se = cell_props(t.key, 0)
        assert st.any() or se.any(), t.key


def test_families_parse():
    assert parse_families("all") == ALL and parse_families("none") == ()
    assert parse_families("round, curve") == ("curve", "round")
    with pytest.raises(ValueError):
        parse_families("wedge")


def test_every_part_stacks_with_the_stud_play():
    """Stud, walls, tubes, posts, sockets: no overlap and the same play everywhere (2026-11-12: the walls left 0.3,
    the tubes 0; a round corner's outer cell ran into the stud below until every cell got a socket)."""
    system = BrickSystem(socket_clearance=0.12)
    fit = system.fit()
    assert all(abs(fit[k] - 0.12) < 1e-9 for k in ("gap_wall", "gap_tube", "gap_post", "gap_socket"))
    for row in stacking_check(system, 9.6, [t.key for t in CATALOGUE]):
        assert row["overlap_mm3"] < 0.01, row
        assert abs(row["stud_gap_mm"] - 0.12) < 0.005, row


def test_shaped_pieces_round_a_sphere(parts):
    lz = Legolizer()
    plain = lz.run(LegoSettings(input=str(parts["sphere"]), size=12))
    shaped = lz.run(LegoSettings(input=str(parts["sphere"]), size=12, shapes=ALL))
    assert shaped.stats["shaped"] > 20 and not check_cover(shaped.model, shaped.occupancy)
    assert shaped.stats["stable"]
    assert shaped.stats["deviation"] < 0.8 * plain.stats["deviation"]     # measured: 12.9 % -> 8.8 %
    kinds = {b.kind for b in shaped.model.bricks} - {""}
    assert any(not TYPES[k].inverted for k in kinds) and any(TYPES[k].inverted for k in kinds)   # top and underside
    # nothing sits on a cell without a stud
    m = shaped.model
    above = np.zeros_like(m.occupancy)
    above[:, :, :-1] = m.occupancy[:, :, 1:]
    assert not (above & m.occupancy & ~m.studs).any()


def test_special_pieces_carry_the_exact_shape(tmp_path):
    """A slim cone on a block: its tip is no brick shape; special pieces cut from the input carry it."""
    cone = trimesh.creation.cone(radius=5.0, height=30.0, sections=48)
    cone.apply_translation([20, 20, 19.2])
    block = trimesh.creation.box((40, 40, 19.2))
    block.apply_translation([20, 20, 9.6])
    part = trimesh.boolean.union([block, cone], engine="manifold")
    path = tmp_path / "cone.stl"
    part.export(path)
    r = Legolizer().run(LegoSettings(input=str(path), size_mode="scale", size=1.0, special=True,
                                     special_tolerance=0.15))
    specials = [n for n, b in enumerate(r.model.bricks) if b.is_special]
    assert specials and all(n in r.model.special_meshes for n in specials)
    for n in specials:
        solid = special_print_solid(r.model, n, r.grid, r.system)
        assert to_trimesh(solid).is_watertight and solid.volume() > 0
    assert not check_cover(r.model, r.occupancy)


def test_colour_patterns(parts):
    r = Legolizer().run(LegoSettings(input=str(parts["sphere"]), size=8))
    pal = {tuple(np.round(v, 4)) for v in PALETTE.values()}
    for mode in ("random", "rainbow", "fade", "layer", "mixed", "single"):
        cols = brick_colours(r.model, mode)
        assert cols.shape == (len(r.model.bricks), 3)
        assert all(tuple(np.round(c, 4)) in pal for c in cols), mode       # only colours bricks come in
    rain = brick_colours(r.model, "rainbow")
    bottom = [n for n, b in enumerate(r.model.bricks) if b.layer == 0]
    top = [n for n, b in enumerate(r.model.bricks) if b.layer == max(x.layer for x in r.model.bricks)]
    assert not np.allclose(rain[bottom[0]], rain[top[0]])
    fade = brick_colours(r.model, "fade", fade="ocean")
    assert np.allclose(fade[bottom[0]], PALETTE[FADES["ocean"][0]])
    assert r.colour_mode("auto") == "size"                                 # no colours in the input: by size


def test_auto_colours_follow_a_coloured_input(parts):
    r = Legolizer().run(LegoSettings(input=str(parts["two"]), size_mode="scale", size=1.0))   # colour_mode "auto"
    assert r.colour_mode() == "source"
    assert np.allclose(r.colours(), r.colours("source"))
