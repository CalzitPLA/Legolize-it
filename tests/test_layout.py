import numpy as np
import pytest
from scipy import ndimage

from lego_engine.analysis import connectivity, stats
from lego_engine.layout import Brick, BrickModel, check_cover, layout_bricks
from lego_engine.system import DEFAULT_SIZES, BrickSystem, normalise_sizes, parse_sizes


def blob(seed, shape=(14, 11, 9)):
    rng = np.random.default_rng(seed)
    return ndimage.gaussian_filter(rng.random(shape), 1.5) > 0.5


@pytest.mark.parametrize("seed", range(6))
def test_cover_is_exact_on_random_shapes(seed):
    occ = blob(seed)
    model = layout_bricks(occ)
    assert check_cover(model, occ) == []
    assert (model.occupancy == occ).all()


def test_catalogue_and_longest_brick_are_respected():
    occ = np.ones((20, 20, 3), bool)
    model = layout_bricks(occ, sizes=((1, 2), (2, 4), (1, 8)), max_length=4)
    names = {b.name for b in model.bricks}
    assert names <= {"1x1", "1x2", "2x4"} and "2x4" in names


def test_layers_cross():
    """Even layers run along x, odd layers mostly along y (a brick along x may still win where it bridges a joint
    without ending on one). (Two layers of 2x4 on 8 x 8 cannot become one piece: no brick can straddle both x = 4
    and y = 4 - the wall tests below check the bond.)"""
    occ = np.ones((8, 8, 2), bool)
    model = layout_bricks(occ, sizes=((2, 4),))
    l0 = [b for b in model.bricks if b.layer == 0]
    l1 = [b for b in model.bricks if b.layer == 1]
    assert all(b.w == 4 and b.d == 2 for b in l0)
    assert sum(b.w == 2 and b.d == 4 for b in l1) >= 0.75 * len(l1)


@pytest.mark.parametrize("width", (1, 2))
def test_a_long_wall_is_bonded_into_one_piece(width):
    """Running bond: the joints of neighbouring layers do not line up, so a long wall holds as one piece."""
    occ = np.ones((24, width, 8), bool)
    model = layout_bricks(occ)
    con = connectivity(model)
    assert con["groups"] == 1 and con["floating"] == []


def test_connectivity_finds_a_floating_group():
    ids = np.full((4, 1, 3), -1, np.int32)
    bricks = [Brick(0, 0, 0, 2, 1), Brick(1, 0, 0, 2, 1), Brick(2, 3, 0, 1, 1)]
    for n, b in enumerate(bricks):
        ids[b.i:b.i + b.w, b.j:b.j + b.d, b.layer] = n
    con = connectivity(BrickModel(bricks, ids, DEFAULT_SIZES))
    assert con["groups"] == 2 and con["floating"] == [2]


def test_stats_counts_and_studs():
    occ = np.zeros((4, 2, 2), bool)
    occ[:, :, 0] = True
    occ[0:2, :, 1] = True
    model = layout_bricks(occ, sizes=((2, 4), (2, 2)))
    s = stats(model, BrickSystem(), "brick")
    assert s["bricks"] == 2 and s["counts"] == {"2x4": 1, "2x2": 1}
    assert s["studs_visible"] == 8 and s["stable"]
    assert s["size_mm"] == [32.0, 16.0, 19.2]


def test_sizes_are_parsed_and_normalised():
    assert parse_sizes("4x2, 1x1; 2x4") == ((2, 4), (1, 1))
    assert normalise_sizes(((4, 2),), max_length=3) == ((1, 1),)
    with pytest.raises(ValueError):
        parse_sizes("0x2")
