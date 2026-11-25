"""Technic parts and axle sub-assemblies (lego_engine.technic, lego_engine.assembly)."""
import numpy as np
import pytest
import trimesh

from lego_engine.analysis import connectivity, stats
from lego_engine.assembly import Assembly, axle_height, cells_mask, meshes, place
from lego_engine.layout import layout_connected
from lego_engine.system import DEFAULT_SIZES, BrickSystem
from lego_engine.technic import PARTS, axles_for, part_mesh
from lego_engine.voxelize import Grid


@pytest.mark.parametrize("key", sorted(PARTS))
def test_every_part_is_a_solid_of_its_size(key):
    v, f = part_mesh(key)
    m = trimesh.Trimesh(v, f)
    assert m.is_watertight and m.volume > 0, key
    t = PARTS[key]
    lo, hi = m.bounds
    if t.role != "support":
        assert hi[2] - lo[2] >= t.length - 1e-6 and hi[2] - lo[2] <= t.length + 2.0     # studs stand out
        assert max(hi[0] - lo[0], hi[1] - lo[1]) <= 2 * t.radius + 0.05


def test_axles_for_a_span():
    assert axles_for(8) == ["axle_8"]
    got = axles_for(14)
    assert sum(PARTS[k].studs_l for k in got if PARTS[k].role == "axle") == 14 and "axle_joiner_2" in got
    for n in range(2, 60):
        got = axles_for(n)
        assert sum(PARTS[k].studs_l for k in got if PARTS[k].role == "axle") >= n


def _stand(posts=True):
    """A base (one 2x8 brick), two posts on it at the ends, an axle along x held by two support bricks on the posts:
    bush, round brick 2x2, gear 24, round plate 2x2, bush between them."""
    grid = Grid(np.zeros(3), np.array([8.0, 8.0, 9.6]), (8, 3, 5))
    occ = np.zeros(grid.shape, bool)
    occ[:, 0:2, 0] = True                     # one 2x8 brick (a single layer side by side would not hold)
    if posts:
        occ[0, 1, 1] = occ[7, 1, 1] = True
    z = axle_height(2, grid)
    a = Assembly(axis=0, line=(12.0, z), start=0.0, axles=("axle_8",),
                 parts=(("bush", 8.0, "light_bluish_grey"), ("round_brick_2x2", 16.0, "red"),
                        ("gear_24", 28.0, "dark_bluish_grey"), ("round_plate_2x2", 34.0, "blue"),
                        ("bush", 48.0, "light_bluish_grey")),
                 supports=((0, 1, 2), (7, 1, 2)), support_layers=1)
    return grid, occ, place(a, grid)


def test_an_axle_stack_on_two_posts_holds():
    grid, occ, a = _stand()
    taken = cells_mask([a], grid.shape)
    assert taken[0, 1, 2] and taken[7, 1, 2] and taken[3, 1, 2]          # supports and the round brick
    assert not (taken & occ).any()                                       # it leaves the posts and the base alone
    model = layout_connected(occ & ~taken, DEFAULT_SIZES)
    model.assemblies = [a]
    con = connectivity(model)
    assert con["groups"] == 1 and not con["floating"] and not con["floating_assemblies"]
    st = stats(model, BrickSystem(), "brick")
    assert st["stable"] and st["assemblies"] == 1 and st["technic"]["support_1x1"] == 2 and st["technic"]["bush"] == 2
    assert len(meshes(a, grid)) == 1 + 5 + 2


def test_an_axle_stack_without_posts_floats():
    grid, occ, a = _stand(posts=False)
    model = layout_connected(occ & ~cells_mask([a], grid.shape), DEFAULT_SIZES)
    model.assemblies = [a]
    con = connectivity(model)
    assert con["floating_assemblies"] == [0] and con["groups"] == 2
    assert not stats(model, BrickSystem(), "brick")["stable"]


def test_assembly_round_trip():
    _grid, _occ, a = _stand()
    b = Assembly.from_dict(a.to_dict())
    assert b == a and b.mount_layer == 2 and b.length == 64.0
