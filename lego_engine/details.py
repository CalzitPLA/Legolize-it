"""Details: parts that make a model look like a set of plastic bricks rather than a stack of them - laid by rule, not
fitted to the part's surface (lego_engine.shapes fits slopes and curves):

    tiles  plates with nothing on top become tiles of their size: smooth tops (plate layers only - a tile is a plate)
    rods   1x1 columns standing free (nothing beside them in their layers, at least ROD_MIN layers of 1x1 cells) become
           round 1x1 plates or bricks
    bars   straight runs standing free in a layer (nothing beside, above or below the middle cells) become a bar held by
           a clip plate at each end - bar 3L, 4L or 6L over 4, 5 or 7 cells (lego_engine.shapes.BAR_LENGTHS); laid
           before the bricks, as pieces with a stud and a seat at each end only

`bar_pieces(occ, free)` -> pieces for lego_engine.layout; `apply(model, details, unit)` -> (model with tiles and rods,
counts). Tiles and rods keep every brick's place: the cover, the colours and how the model holds stay as they were
(nothing sits on a tile; a round 1x1 has its stud and its seat).
"""
from __future__ import annotations

from typing import Dict, Iterable, List, Tuple

import numpy as np

from .layout import Brick, BrickModel
from .shapes import BAR_LENGTHS, TYPES, cell_props

DETAILS = ("tiles", "rods", "bars")
ROD_MIN = 3


def parse_details(text) -> Tuple[str, ...]:
    """'tiles, rods' / ['bars'] / 'all' / 'none' -> a tuple of DETAILS (in their order)."""
    if text is None:
        return ()
    items = text.replace(";", ",").split(",") if isinstance(text, str) else list(text)
    items = [str(x).strip().lower() for x in items if str(x).strip()]
    if items in (["none"], ["off"], []):
        return ()
    if "all" in items:
        return DETAILS
    bad = [x for x in items if x not in DETAILS]
    if bad:
        raise ValueError(f"unknown details {bad}: choose from {', '.join(DETAILS)}, all or none")
    return tuple(d for d in DETAILS if d in items)


def _shift(a: np.ndarray, d: Tuple[int, int, int]) -> np.ndarray:
    """b[x] = a[x + d] (False outside)."""
    out = np.zeros_like(a)
    if any(abs(s) >= a.shape[n] for n, s in enumerate(d)):
        return out
    src = tuple(slice(max(0, s), a.shape[n] + min(0, s)) for n, s in enumerate(d))
    dst = tuple(slice(max(0, -s), a.shape[n] + min(0, -s)) for n, s in enumerate(d))
    out[dst] = a[src]
    return out


def bar_pieces(occ: np.ndarray, free: np.ndarray = None, lengths: Iterable[int] = BAR_LENGTHS) -> List[tuple]:
    """Bars for the straight runs of `occ` that stand free in their layer: [(Brick, studs, seats)], longest first, not
    overlapping. A run of L cells (L in `lengths`) along x or y qualifies when its L-2 middle cells have nothing beside
    them (across the run), above or below them, and each end holds: something below it or above it. `free`: cells
    still to lay (not taken by other pieces)."""
    occ = np.asarray(occ, bool)
    free = occ if free is None else occ & free
    above, below = _shift(occ, (0, 0, 1)), _shift(occ, (0, 0, -1))
    holds = above | below
    taken = np.zeros_like(occ)
    found = []
    for axis, rot in ((0, 0), (1, 1)):
        side = (0, 1, 0) if axis == 0 else (1, 0, 0)
        lone = free & ~_shift(occ, side) & ~_shift(occ, tuple(-x for x in side)) & ~above & ~below
        for L in sorted(set(lengths), reverse=True):
            ok = np.ones_like(occ)
            step = [0, 0, 0]
            for t in range(L):                                  # ok[x]: the run starting at x fits
                step[axis] = t
                cell = _shift(free, tuple(step))
                if 0 < t < L - 1:
                    cell = cell & _shift(lone, tuple(step))
                else:
                    cell = cell & _shift(holds, tuple(step))
                ok &= cell
            for i, j, k in np.argwhere(ok):
                found.append((L, axis, rot, int(i), int(j), int(k)))
    out = []
    for L, axis, rot, i, j, k in sorted(found, key=lambda f: (-f[0], f[5], f[4], f[3], f[1])):
        w, d = (L, 1) if axis == 0 else (1, L)
        sl = (slice(i, i + w), slice(j, j + d), k)
        if taken[sl].any():
            continue
        taken[sl] = True
        key = f"bar_{L}"
        studs, seats = cell_props(key, rot)
        out.append((Brick(k, i, j, w, d, key, rot), studs.copy(), seats.copy()))
    return sorted(out, key=lambda pc: (pc[0].layer, pc[0].j, pc[0].i))


