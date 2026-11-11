"""Voxelization on the brick grid: a cell is pitch x pitch in plan and one layer (a brick or a plate) high.

1. `place()` turns the part so its "up" is +z, scales it (to a number of studs along its longest horizontal side, or by
   a factor; the part's units are taken as mm) and moves it into the grid: centred in x/y, its bottom on the grid's
   floor (z = 0), so a flat bottom fills the first layer exactly.
2. `fill_fraction()` measures how much of every cell is material: samples^3 points per cell (a regular sub-grid), each
   point inside the part when its generalized winding number is above 0.5 (libigl's fast winding number: robust for
   meshes with small holes or flipped triangles, where ray parity flips). The fraction is the share of inside points.
3. `occupancy()` thresholds the fraction (0.5 = a cell is a brick when at least half of it is material); `hollow()`
   keeps only a shell of the given thickness.

Arrays are indexed [i, j, k] = [x, y, z]: index i is along +x, j along +y, k is the layer (+z). The world position of
the centre of cell (i, j, k) is origin + (i + 0.5, j + 0.5, k + 0.5) * cell - see `Grid.centre`.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple

import numpy as np

from .mesh_io import up_matrix

SIZE_MODES = ("studs", "scale")


@dataclass
class Grid:
    origin: np.ndarray              # world position of the corner of cell (0, 0, 0) (model space, mm)
    cell: np.ndarray                # (pitch, pitch, layer height)
    shape: Tuple[int, int, int]     # (nx, ny, nz)

    def centre(self, i, j, k) -> np.ndarray:
        return self.origin + (np.array([i, j, k], float) + 0.5) * self.cell

    def corner(self, i, j, k) -> np.ndarray:
        return self.origin + np.array([i, j, k], float) * self.cell

    @property
    def size(self) -> np.ndarray:
        return np.asarray(self.shape, float) * self.cell

    def to_dict(self) -> dict:
        return {"origin": [float(x) for x in self.origin], "cell": [float(x) for x in self.cell],
                "shape": [int(x) for x in self.shape]}


@dataclass
class Placement:
    """input (mm) -> model space: x' = scale * R @ x + offset."""
    up: str
    scale: float
    offset: np.ndarray

    @property
    def rotation(self) -> np.ndarray:
        return up_matrix(self.up)

    @property
    def matrix(self) -> np.ndarray:
        m = np.eye(4)
        m[:3, :3] = self.scale * self.rotation
        m[:3, 3] = self.offset
        return m

    def apply(self, points: np.ndarray) -> np.ndarray:
        return self.scale * (np.asarray(points, float) @ self.rotation.T) + self.offset

    def to_dict(self) -> dict:
        return {"up": self.up, "scale": float(self.scale), "offset": [float(x) for x in self.offset],
                "matrix": self.matrix.tolist()}


def place(vertices: np.ndarray, cell: np.ndarray, size_mode: str = "studs", size: float = 16.0,
          up: str = "z") -> Tuple[Placement, Grid]:
    """The placement of the part in the grid and the grid itself (see the module docstring)."""
    cell = np.asarray(cell, float)
    turned = np.asarray(vertices, float) @ up_matrix(up).T
    lo, hi = turned.min(axis=0), turned.max(axis=0)
    ext = hi - lo
    if size_mode == "studs":
        horizontal = max(ext[0], ext[1])
        reference = horizontal if horizontal > 1e-12 else ext[2]
        if reference <= 1e-12 or size <= 0:
            raise ValueError("the part has no extent (or the size is not positive)")
        scale = float(size) * cell[0] / reference
    elif size_mode == "scale":
        if size <= 0:
            raise ValueError("the scale factor must be positive")
        scale = float(size)
    else:
        raise ValueError(f"size_mode must be one of {SIZE_MODES}, not {size_mode!r}")
    scaled = ext * scale
    n = np.maximum(1, np.ceil(scaled / cell - 1e-6)).astype(int)
    pad = (n * cell - scaled) / 2.0
    pad[2] = 0.0                                          # bottom on the floor of the grid
    offset = -lo * scale + pad
    return Placement(up, scale, offset), Grid(np.zeros(3), cell, tuple(int(x) for x in n))


