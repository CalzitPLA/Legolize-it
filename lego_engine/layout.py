"""Covering each layer's occupied cells with bricks from the catalogue, so the stack holds together.

Per layer, cells are visited in scan order and the first free cell becomes the corner of the next brick (in that order
every cell before it is covered, so a brick that covers it must start there - the anchoring is complete). Of the bricks
that fit there (free cells only, the catalogue in both orientations) the one with the best score is laid:

    score = area + bridge_weight * min(bricks below it - 1, 2) + orient_weight * (its long side along the layer's axis)
                 - seam_weight * (cells of its far end, along its length, that land on a seam of the layer below)

- area: big bricks first (fewer parts, stronger);
- bridging: a brick that sits on two or three bricks of the layer below ties them together;
- seams: a brick should not end where a brick below ends while both layers go on - else the joints line up through
  the layers and nothing ties the two sides together (the running bond of a brick wall). Measured 2026-11-11 on a
  portal frame (thin walls, 24 studs): without this term every layer of the beam started with an 8-long brick, the
  joints lined up and the beam's middle floated (5 bricks); the greedy cannot see this from the bridging term alone,
  because the brick that would shift the joints is chosen one anchor earlier;
- orientation: even layers prefer bricks along x and are scanned along x, odd layers along y, so layers cross.

`layout_connected` (what the pipeline uses) adds repair passes: where two groups of bricks touch side by side in a
layer but do not hold together, the layout is redone with a penalty for ending on that boundary, so a brick reaches
across (measured 2026-11-11: a 1 mm fin on a 40 mm block, thin walls "auto": 3 groups -> 1).

The result is exact: every occupied cell is covered by exactly one brick and no brick covers an empty cell
(`check_cover`).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Tuple

import numpy as np

from .system import DEFAULT_SIZES, normalise_sizes, size_name


@dataclass(frozen=True)
class Brick:
    layer: int        # k
    i: int            # first cell along x
    j: int            # first cell along y
    w: int            # cells along x
    d: int            # cells along y

    @property
    def name(self) -> str:
        return size_name(self.w, self.d)

    @property
    def area(self) -> int:
        return self.w * self.d

    @property
    def along_x(self) -> bool:
        return self.w >= self.d

    def to_list(self) -> List[int]:
        return [self.layer, self.i, self.j, self.w, self.d]


@dataclass
class BrickModel:
    bricks: List[Brick]
    ids: np.ndarray                   # (nx, ny, nz) int32: index into bricks, -1 = empty
    sizes: Tuple[Tuple[int, int], ...]
    layer_axis: Dict[int, str] = field(default_factory=dict)

    @property
    def shape(self) -> Tuple[int, int, int]:
        return tuple(self.ids.shape)

    @property
    def occupancy(self) -> np.ndarray:
        return self.ids >= 0

    def layer_bricks(self, k: int) -> List[int]:
        return [n for n, b in enumerate(self.bricks) if b.layer == k]

    def counts(self) -> Dict[str, int]:
        out: Dict[str, int] = {}
        for b in self.bricks:
            out[b.name] = out.get(b.name, 0) + 1
        return dict(sorted(out.items(), key=lambda kv: (-_area(kv[0]), kv[0])))


def _area(name: str) -> int:
    a, b = name.split("x")
    return int(a) * int(b)


def _orientations(sizes: Iterable[Tuple[int, int]], prefer_x: bool) -> List[Tuple[int, int]]:
    """(w, d) candidates, each size in both orientations, the layer's preferred orientation first."""
    out = []
    for a, b in sizes:
        long_x, long_y = (b, a), (a, b)
        for o in ((long_x, long_y) if prefer_x else (long_y, long_x)):
            if o not in out:
                out.append(o)
    return out


