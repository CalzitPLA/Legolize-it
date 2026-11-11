import math

import numpy as np
import pytest

from lego_engine.geometry import box_mesh, brick_solid, bricks_display, cylinder_mesh, layer_slab, to_trimesh
from lego_engine.layout import layout_bricks
from lego_engine.system import BrickSystem
from lego_engine.voxelize import Grid

SYS = BrickSystem()


def test_display_meshes_are_closed_and_outward():
    import trimesh
    v, f = box_mesh(np.array([[0, 0, 0], [5, 0, 0]]), np.array([[1, 2, 3], [6, 1, 1]]))
    for part in trimesh.Trimesh(v, f, process=True).split():
        assert part.is_watertight and part.volume > 0
    v, f = cylinder_mesh(np.array([[0.0, 0.0]]), np.array([0.0]), 2.0, 3.0, 24)
    cyl = trimesh.Trimesh(v, f, process=True)
    assert cyl.is_watertight and cyl.volume == pytest.approx(24 / 2 * math.sin(2 * math.pi / 24) * 4 * 3, rel=1e-6)


def test_solid_brick_volume():
    m = to_trimesh(brick_solid(2, 4, 9.6, SYS, hollow=False))
    body = (16 - 0.2) * (32 - 0.2) * 9.6
    studs = 8 * math.pi * 2.4 ** 2 * 1.7
    assert m.is_watertight and m.volume == pytest.approx(body + studs, rel=0.01)
    assert m.bounds[1][2] == pytest.approx(9.6 + 1.7, abs=1e-6)


@pytest.mark.parametrize("w,d,h", [(2, 4, 9.6), (1, 6, 9.6), (1, 1, 3.2), (2, 2, 3.2)])
def test_hollow_bricks_are_printable(w, d, h):
    solid = to_trimesh(brick_solid(w, d, h, SYS, hollow=False))
    hollow = to_trimesh(brick_solid(w, d, h, SYS, hollow=True))
    assert hollow.is_watertight and 0 < hollow.volume < solid.volume
    assert hollow.bounds[0][2] == pytest.approx(0.0)


def test_layer_slab_has_sockets_where_it_sits_on_the_layer_below():
    cells = np.ones((3, 2), bool)
    below = np.zeros((3, 2), bool)
    below[0, 0] = True
    plain = to_trimesh(layer_slab(cells, None, 3.2, SYS))
    socketed = to_trimesh(layer_slab(cells, below, 3.2, SYS))
    socket = math.pi * (2.4 + SYS.socket_clearance) ** 2 * (SYS.stud_height + 0.2)
    assert plain.is_watertight and socketed.is_watertight
    assert plain.volume - socketed.volume == pytest.approx(socket, rel=0.02)


def test_display_studs_only_where_nothing_covers_them():
    occ = np.zeros((2, 2, 2), bool)
    occ[:, :, 0] = True
    occ[0, 0, 1] = True
    model = layout_bricks(occ, sizes=((2, 2),))
    grid = Grid(np.zeros(3), np.array([8.0, 8.0, 9.6]), occ.shape)
    _, f_vis, own = bricks_display(model, grid, SYS, studs="visible", segments=8)
    _, f_cov, _ = bricks_display(model, grid, SYS, studs="covered", bodies=False, segments=8)
    per_stud = 4 * 8
    assert len(f_vis) == 12 * 2 + per_stud * 4         # 3 open studs below + 1 on top
    assert len(f_cov) == per_stud * 1
    assert set(own.tolist()) == {0, 1}
