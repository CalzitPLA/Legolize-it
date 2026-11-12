"""Brick geometry, two kinds:

- display meshes (`bricks_display`, `voxel_mesh`): plain numpy triangles built for all bricks at once - a box per brick
  and a cylinder per visible stud, no booleans - so the window draws thousands of bricks instantly;
- solids (`brick_solid`, `layer_slab`): closed, printable manifolds (manifold3d) for the STL export - a brick with its
  studs and, optionally, the hollow underside with tubes and posts; a layer slab with studs on top and sockets below;
- shaped pieces (`piece_solid`, from lego_engine.shapes.outline) and special pieces (`special_solid`: the input cut to
  the piece's cells - exact for a closed input, else built from the fine samples); `model_slab` fuses a layer of a
  model with pieces; `stacking_check` stacks parts and measures their overlap and the stud play.

Lengths in mm. A brick's body is inset by `clearance` on every side, so neighbouring bricks do not touch; studs sit on
the cell centres.
"""
from __future__ import annotations

from functools import lru_cache
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
    b = np.array([x.to_list()[:5] for x in bricks], float).reshape(-1, 5)    # layer, i, j, w, d
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
    base = 0

    def add(v, f, who):
        nonlocal base
        verts.append(v)
        faces.append(f + base)
        owner.append(np.full(len(f), who, np.int64) if np.ndim(who) == 0 else who)
        base += len(v)

    p, h = grid.cell[0], grid.cell[2]
    if bodies:
        plain = [(n, b) for n, b in zip(idx, bricks) if b.is_brick]
        if plain:
            lo, hi = brick_boxes([b for _, b in plain], grid, system)
            bv, bf = box_mesh(lo, hi)
            add(bv, bf, np.repeat(np.asarray([n for n, _ in plain]), 12))
        for n, b in zip(idx, bricks):
            if b.is_special:
                mesh = model.special_meshes.get(n)
                if mesh is None:                                   # not cut yet: its cells as a box
                    lo, hi = brick_boxes([b], grid, system)
                    mesh = box_mesh(lo, hi)
                if len(mesh[1]):
                    add(np.asarray(mesh[0], float), np.asarray(mesh[1], np.int64), n)
            elif b.kind:
                tv, tf = piece_template(b.kind, b.rot, float(p), float(h), float(system.clearance))
                add(tv + grid.origin + np.array([b.i * p, b.j * p, b.layer * h]), tf, n)
    occ = model.occupancy
    has_stud = model.studs
    nz = occ.shape[2]
    centres, z0, stud_owner = [], [], []
    for n, b in zip(idx, bricks):
        for a in range(b.i, b.i + b.w):
            for q in range(b.j, b.j + b.d):
                if not has_stud[a, q, b.layer]:
                    continue
                covered = b.layer + 1 < nz and bool(occ[a, q, b.layer + 1])
                if b.is_special and not covered and studs != "all":
                    continue                                       # a special piece has studs only under bricks
                if studs == "all" or (studs == "visible" and not covered) or (studs == "covered" and covered):
                    centres.append((grid.origin[0] + (a + 0.5) * p, grid.origin[1] + (q + 0.5) * p))
                    z0.append(grid.origin[2] + (b.layer + 1) * h)
                    stud_owner.append(n)
    if centres:
        sv, sf = cylinder_mesh(np.array(centres), np.array(z0), system.stud_diameter / 2, system.stud_height, segments)
        add(sv, sf, np.repeat(np.asarray(stud_owner), len(sf) // len(centres)))
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
    wall = system.wall_thickness
    inner = (w * p - 2 * c - 2 * wall, d * p - 2 * c - 2 * wall, height - system.top)
    if hollow and min(inner) > 0.2:
        body = body - mf.Manifold.cube((inner[0], inner[1], inner[2] + 1.0)).translate((c + wall, c + wall, -1.0))
        supports = []
        ch = height - system.top + 0.01
        if w >= 2 and d >= 2:
            for a in range(1, w):
                for b in range(1, d):
                    supports.append(_cyl(system.tube_diameter / 2, ch, a * p, b * p, 0, seg)
                                    - _cyl(system.tube_inner / 2, ch + 2, a * p, b * p, -1, seg))
        elif max(w, d) >= 2:
            for a in range(1, max(w, d)):
                x, y = (a * p, p / 2) if w >= d else (p / 2, a * p)
                supports.append(_cyl(system.post_d / 2, ch, x, y, 0, seg))
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
        sockets = [_cyl(system.socket_diameter / 2, system.socket_depth + 1.0,
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


# ------------------------------------------------------------------------------------------- shaped pieces


def _outline_solid(o: Dict):
    """manifold3d.Manifold of a lego_engine.shapes.outline."""
    import manifold3d as mf
    if o["op"] == "cylinder":
        cx, cy = o["centre"]
        return mf.Manifold.cylinder(o["height"], o["radius"], circular_segments=48).translate((cx, cy, 0))
    cs = mf.CrossSection([[tuple(map(float, q)) for q in o["poly"]]])
    if o["op"] == "extrude_z":
        return mf.Manifold.extrude(cs, o["height"])
    if o["op"] == "extrude_y":                               # profile in (x, z), extruded along y
        return mf.Manifold.extrude(cs, o["length"]).transform([[1, 0, 0, 0], [0, 0, 1, o["y0"]], [0, 1, 0, 0]])
    if o["op"] == "revolve":
        cx, cy = o["centre"]
        return mf.Manifold.revolve(cs, circular_segments=96, revolve_degrees=90.0).translate((cx, cy, 0))
    raise ValueError(o["op"])


def piece_solid(kind: str, rot: int, pitch: float, height: float, clearance: float, system: Optional[BrickSystem] = None,
                studs: bool = True):
    """A shaped piece (manifold3d.Manifold) in its footprint's coordinates (the footprint from the origin, bottom at
    z = 0), turned by rot x 90 degrees; with studs on its stud cells (`system` for their size)."""
    from .shapes import outline, placement_offset, stud_cells_local
    system = system or BrickSystem()
    body = _outline_solid(outline(kind, pitch, height, clearance))
    if studs:
        cyl = [_cyl(system.stud_diameter / 2, system.stud_height + 0.01, (a + 0.5) * pitch, (b + 0.5) * pitch,
                    height - 0.01, system.segments) for a, b in stud_cells_local(kind)]
        body = _union([body] + cyl)
    ox, oy = placement_offset(kind, rot, pitch)
    return body.rotate((0.0, 0.0, 90.0 * (rot % 4))).translate((ox, oy, 0.0))


@lru_cache(maxsize=512)
def piece_template(kind: str, rot: int, pitch: float, height: float, clearance: float) -> Tuple[np.ndarray, np.ndarray]:
    """The display mesh of a shaped piece's body (no studs) in its footprint's coordinates - cached."""
    mesh = piece_solid(kind, rot, pitch, height, clearance, studs=False).to_mesh()
    return np.asarray(mesh.vert_properties)[:, :3].astype(float), np.asarray(mesh.tri_verts).astype(np.int64)


# ------------------------------------------------------------------------------------------- special pieces


def input_solid(vertices: np.ndarray, faces: np.ndarray):
    """The input as a manifold3d.Manifold (several closed bodies: their union), or None when it is not closed."""
    import manifold3d as mf

    def one(v, f):
        m = mf.Manifold(mf.Mesh(np.ascontiguousarray(v, np.float32), np.ascontiguousarray(f, np.uint32)))
        return m if m.status() == mf.Error.NoError and not m.is_empty() else None

    try:
        m = one(vertices, faces)
        if m is not None:
            return m
        import trimesh
        bodies = trimesh.Trimesh(vertices, faces, process=True).split(only_watertight=False)
        parts = [one(b.vertices, b.faces) for b in bodies]
        if not parts or any(x is None for x in parts):
            return None
        return _union(parts)
    except Exception:
        return None


def _piece_box(b: Brick, grid: Grid, inset: float):
    import manifold3d as mf
    p, h = grid.cell[0], grid.cell[2]
    lo = grid.origin + np.array([b.i * p + inset, b.j * p + inset, b.layer * h])
    size = np.array([b.w * p - 2 * inset, b.d * p - 2 * inset, h])
    return mf.Manifold.cube(tuple(map(float, size))).translate(tuple(map(float, lo)))


def special_solid(b: Brick, grid: Grid, solid=None, fine=None, inset: float = 0.0):
    """A special piece's body (manifold3d.Manifold, model coordinates): the input inside its cells (inset at the
    sides) - `solid` (input_solid) when the input is closed, else the fine samples' inside points as little blocks."""
    import manifold3d as mf
    box = _piece_box(b, grid, inset)
    if solid is not None:
        return solid ^ box
    if fine is None:
        return box
    s = fine.s
    step = grid.cell / s
    parts = []
    for a in range(b.i, b.i + b.w):
        for q in range(b.j, b.j + b.d):
            row = fine.index[a, q, b.layer]
            if row < 0:
                if fine.solid[a, q, b.layer]:
                    parts.append(_piece_box(Brick(b.layer, a, q, 1, 1), grid, 0.0))
                continue
            cube = fine.inside[row].reshape(s, s, s)
            for z in range(s):
                sq = [mf.CrossSection.square((step[0], step[1])).translate((x * step[0], y * step[1]))
                      for x, y in np.argwhere(cube[:, :, z])]
                if sq:
                    plan = mf.CrossSection.batch_boolean(sq, mf.OpType.Add) if len(sq) > 1 else sq[0]
                    corner = grid.origin + np.array([a * grid.cell[0], q * grid.cell[1], b.layer * grid.cell[2] + z * step[2]])
                    parts.append(mf.Manifold.extrude(plan, float(step[2])).translate(tuple(map(float, corner))))
    return _union(parts) ^ box


def manifold_mesh(solid) -> Tuple[np.ndarray, np.ndarray]:
    mesh = solid.to_mesh()
    return np.asarray(mesh.vert_properties)[:, :3].astype(float), np.asarray(mesh.tri_verts).astype(np.int64)


def mesh_solid(vertices: np.ndarray, faces: np.ndarray):
    import manifold3d as mf
    if not len(faces):
        return mf.Manifold()
    return mf.Manifold(mf.Mesh(np.ascontiguousarray(vertices, np.float32), np.ascontiguousarray(faces, np.uint32)))


def model_slab(model: BrickModel, k: int, grid: Grid, system: BrickSystem):
    """One printable slab for layer k of a model with shaped and special pieces (manifold3d.Manifold, grid
    coordinates, bottom at z = 0): the plain bricks' cells fused, the pieces' bodies (no side play, so they fuse with
    their neighbours), studs where the model has them (on a special piece only under something), sockets under every
    cell that sits on a stud of the layer below."""
    import manifold3d as mf
    p, h, seg = float(grid.cell[0]), float(grid.cell[2]), system.segments
    ids = model.ids[:, :, k]
    occ = ids >= 0
    parts = []
    plain = occ & np.isin(ids, [n for n, b in enumerate(model.bricks) if b.is_brick and b.layer == k])
    squares = [mf.CrossSection.square((n * p, m * p)).translate((i * p, j * p)) for i, j, n, m in _rectangles(plain)]
    if squares:
        plan = squares[0] if len(squares) == 1 else mf.CrossSection.batch_boolean(squares, mf.OpType.Add)
        parts.append(mf.Manifold.extrude(plan, h))
    for n in model.layer_bricks(k):
        b = model.bricks[n]
        if b.is_special:
            mesh = model.special_meshes.get(n)
            if mesh is not None and len(mesh[1]):
                parts.append(mesh_solid(mesh[0] - grid.origin - np.array([0, 0, k * h]), mesh[1]))
        elif b.kind:
            parts.append(piece_solid(b.kind, b.rot, p, h, 0.0, system, studs=False).translate((b.i * p, b.j * p, 0)))
    slab = _union(parts)
    studs = model.studs[:, :, k].copy()
    nz = model.shape[2]
    covered = model.occupancy[:, :, k + 1] if k + 1 < nz else np.zeros_like(occ)
    special = np.isin(ids, [n for n, b in enumerate(model.bricks) if b.is_special])
    studs &= ~special | covered
    if k > 0:
        sit = occ & model.studs[:, :, k - 1]           # every stud below gets a socket, seat or not (no collisions)
        sockets = [_cyl(system.socket_diameter / 2, system.socket_depth + 1.0,
                        (a + 0.5) * p, (b + 0.5) * p, -1.0, seg) for a, b in np.argwhere(sit)]
        if sockets:
            slab = slab - _union(sockets)
    cyl = [_cyl(system.stud_diameter / 2, system.stud_height + 0.01, (a + 0.5) * p, (b + 0.5) * p, h - 0.01, seg)
           for a, b in np.argwhere(studs)]
    return _union([slab] + cyl) if cyl else slab


# ------------------------------------------------------------------------------------------- printable pieces


def _sockets(cells, pitch: float, system: BrickSystem):
    """Socket cylinders (a stud's diameter + 2 x play, the stud's height + 0.2 deep) under the given (a, b) cells."""
    return [_cyl(system.socket_diameter / 2, system.socket_depth + 1.0, (a + 0.5) * pitch, (b + 0.5) * pitch, -1.0,
                 system.segments) for a, b in cells]


def piece_print_solid(kind: str, height: float, system: BrickSystem, seat_min: float = 0.0):
    """A shaped piece as printed or modelled on its own (turn 0, footprint from the origin): its body inset by the side
    clearance, studs on its stud cells and a socket under every cell - also where it does not sit on the stud (a round
    corner's outer cell is partly material: without the socket the stud below runs into it)."""
    from .shapes import TYPES
    t = TYPES[kind]
    body = piece_solid(kind, 0, system.pitch, height, system.clearance, system, studs=True)
    return body - _union(_sockets([(a, b) for a in range(t.L) for b in range(t.W)], system.pitch, system))


def special_print_solid(model: BrickModel, n: int, grid: Grid, system: BrickSystem):
    """A special piece as printed or modelled on its own, in model coordinates: the input inside its cells (inset by
    the side clearance), studs where something sits on it, sockets where it sits on a stud."""
    b = model.bricks[n]
    p, h = float(grid.cell[0]), float(grid.cell[2])
    v, f = model.special_meshes[n]
    body = mesh_solid(v, f) ^ _piece_box(b, grid, system.clearance)
    k, nz = b.layer, model.shape[2]
    fp = (slice(b.i, b.i + b.w), slice(b.j, b.j + b.d))
    covered = model.occupancy[fp + (k + 1,)] if k + 1 < nz else np.zeros((b.w, b.d), bool)
    studs = np.argwhere(model.studs[fp + (k,)] & covered)
    sit = np.argwhere(model.studs[fp + (k - 1,)]) if k > 0 else np.zeros((0, 2), int)     # every stud below
    corner = grid.origin + np.array([b.i * p, b.j * p, k * h])
    if len(sit):
        body = body - _union(_sockets(sit, p, system)).translate(tuple(map(float, corner)))
    if len(studs):
        body = _union([body] + [_cyl(system.stud_diameter / 2, system.stud_height + 0.01, (a + 0.5) * p, (q + 0.5) * p,
                                     h - 0.01, system.segments).translate(tuple(map(float, corner))) for a, q in studs])
    return body


def stacking_check(system: BrickSystem, height: float, kinds=()) -> List[Dict]:
    """Stack printable parts and measure (manifold3d): a 2x2 on a 2x2, 1x2 on 1x2, 1x1 on 1x1 (hollow undersides), and
    every shaped piece of `kinds` on a 2x2 brick. Per pair: the overlap volume (mm^3, should be 0) and the narrowest
    gap between the lower part's studs and the upper part (should be the stud play)."""
    p = system.pitch
    out = []

    def pair(name, lower, upper, studs_cells):
        upper = upper.translate((0.0, 0.0, height))
        studs = _union([_cyl(system.stud_diameter / 2, system.stud_height, (a + 0.5) * p, (b + 0.5) * p, height,
                             system.segments) for a, b in studs_cells])
        overlap = (lower ^ upper).volume()
        gap = studs.min_gap(upper, 2.0)
        out.append({"pair": name, "overlap_mm3": round(float(overlap), 4), "stud_gap_mm": round(float(gap), 4)})

    for w, d in ((2, 2), (1, 2), (1, 1)):
        brick = brick_solid(w, d, height, system, hollow=True)
        pair(f"{w}x{d} on {w}x{d}", brick, brick, [(a, b) for a in range(w) for b in range(d)])
    base = brick_solid(2, 2, height, system, hollow=True)
    from .shapes import TYPES, cell_props
    for kind in kinds:
        t = TYPES[kind]
        if t.L > 2 or t.W > 2:
            continue
        piece = piece_print_solid(kind, height, system)
        _studs, seats = cell_props(kind, 0)
        if seats.any():
            pair(f"{t.label} on 2x2", base, piece, [(int(a), int(b)) for a, b in np.argwhere(seats)])
    return out