def _seams(below: Optional[np.ndarray], layer: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    """Where an end of a brick of this layer would sit on a seam of the layer below while both layers go on:
    ax[a, q] for the boundary between cells a - 1 and a along x (shape nx + 1, ny), ay[a, q] along y (nx, ny + 1)."""
    nx, ny = layer.shape
    ax = np.zeros((nx + 1, ny), bool)
    ay = np.zeros((nx, ny + 1), bool)
    if below is None:
        return ax, ay
    l, r = below[:-1, :], below[1:, :]
    ax[1:-1, :] = (l >= 0) & (r >= 0) & (l != r) & layer[1:, :]
    l, r = below[:, :-1], below[:, 1:]
    ay[:, 1:-1] = (l >= 0) & (r >= 0) & (l != r) & layer[:, 1:]
    return ax, ay


def layout_layer(free: np.ndarray, below: Optional[np.ndarray], sizes: Tuple[Tuple[int, int], ...], k: int,
                 bridge_weight: float = 1.5, orient_weight: float = 0.5,
                 seam_weight: Optional[float] = None,
                 junction: Optional[Tuple[np.ndarray, np.ndarray]] = None,
                 junction_weight: float = 6.0) -> List[Brick]:
    """Bricks covering the True cells of `free` (nx, ny; consumed). `below`: the brick ids of the layer below or None.
    `seam_weight` (default 1.6 x bridge_weight): the penalty per cell of a brick's far end that lands on a seam below.
    `junction` (jx (nx + 1, ny), jy (nx, ny + 1)): boundaries between groups that did not hold together in an earlier
    pass (`layout_connected`); a brick ending on one, at either far end, pays `junction_weight` per cell."""
    nx, ny = free.shape
    prefer_x = (k % 2 == 0)
    seam_weight = 1.6 * bridge_weight if seam_weight is None else seam_weight
    cands = _orientations(sizes, prefer_x)
    order_by_area = sorted(range(len(cands)), key=lambda n: -cands[n][0] * cands[n][1])
    cands = [cands[n] for n in order_by_area]
    W = np.array([c[0] for c in cands])
    D = np.array([c[1] for c in cands])
    area = W * D
    reach = int(max(W.max(), D.max()))
    ax, ay = _seams(below, free)                    # free is still the whole layer here
    bonus_max = 2 * bridge_weight + orient_weight
    free_rows = free.tolist()                       # fast membership test for the scan
    bricks: List[Brick] = []
    order = ((i, j) for j in range(ny) for i in range(nx)) if prefer_x else \
            ((i, j) for i in range(nx) for j in range(ny))
    for i, j in order:
        if not free_rows[i][j]:
            continue
        win = free[i:i + reach, j:j + reach]
        sat = np.zeros((win.shape[0] + 1, win.shape[1] + 1), np.int32)     # summed-area table of the window
        sat[1:, 1:] = win.cumsum(0).cumsum(1)
        inside = (W <= win.shape[0]) & (D <= win.shape[1])
        Wc, Dc = np.minimum(W, win.shape[0]), np.minimum(D, win.shape[1])
        fits = inside & (sat[Wc, Dc] == area)
        idx = np.flatnonzero(fits)                  # 1x1 always fits; largest first
        best, best_score = None, -1e9
        below_win = below[i:i + reach, j:j + reach] if below is not None else None
        for n in idx:
            a = int(area[n])
            if a + bonus_max <= best_score:
                break                               # sorted by area: no later candidate can win
            w, d = int(W[n]), int(D[n])
            score = float(a)
            if below_win is not None:
                under = below_win[:w, :d]
                nb = len(set(under[under >= 0].tolist()))
                if nb > 1:
                    score += bridge_weight * min(nb - 1, 2)
                # only the ends along the brick's length (a crossing brick's side always lies on joints below)
                aligned = (int(ax[i + w, j:j + d].sum()) if w >= d else 0) + (int(ay[i:i + w, j + d].sum()) if d >= w else 0)
                score -= seam_weight * aligned
            if junction is not None:
                score -= junction_weight * (int(junction[0][i + w, j:j + d].sum()) + int(junction[1][i:i + w, j + d].sum()))
            if w != d and ((w > d) == prefer_x):
                score += orient_weight
            if score > best_score + 1e-9:
                best, best_score = n, score
        w, d = int(W[best]), int(D[best])
        free[i:i + w, j:j + d] = False
        for a in range(i, i + w):
            row = free_rows[a]
            for b in range(j, j + d):
                row[b] = False
        bricks.append(Brick(k, i, j, w, d))
    return bricks


def layout_bricks(occ: np.ndarray, sizes: Iterable[Tuple[int, int]] = DEFAULT_SIZES, max_length: int = 0,
                  bridge_weight: float = 1.5, orient_weight: float = 0.5,
                  junctions: Optional[Dict[int, Tuple[np.ndarray, np.ndarray]]] = None) -> BrickModel:
    """Bricks for every occupied cell of `occ` (nx, ny, nz), layer by layer from the bottom. `junctions`: per layer,
    boundaries a brick should reach across (see layout_layer)."""
    sizes = normalise_sizes(sizes, max_length)
    nx, ny, nz = occ.shape
    ids = np.full(occ.shape, -1, np.int32)
    bricks: List[Brick] = []
    axis = {}
    for k in range(nz):
        layer = occ[:, :, k]
        if not layer.any():
            continue
        below = ids[:, :, k - 1] if k > 0 else None
        new = layout_layer(layer.copy(), below, sizes, k, bridge_weight, orient_weight,
                           junction=(junctions or {}).get(k))
        for b in new:
            ids[b.i:b.i + b.w, b.j:b.j + b.d, k] = len(bricks)
            bricks.append(b)
        axis[k] = "x" if k % 2 == 0 else "y"
    return BrickModel(bricks, ids, sizes, axis)


def junctions_of(model: BrickModel, group_of: List[int]) -> Dict[int, Tuple[np.ndarray, np.ndarray]]:
    """Per layer: where two neighbouring cells belong to bricks of different groups (lego_engine.analysis)."""
    g = np.where(model.ids >= 0, np.asarray(list(group_of) + [-1], np.int64)[model.ids], -1)
    nx, ny, nz = g.shape
    out = {}
    for k in range(nz):
        a = g[:, :, k]
        jx = np.zeros((nx + 1, ny), bool)
        jy = np.zeros((nx, ny + 1), bool)
        jx[1:-1, :] = (a[:-1, :] >= 0) & (a[1:, :] >= 0) & (a[:-1, :] != a[1:, :])
        jy[:, 1:-1] = (a[:, :-1] >= 0) & (a[:, 1:] >= 0) & (a[:, :-1] != a[:, 1:])
        if jx.any() or jy.any():
            out[k] = (jx, jy)
    return out


def layout_connected(occ: np.ndarray, sizes: Iterable[Tuple[int, int]] = DEFAULT_SIZES, max_length: int = 0,
                     bridge_weight: float = 1.5, orient_weight: float = 0.5, rounds: int = 3) -> BrickModel:
    """layout_bricks, then up to `rounds` passes that lay the bricks again with a penalty for ending where two groups
    meet side by side (a thin fin on a block, a joint of a frame: every layer's bricks end at the same place and the
    two sides only touch). Returns the best pass: fewest floating bricks, then fewest groups, then fewest bricks.
    Groups that never touch (the parts of an assembly) are left as they are."""
    from .analysis import connectivity
    junctions: Dict[int, Tuple[np.ndarray, np.ndarray]] = {}
    model = layout_bricks(occ, sizes, max_length, bridge_weight, orient_weight)
    con = connectivity(model)
    best, best_key = model, (len(con["floating"]), con["groups"], len(model.bricks))
    for _ in range(rounds):
        new = junctions_of(model, con["group_of"])
        if not new:
            break
        for k, (jx, jy) in new.items():                  # the boundaries of all passes so far
            if k in junctions:
                junctions[k] = (junctions[k][0] | jx, junctions[k][1] | jy)
            else:
                junctions[k] = (jx, jy)
        model = layout_bricks(occ, sizes, max_length, bridge_weight, orient_weight, junctions)
        con = connectivity(model)
        key = (len(con["floating"]), con["groups"], len(model.bricks))
        if key < best_key:
            best, best_key = model, key
        if con["groups"] == 1:
            break
    return best


def check_cover(model: BrickModel, occ: np.ndarray) -> List[str]:
    """Problems of the cover (an empty list when every occupied cell has exactly one brick and nothing else does)."""
    problems = []
    count = np.zeros(occ.shape, np.int32)
    allowed = set(model.sizes)
    for n, b in enumerate(model.bricks):
        if tuple(sorted((b.w, b.d))) not in allowed:
            problems.append(f"brick {n} {b.name} is not in the catalogue")
        sl = (slice(b.i, b.i + b.w), slice(b.j, b.j + b.d), b.layer)
        count[sl] += 1
        if not occ[sl].all():
            problems.append(f"brick {n} {b.name} at layer {b.layer} ({b.i}, {b.j}) covers empty cells")
    if (count > 1).any():
        problems.append(f"{int((count > 1).sum())} cells covered twice")
    if (occ & (count == 0)).any():
        problems.append(f"{int((occ & (count == 0)).sum())} occupied cells not covered")
    return problems
