"""Brick geometry, two kinds:

- display meshes (`bricks_display`, `voxel_mesh`): plain numpy triangles built for all bricks at once - a box per brick
  and a cylinder per visible stud, no booleans - so the window draws thousands of bricks instantly;
- solids (`brick_solid`, `layer_slab`): closed, printable manifolds (manifold3d) for the STL export - a brick with its
  studs and, optionally, the hollow underside with tubes and posts; a layer slab with studs on top and sockets below.

Lengths in mm. A brick's body is inset by `clearance` on every side, so neighbouring bricks do not touch; studs sit on
the cell centres.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import numpy as np

from .layout import Brick, BrickModel
from .system import BrickSystem
from .voxelize import Grid

_BOX_FACES = np.array([[0, 2, 1], [1, 2, 3], [4, 5, 6], [5, 7, 6], [0, 1, 4], [1, 5, 4],
                       [2, 6, 3], [3, 6, 7], [0, 4, 2], [2, 4, 6], [1, 3, 5], [3, 7, 5]], np.int64)


def box_mesh(lo: np.ndarray, hi: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    """N boxes from their corners (N, 3) -> vertices (8N, 3), triangles (12N, 3), outward facing."""
    lo, hi = np.atleast_2d(lo), np.atleast_2d(hi)
    sel = np.array([[(c >> 0) & 1, (c >> 1) & 1, (c >> 2) & 1] for c in range(8)], bool)   # corner c: x, y, z bits
    v = np.where(sel[None, :, :], hi[:, None, :], lo[:, None, :]).reshape(-1, 3)
    f = (_BOX_FACES[None, :, :] + 8 * np.arange(len(lo))[:, None, None]).reshape(-1, 3)
    return v, f


def cylinder_mesh(centres: np.ndarray, z0: np.ndarray, radius: float, height: float,
                  segments: int = 16) -> Tuple[np.ndarray, np.ndarray]:
    """M vertical closed cylinders, bottom centre (x, y) = centres[m], from z0[m] up by `height`."""
    centres = np.atleast_2d(np.asarray(centres, float))
    z0 = np.asarray(z0, float).reshape(-1)
    m, s = len(centres), int(segments)
    a = 2 * np.pi * np.arange(s) / s
    ring = np.column_stack([np.cos(a), np.sin(a)]) * radius                   # (s, 2)
    # template: s bottom ring, s top ring, bottom centre, top centre
    tv = np.zeros((2 * s + 2, 3))
    tv[:s, :2], tv[s:2 * s, :2] = ring, ring
    tv[s:2 * s, 2] = height
    tv[2 * s + 1, 2] = height
    tf = []
    for q in range(s):
        r = (q + 1) % s
        tf += [[q, r, s + q], [r, s + r, s + q],                              # side
               [2 * s, r, q], [2 * s + 1, s + q, s + r]]                      # bottom (down), top (up)
    tf = np.array(tf, np.int64)
    v = tv[None, :, :] + np.column_stack([centres, z0])[:, None, :]
    f = tf[None, :, :] + (2 * s + 2) * np.arange(m)[:, None, None]
    return v.reshape(-1, 3), f.reshape(-1, 3)


def brick_boxes(bricks: List[Brick], grid: Grid, system: BrickSystem) -> Tuple[np.ndarray, np.ndarray]:
    """(lo, hi) of the bodies of `bricks` (N, 3)."""
    b = np.array([x.to_list() for x in bricks], float).reshape(-1, 5)        # layer, i, j, w, d
    p, h, c = grid.cell[0], grid.cell[2], system.clearance
    lo = grid.origin + np.column_stack([b[:, 1] * p + c, b[:, 2] * p + c, b[:, 0] * h])
    hi = grid.origin + np.column_stack([(b[:, 1] + b[:, 3]) * p - c, (b[:, 2] + b[:, 4]) * p - c, (b[:, 0] + 1) * h])
    return lo, hi


STUDS = ("visible", "covered", "all", "none")


def bricks_display(model: BrickModel, grid: Grid, system: BrickSystem, indices: Optional[List[int]] = None,
                   studs: str = "visible", bodies: bool = True,
                   segments: int = 16) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """One mesh of the bricks `indices` (default: all): vertices, triangles and the brick index of every triangle.
    `studs`: "visible" - where nothing is on top; "covered" - only those under a brick of the layer above (drawn when
    the view stops at this layer); "all"; "none". `bodies` False: studs only."""
    if studs not in STUDS:
        raise ValueError(f"studs must be one of {STUDS}")
    idx = list(range(len(model.bricks))) if indices is None else list(indices)
    empty = np.zeros((0, 3)), np.zeros((0, 3), np.int64), np.zeros(0, np.int64)
    if not idx:
        return empty
    bricks = [model.bricks[n] for n in idx]
    verts, faces, owner = [], [], []
    if bodies:
        lo, hi = brick_boxes(bricks, grid, system)
        bv, bf = box_mesh(lo, hi)
        verts, faces, owner = [bv], [bf], [np.repeat(np.asarray(idx), 12)]
    occ = model.occupancy
    nz = occ.shape[2]
    centres, z0, stud_owner = [], [], []
    p, h = grid.cell[0], grid.cell[2]
    for n, b in zip(idx, bricks):
        for a in range(b.i, b.i + b.w):
            for q in range(b.j, b.j + b.d):
                covered = b.layer + 1 < nz and bool(occ[a, q, b.layer + 1])
                if studs == "all" or (studs == "visible" and not covered) or (studs == "covered" and covered):
                    centres.append((grid.origin[0] + (a + 0.5) * p, grid.origin[1] + (q + 0.5) * p))
                    z0.append(grid.origin[2] + (b.layer + 1) * h)
                    stud_owner.append(n)
    if centres:
        sv, sf = cylinder_mesh(np.array(centres), np.array(z0), system.stud_diameter / 2, system.stud_height, segments)
        base = sum(len(v) for v in verts)
        verts.append(sv)
        faces.append(sf + base)
        owner.append(np.repeat(np.asarray(stud_owner), len(sf) // len(centres)))
    if not verts:
        return empty
    return np.vstack(verts), np.vstack(faces), np.concatenate(owner)


def voxel_mesh(occ: np.ndarray, grid: Grid, shrink: float = 0.0) -> Tuple[np.ndarray, np.ndarray]:
    """A box per occupied cell (the raw voxelization), shrunk by `shrink` mm on every side."""
    cells = np.argwhere(occ)
    if len(cells) == 0:
        return np.zeros((0, 3)), np.zeros((0, 3), np.int64)
    lo = grid.origin + cells * grid.cell + shrink
    return box_mesh(lo, lo + grid.cell - 2 * shrink)


# ------------------------------------------------------------------------------------------------- solids


def _cyl(radius: float, height: float, x: float, y: float, z: float, segments: int):
    import manifold3d as mf
    return mf.Manifold.cylinder(height, radius, circular_segments=segments).translate((x, y, z))


def _union(parts):
    import manifold3d as mf
    parts = [p for p in parts if p is not None]
    if not parts:
        return mf.Manifold()
    return parts[0] if len(parts) == 1 else mf.Manifold.batch_boolean(parts, mf.OpType.Add)


def brick_solid(w: int, d: int, height: float, system: BrickSystem, hollow: bool = True, studs: bool = True):
    """A printable brick (manifold3d.Manifold), w x d studs, in local coordinates: the cell block starts at the origin,
    the body is inset by the clearance, the bottom is z = 0 and the studs point up. `hollow`: the open underside with
    walls, tubes (w and d >= 2) or posts (one-wide bricks); a body too low for a cavity stays solid."""
    import manifold3d as mf
    p, c, seg = system.pitch, system.clearance, system.segments
    body = mf.Manifold.cube((w * p - 2 * c, d * p - 2 * c, height)).translate((c, c, 0))
    inner = (w * p - 2 * c - 2 * system.wall, d * p - 2 * c - 2 * system.wall, height - system.top)
    if hollow and min(inner) > 0.2:
        body = body - mf.Manifold.cube((inner[0], inner[1], inner[2] + 1.0)).translate((c + system.wall, c + system.wall, -1.0))
        supports = []
        ch = height - system.top + 0.01
        if w >= 2 and d >= 2:
            for a in range(1, w):
                for b in range(1, d):
                    supports.append(_cyl(system.tube_outer / 2, ch, a * p, b * p, 0, seg)
                                    - _cyl(system.tube_inner / 2, ch + 2, a * p, b * p, -1, seg))
        elif max(w, d) >= 2:
            for a in range(1, max(w, d)):
                x, y = (a * p, p / 2) if w >= d else (p / 2, a * p)
                supports.append(_cyl(system.post_diameter / 2, ch, x, y, 0, seg))
        body = _union([body] + supports)
    if studs:
        body = _union([body] + [_cyl(system.stud_diameter / 2, system.stud_height + 0.01, (a + 0.5) * p, (b + 0.5) * p,
                                     height - 0.01, seg) for a in range(w) for b in range(d)])
    return body


def _rectangles(mask: np.ndarray) -> List[Tuple[int, int, int, int]]:
    """Runs of True along x per row: (i, j, length, 1) - the cells of `mask` as few rectangles."""
    out = []
    for j in range(mask.shape[1]):
        col = mask[:, j]
        i = 0
        while i < len(col):
            if col[i]:
                s = i
                while i < len(col) and col[i]:
                    i += 1
                out.append((s, j, i - s, 1))
            else:
                i += 1
    return out


def layer_slab(cells: np.ndarray, below: Optional[np.ndarray], height: float, system: BrickSystem,
               studs: bool = True):
    """One printable slab for a layer (manifold3d.Manifold) in grid coordinates with its bottom at z = 0: the layer's
    cells fused, a stud on every cell and a socket (a stud's diameter plus twice `socket_clearance`, the stud's height
    plus 0.2 deep) under every cell that sits on a cell of the layer `below`. Printed slabs plug into each other."""
    import manifold3d as mf
    p, seg = system.pitch, system.segments
    squares = [mf.CrossSection.square((n * p, m * p)).translate((i * p, j * p)) for i, j, n, m in _rectangles(cells)]
    if not squares:
        return mf.Manifold()
    plan = squares[0] if len(squares) == 1 else mf.CrossSection.batch_boolean(squares, mf.OpType.Add)
    slab = mf.Manifold.extrude(plan, height)
    if below is not None:
        sockets = [_cyl(system.stud_diameter / 2 + system.socket_clearance, system.stud_height + 0.2 + 1.0,
                        (a + 0.5) * p, (b + 0.5) * p, -1.0, seg) for a, b in np.argwhere(cells & below)]
        if sockets:
            slab = slab - _union(sockets)
    if studs:
        slab = _union([slab] + [_cyl(system.stud_diameter / 2, system.stud_height + 0.01, (a + 0.5) * p, (b + 0.5) * p,
                                     height - 0.01, seg) for a, b in np.argwhere(cells)])
    return slab


def to_trimesh(solid):
    """manifold3d.Manifold -> trimesh.Trimesh."""
    import trimesh
    mesh = solid.to_mesh()
    return trimesh.Trimesh(np.asarray(mesh.vert_properties)[:, :3].astype(float),
                           np.asarray(mesh.tri_verts).astype(np.int64), process=False)