def _winding(vertices: np.ndarray, faces: np.ndarray, points: np.ndarray) -> np.ndarray:
    try:
        import igl
        return igl.fast_winding_number(np.ascontiguousarray(vertices, np.float64),
                                       np.ascontiguousarray(faces, np.int64),
                                       np.ascontiguousarray(points, np.float64))
    except ImportError:                                   # slower and less robust, but no extra package
        import trimesh
        mesh = trimesh.Trimesh(vertices, faces, process=False)
        return mesh.contains(points).astype(float)


def fill_fraction(vertices: np.ndarray, faces: np.ndarray, grid: Grid, samples: int = 3,
                  max_points: int = 3_000_000) -> np.ndarray:
    """(nx, ny, nz) float32: the share of each cell inside the part (vertices already in model space)."""
    samples = max(1, int(samples))
    nx, ny, nz = grid.shape
    sub = (np.arange(samples) + 0.5) / samples
    su, sv, sw = np.meshgrid(sub, sub, sub, indexing="ij")
    sub3 = np.stack([su.ravel(), sv.ravel(), sw.ravel()], axis=1)          # (s^3, 3) in cell units
    ii, jj = np.meshgrid(np.arange(nx), np.arange(ny), indexing="ij")
    plan = np.stack([ii.ravel(), jj.ravel()], axis=1).astype(float)        # (nx*ny, 2)
    out = np.zeros((nx, ny, nz), np.float32)
    per_layer = nx * ny * len(sub3)
    layers_per_chunk = max(1, max_points // max(1, per_layer))
    for k0 in range(0, nz, layers_per_chunk):
        ks = np.arange(k0, min(nz, k0 + layers_per_chunk))
        cells = np.concatenate([np.column_stack([plan, np.full(len(plan), k, float)]) for k in ks])   # (c, 3)
        pts = (cells[:, None, :] + sub3[None, :, :]) * grid.cell + grid.origin
        w = _winding(vertices, faces, pts.reshape(-1, 3))
        frac = (w.reshape(len(cells), len(sub3)) > 0.5).mean(axis=1)
        out[:, :, ks] = frac.reshape(len(ks), nx, ny).transpose(1, 2, 0)
    return out


def surface_cells(vertices: np.ndarray, faces: np.ndarray, grid: Grid, spacing: float = 0.25,
                  max_points: int = 4_000_000, seed: int = 0) -> np.ndarray:
    """(nx, ny, nz) bool: the cells the surface passes through - for thin walls (sheet metal, the flanges of a beam),
    which fill too little of any cell to reach a fill threshold and fall between the fraction's sample points. The
    surface is sampled about every `spacing` x the smallest cell side (area-weighted, a fixed seed), plus every vertex
    and every triangle's centroid."""
    v = np.asarray(vertices, float)
    tri = v[np.asarray(faces, np.int64)]
    area = 0.5 * np.linalg.norm(np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0]), axis=1)
    step = spacing * float(np.min(grid.cell))
    n = int(min(max_points, max(len(tri), area.sum() / step ** 2)))
    rng = np.random.default_rng(seed)
    pick = rng.choice(len(tri), size=n, p=area / area.sum()) if area.sum() > 0 else np.zeros(0, int)
    a, b = rng.random(n), rng.random(n)
    flip = a + b > 1
    a[flip], b[flip] = 1 - a[flip], 1 - b[flip]
    t = tri[pick]
    pts = np.vstack([t[:, 0] + a[:, None] * (t[:, 1] - t[:, 0]) + b[:, None] * (t[:, 2] - t[:, 0]),
                     v, tri.mean(axis=1)])
    idx = np.floor((pts - grid.origin) / grid.cell).astype(int)
    idx = np.clip(idx, 0, np.asarray(grid.shape) - 1)
    out = np.zeros(grid.shape, bool)
    out[idx[:, 0], idx[:, 1], idx[:, 2]] = True
    return out


def occupancy(fraction: np.ndarray, threshold: float = 0.5) -> np.ndarray:
    """Cells with at least `threshold` of material. A threshold of 0 still needs some material in the cell."""
    if threshold <= 0:
        return fraction > 0
    return fraction >= threshold - 1e-9


def hollow(occ: np.ndarray, shell: int) -> np.ndarray:
    """Only the cells within `shell` cells (6-neighbour steps) of an empty cell or of the grid's border; 0 = solid."""
    if shell <= 0:
        return occ.copy()
    from scipy import ndimage
    interior = ndimage.binary_erosion(occ, structure=ndimage.generate_binary_structure(3, 1),
                                      iterations=int(shell), border_value=0)
    return occ & ~interior
