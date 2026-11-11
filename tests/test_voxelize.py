import numpy as np
import pytest

from lego_engine.mesh_io import UP_AXES, load_part, up_matrix
from lego_engine.voxelize import Grid, fill_fraction, hollow, occupancy, place, surface_cells

CELL = np.array([8.0, 8.0, 9.6])


def test_up_matrices_are_rotations_that_lift_their_axis():
    axis = {"z": (0, 0, 1), "-z": (0, 0, -1), "y": (0, 1, 0), "-y": (0, -1, 0), "x": (1, 0, 0), "-x": (-1, 0, 0)}
    for up in UP_AXES:
        r = up_matrix(up)
        assert np.allclose(r @ r.T, np.eye(3)) and np.isclose(np.linalg.det(r), 1.0)
        assert np.allclose(r @ np.array(axis[up], float), (0, 0, 1))


def test_place_scales_to_studs_and_puts_the_bottom_on_the_floor(parts):
    mesh = load_part(parts["L"])
    placement, grid = place(mesh.vertices, CELL, "studs", 8)
    assert placement.scale == pytest.approx(8 * 8.0 / 32)
    v = placement.apply(mesh.vertices)
    assert v[:, 2].min() == pytest.approx(0.0)
    assert grid.shape[0] == 8 and grid.shape[1] == 4
    assert np.allclose(placement.matrix[:3, :3], placement.scale * np.eye(3))


def test_fraction_of_a_grid_aligned_box_is_exact_and_the_axes_are_not_mixed(parts):
    """The L at scale 1 fills whole cells: the tower stands in the +x/+y corner column, nothing else above layer 0."""
    mesh = load_part(parts["L"])
    placement, grid = place(mesh.vertices, CELL, "scale", 1.0)
    assert grid.shape == (4, 2, 3)
    frac = fill_fraction(placement.apply(mesh.vertices), mesh.faces, grid, samples=3)
    occ = occupancy(frac)
    assert np.allclose(frac[occ], 1.0) and np.allclose(frac[~occ], 0.0)
    assert occ[:, :, 0].all()
    assert occ[3, 1, :].all()
    assert occ[:, :, 1:].sum() == 2


def test_a_y_up_part_is_turned_upright(parts):
    mesh = load_part(parts["L"])
    turned = mesh.copy()
    turned.vertices = mesh.vertices @ np.linalg.inv(up_matrix("y")).T      # what a y-up exporter writes
    placement, grid = place(turned.vertices, CELL, "scale", 1.0, up="y")
    occ = occupancy(fill_fraction(placement.apply(turned.vertices), turned.faces, grid))
    assert grid.shape == (4, 2, 3) and occ.sum() == 10 and occ[3, 1, :].all()


def test_sphere_volume_is_kept():
    import trimesh
    m = trimesh.creation.icosphere(4, radius=40)
    placement, grid = place(m.vertices, np.array([8.0, 8.0, 3.2]), "scale", 1.0)
    frac = fill_fraction(placement.apply(m.vertices), m.faces, grid, samples=4)
    assert frac.sum() * np.prod(grid.cell) == pytest.approx(m.volume, rel=0.03)


def test_hollow_keeps_a_shell():
    occ = np.ones((7, 7, 7), bool)
    shell = hollow(occ, 1)
    assert shell.sum() == 7 ** 3 - 5 ** 3
    assert not shell[1:-1, 1:-1, 1:-1].any()
    assert hollow(occ, 0).all()


def test_thin_walls_are_found_by_the_surface(parts):
    mesh = load_part(parts["plate"])
    placement, grid = place(mesh.vertices, np.array([8.0, 8.0, 3.2]), "scale", 1.0)
    v = placement.apply(mesh.vertices)
    assert fill_fraction(v, mesh.faces, grid).max() < 0.5             # 0.4 mm in 3.2 mm cells
    touched = surface_cells(v, mesh.faces, grid)
    assert grid.shape == (10, 8, 1) and touched.all()