def apply(model: BrickModel, details: Iterable[str], unit: str, off=()) -> Tuple[BrickModel, Dict[str, int]]:
    """Tiles and rods on a laid model (see the module docstring): (the new model, {"tiles": n, "rods": n}). `off`:
    parts switched off (ids, lego_engine.catalogues: "round_1" - no rods, "tile_4x2" ...)."""
    details = tuple(details)
    off = set(off)
    if "round_1" in off:
        details = tuple(d for d in details if d != "rods")
    bricks = list(model.bricks)
    occ = model.occupancy
    if getattr(model, "assemblies", None):              # an axle sub-assembly covers what is under its support bricks
        from .assembly import cells_mask
        occ = occ | cells_mask(model.assemblies, occ.shape)
    nx, ny, nz = occ.shape
    studs = np.ones(occ.shape, bool) if model.stud_cells is None else model.stud_cells.copy()
    seats = np.ones(occ.shape, bool) if model.seat_cells is None else model.seat_cells.copy()
    counts = {"tiles": 0, "rods": 0}

    def put(n, key, rot):
        b = bricks[n]
        bricks[n] = Brick(b.layer, b.i, b.j, b.w, b.d, key, rot)
        st, se = cell_props(key, rot)
        sl = (slice(b.i, b.i + b.w), slice(b.j, b.j + b.d), b.layer)
        studs[sl], seats[sl] = st, se

    if "rods" in details:
        ones = np.zeros(occ.shape, bool)
        for b in bricks:
            if b.is_brick and b.w == 1 and b.d == 1:
                ones[b.i, b.j, b.layer] = True
        side = np.zeros(occ.shape, bool)
        for d in ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0)):
            side |= _shift(occ, d)
        lone = ones & ~side
        for i, j in zip(*np.nonzero(lone.any(axis=2))):
            col = lone[i, j]
            k = 0
            while k < nz:
                if not col[k]:
                    k += 1
                    continue
                e = k
                while e < nz and col[e]:
                    e += 1
                if e - k >= ROD_MIN:
                    for q in range(k, e):
                        put(int(model.ids[i, j, q]), "round_1", 0)
                        counts["rods"] += 1
                k = e
    ids = model.ids
    if "tiles" in details and unit == "plate":
        split_by_layer: Dict[int, List[int]] = {}
        for n, b in enumerate(list(bricks)):
            if not b.is_brick:
                continue
            if b.layer + 1 < nz and occ[b.i:b.i + b.w, b.j:b.j + b.d, b.layer + 1].any():
                continue
            L, W = max(b.w, b.d), min(b.w, b.d)
            key = f"tile_{L}x{W}"
            if key in TYPES and key not in off:
                put(n, key, 0 if b.w >= b.d else 1)
                counts["tiles"] += 1
            elif tile_cover(b.w, b.d, off):
                split_by_layer.setdefault(b.layer, []).append(n)
        if split_by_layer:                  # larger plates on top: covered with tiles where the model holds as well
            from .analysis import connectivity
            ids = ids.copy()

            def held(bs):
                m = BrickModel(bs, ids, model.sizes, model.layer_axis, studs, seats, {}, list(model.assemblies))
                c = connectivity(m)
                return c["groups"], len(c["floating"])

            ref = held(bricks)
            for k in sorted(split_by_layer, reverse=True):
                saved = (list(bricks), ids.copy(), studs.copy(), seats.copy())
                added = 0
                for n in split_by_layer[k]:
                    b = bricks[n]
                    parts = tile_cover(b.w, b.d, off)
                    for q, (a, c, w, d) in enumerate(parts):
                        L, W = max(w, d), min(w, d)
                        t = Brick(b.layer, b.i + a, b.j + c, w, d, f"tile_{L}x{W}", 0 if w >= d else 1)
                        if q == 0:
                            bricks[n] = t
                            m = n
                        else:
                            bricks.append(t)
                            m = len(bricks) - 1
                        ids[t.i:t.i + w, t.j:t.j + d, k] = m
                        st, se = cell_props(t.kind, t.rot)
                        studs[t.i:t.i + w, t.j:t.j + d, k], seats[t.i:t.i + w, t.j:t.j + d, k] = st, se
                        added += 1
                now = held(bricks)
                if now[0] > ref[0] or now[1] > ref[1]:
                    bricks[:], ids[...], studs[...], seats[...] = saved[0], saved[1], saved[2], saved[3]
                else:
                    counts["tiles"] += added
    new = BrickModel(bricks, ids, model.sizes, dict(model.layer_axis), studs, seats, dict(model.special_meshes),
                     list(model.assemblies))
    return new, counts


def tile_cover(w: int, d: int, off=()) -> List[Tuple[int, int, int, int]]:
    """Tiles that cover a w x d plate whose size has no tile: [(di, dj, w, d)] - strips two wide (one, for an odd
    width) along its length, each the longest tiles that fit; [] when the plate has a tile of its own size. `off`:
    tiles switched off (ids "tile_LxW")."""
    L, W = max(w, d), min(w, d)
    if f"tile_{L}x{W}" in TYPES and f"tile_{L}x{W}" not in off:
        return []
    have = {(t.L, t.W) for t in TYPES.values() if t.form == "tile" and t.key not in off}

    def strip(v, sw):                                         # tiles along a strip sw wide at v, or None
        got, u = [], 0
        while u < L:
            n = max((a for a, b in have if b == sw and a <= L - u), default=0)
            if not n:
                return None
            got.append((u, v, n, sw))
            u += n
        return got

    out = []
    v = 0
    while v < W:
        two = strip(v, 2) if W - v >= 2 else None
        if two is not None:
            out += two
            v += 2
            continue
        one = strip(v, 1)
        if one is None:
            return []
        out += one
        v += 1
    if w < d:                                                 # the plate runs along y: swap the frame
        out = [(b, a, sw, n) for a, b, n, sw in out]
    return out
