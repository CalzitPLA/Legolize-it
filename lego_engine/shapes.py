"""Shaped pieces and special pieces: where a part's surface is not a staircase of bricks.

Shaped pieces (`CATALOGUE`, one layer high, in four turns) - families:

    slope     slopes: a flat stud cell, then a straight fall to a low lip (slope 2x1, 3x1, ...; 1x1 has no stud)
    curve     curved slopes: a quarter ellipse from the full height down to the lip, no studs (curve 1x1 ... 4x1, 2x2)
    round     round corners (a quarter disk in plan: 1x1, 2x2, 3x3) and round bricks (1x1, 2x2)
    dome      dome corners: a quarter dome (1x1, 2x2) - the corner where curves meet on a round top
    inverted  inverted slopes and curves (upside down: flat studded top, the fall underneath) for overhangs

Every piece is a height field h(u, v) over its footprint in the piece's own frame: u (0..L cells) runs in the direction
the piece falls off, v (0..W) across; h is the share of the layer height that is material (0 = none). A normal piece
fills 0 <= t <= h, an inverted one 1 - h <= t <= 1 (t = the height in the layer, 0..1). Turn `rot` (0..3) points u
along +x, +y, -x, -y.

Which pieces (`fit_pieces`): every cell near the surface is measured finely (`fine_samples`: 6 x 6 x 6 points, inside
by winding number); a piece at a place costs the points where it and the part disagree, the bricks it replaces cost the
same (an occupied cell: its empty points; an empty cell: its material). A piece is laid where it saves the most
(at least `min_gain` cells), greedily, if
    - nothing sits on a cell of it without a stud (its curved top) - and nothing hangs under an inverted piece's fall;
    - it holds: on a stud below, or under something above its studs (the stud-and-tube rule, as for bricks).
The bricks are laid around the pieces afterwards (lego_engine.layout).

Special pieces (`special_cells`): cells where neither a brick, nor no brick, nor a shaped piece comes within
`tolerance` of the part (more than that share of the cell wrong) become custom pieces carrying the part's exact
geometry - the input cut to the cell (lego_engine.geometry.special_solid), with studs and sockets where they join
bricks. Neighbouring special cells of a layer are joined into rectangles like bricks. A special cell needs material
(5 % of the cell) and contact with the model. They print, they are not off-the-shelf bricks.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

LIP = 1.0 / 6.0                    # the low edge of slopes and curves: a sixth of the layer height (1.6 mm on a brick)
FAMILIES = ("slope", "curve", "round", "dome", "inverted")
STUD_REACH = 0.27                  # a stud (radius 2.4 = 0.3 pitch) needs material this far around the centre: 0.3
                                   # left round 1x1 corners and round 2x2 bricks without studs and seats (by 0.06 mm)
SPECIAL = "special"


@dataclass(frozen=True)
class PieceType:
    key: str            # "curve_3x1"
    family: str         # one of FAMILIES
    label: str          # "curve 3x1" (the parts list; L x W)
    form: str           # "slope", "curve", "quarter", "round", "dome"
    L: int              # cells along u (the fall, or the radius)
    W: int              # cells along v
    inverted: bool = False

    @property
    def rotations(self) -> Tuple[int, ...]:
        return (0,) if self.form == "round" else (0, 1, 2, 3)

    def footprint(self, rot: int) -> Tuple[int, int]:
        """(w, d): cells along x and y in turn `rot`."""
        return (self.L, self.W) if rot % 2 == 0 else (self.W, self.L)

    def height(self, u, v) -> np.ndarray:
        """h(u, v) in 0..1 (the share of the layer that is material; 0 = none), u and v in cells."""
        u, v = np.broadcast_arrays(np.asarray(u, float), np.asarray(v, float))
        L, W = self.L, self.W
        if self.form == "slope":
            if L == 1:
                return np.clip(1.0 - (1.0 - LIP) * u, 0, 1)
            return np.where(u <= 1.0, 1.0, LIP + (1.0 - LIP) * (L - u) / (L - 1.0))
        if self.form == "curve":
            return LIP + (1.0 - LIP) * np.sqrt(np.clip(1.0 - (u / L) ** 2, 0.0, 1.0))
        if self.form == "quarter":
            return np.where(u ** 2 + v ** 2 <= L ** 2, 1.0, 0.0)
        if self.form == "round":
            return np.where((u - L / 2) ** 2 + (v - W / 2) ** 2 <= (L / 2) ** 2, 1.0, 0.0)
        if self.form == "dome":
            r = np.sqrt(u ** 2 + v ** 2) / L
            return np.where(r <= 1.0, LIP + (1.0 - LIP) * np.sqrt(np.clip(1.0 - r ** 2, 0, 1)), 0.0)
        raise ValueError(self.form)


def _catalogue() -> Tuple[PieceType, ...]:
    out = []
    for L, W in ((1, 1), (2, 1), (3, 1), (2, 2), (3, 2)):
        out.append(PieceType(f"slope_{L}x{W}", "slope", f"slope {L}x{W}", "slope", L, W))
    for L, W in ((1, 1), (2, 1), (3, 1), (4, 1), (2, 2), (3, 2)):
        out.append(PieceType(f"curve_{L}x{W}", "curve", f"curve {L}x{W}", "curve", L, W))
    for r in (1, 2, 3):
        out.append(PieceType(f"quarter_{r}", "round", f"round corner {r}x{r}", "quarter", r, r))
    for r in (1, 2):
        out.append(PieceType(f"round_{r}", "round", f"round {r}x{r}", "round", r, r))
    for r in (1, 2):
        out.append(PieceType(f"dome_{r}", "dome", f"dome corner {r}x{r}", "dome", r, r))
    for L, W in ((2, 1), (3, 1), (2, 2)):
        out.append(PieceType(f"islope_{L}x{W}", "inverted", f"inverted slope {L}x{W}", "slope", L, W, True))
    for L, W in ((1, 1), (2, 1), (3, 1), (2, 2)):
        out.append(PieceType(f"icurve_{L}x{W}", "inverted", f"inverted curve {L}x{W}", "curve", L, W, True))
    return tuple(out)


CATALOGUE: Tuple[PieceType, ...] = _catalogue()
TYPES: Dict[str, PieceType] = {t.key: t for t in CATALOGUE}


def parse_families(text) -> Tuple[str, ...]:
    """'curve, round' / ['curve'] / 'all' / 'none' -> a tuple of FAMILIES (in their order)."""
    if text is None:
        return ()
    items = text.replace(";", ",").split(",") if isinstance(text, str) else list(text)
    items = [str(x).strip().lower() for x in items if str(x).strip()]
    if items in (["none"], ["off"], []):
        return ()
    if "all" in items:
        return FAMILIES
    bad = [x for x in items if x not in FAMILIES]
    if bad:
        raise ValueError(f"unknown piece families {bad}: choose from {', '.join(FAMILIES)}, all or none")
    return tuple(f for f in FAMILIES if f in items)


def label(kind: str, w: int, d: int) -> str:
    """The parts-list name of a piece: "curve 3x1", "special 1x2" (a plain brick has no kind)."""
    if kind == SPECIAL:
        a, b = sorted((int(w), int(d)))
        return f"special {a}x{b}"
    return TYPES[kind].label


# --------------------------------------------------------------------------------------------- frames and masks


def to_local(x, y, rot: int, w: int, d: int):
    """Footprint coordinates (x 0..w, y 0..d, cells) -> the piece's (u, v)."""
    if rot == 0:
        return x, y
    if rot == 1:
        return y, w - x
    if rot == 2:
        return w - x, d - y
    return d - y, x


def to_footprint(u, v, rot: int, L: float, W: float):
    """The piece's (u, v) -> footprint coordinates (x, y); L, W in the same unit as u, v."""
    if rot == 0:
        return u, v
    if rot == 1:
        return W - v, u
    if rot == 2:
        return L - u, W - v
    return v, L - u


@lru_cache(maxsize=None)
def mask(key: str, rot: int, s: int) -> np.ndarray:
    """(w*s, d*s, s) bool: the piece's material at the fine sample points of its footprint (cell sub-grid s^3,
    points at (q + 0.5) / s), in grid orientation."""
    t = TYPES[key]
    w, d = t.footprint(rot)
    q = (np.arange(s) + 0.5) / s
    xs = (np.arange(w)[:, None] + q[None, :]).ravel()
    ys = (np.arange(d)[:, None] + q[None, :]).ravel()
    X, Y = np.meshgrid(xs, ys, indexing="ij")
    H = t.height(*to_local(X, Y, rot, w, d))[..., None]
    T = q[None, None, :]
    return (T > 1.0 - H) if t.inverted else (T < H)


@lru_cache(maxsize=None)
def cell_props(key: str, rot: int, seat_min: float = 0.0) -> Tuple[np.ndarray, np.ndarray]:
    """(studs, seats), each (w, d) bool in grid orientation: where the piece has a stud on top (it holds what sits
    there), and where it can sit on a stud below (its underside is there, and - `seat_min`, a share of the layer - high
    enough above the stud for a socket: in plate layers the thin end of a curve is not)."""
    t = TYPES[key]
    w, d = t.footprint(rot)
    r = STUD_REACH
    offs = np.array([(0, 0), (r, 0), (-r, 0), (0, r), (0, -r), (0.7 * r, 0.7 * r), (-0.7 * r, 0.7 * r),
                     (0.7 * r, -0.7 * r), (-0.7 * r, -0.7 * r)])
    studs = np.zeros((w, d), bool)
    seats = np.zeros((w, d), bool)
    for a in range(w):
        for b in range(d):
            x, y = a + 0.5 + offs[:, 0], b + 0.5 + offs[:, 1]
            h = t.height(*to_local(x, y, rot, w, d))
            if t.inverted:
                studs[a, b] = bool((h > 0).all())            # a flat top wherever there is material
                seats[a, b] = bool((h >= 0.999).all())       # reaches down to the bottom
            else:
                studs[a, b] = bool((h >= 0.999).all())       # a flat top at full height
                seats[a, b] = bool((h > max(0.0, seat_min)).all())
    return studs, seats


# --------------------------------------------------------------------------------------------- fine samples


@dataclass
class FineSamples:
    """Cells near the surface measured finely: index (nx, ny, nz) int32 - the row in `inside` or -1; inside (n, s^3)
    bool - the sample points inside the part (order x, y, z as in voxelize.fill_fraction); solid (nx, ny, nz) - the
    coarse answer (fraction >= 0.5) for the cells not sampled finely."""
    s: int
    index: np.ndarray
    inside: np.ndarray
    solid: np.ndarray

    @property
    def n_points(self) -> int:
        return self.s ** 3

    def count(self) -> np.ndarray:
        """(nx, ny, nz) number of points inside, for every cell (unsampled cells: all or nothing)."""
        out = np.where(self.solid, self.n_points, 0).astype(np.int32)
        cells = self.index >= 0
        out[cells] = self.inside.sum(axis=1)[self.index[cells]]
        return out

    def face_fraction(self, top: bool) -> np.ndarray:
        """(nx, ny, nz): the share of the top (or bottom) face of each cell that is material."""
        s = self.s
        out = np.where(self.solid, 1.0, 0.0)
        cells = self.index >= 0
        cube = self.inside.reshape(-1, s, s, s)[:, :, :, s - 1 if top else 0]
        out[cells] = cube.reshape(len(cube), -1).mean(axis=1)[self.index[cells]]
        return out


def fine_samples(vertices: np.ndarray, faces: np.ndarray, grid, fraction: np.ndarray, s: int = 6,
                 max_points: int = 3_000_000) -> FineSamples:
    """Sample the cells near the surface (partly filled ones and their 6 neighbours) at s^3 points each."""
    from scipy import ndimage
    from .voxelize import _winding
    partial = (fraction > 0) & (fraction < 1)
    band = ndimage.binary_dilation(partial, structure=ndimage.generate_binary_structure(3, 1))
    cells = np.argwhere(band)
    q = (np.arange(s) + 0.5) / s
    su, sv, sw = np.meshgrid(q, q, q, indexing="ij")
    sub = np.stack([su.ravel(), sv.ravel(), sw.ravel()], axis=1)
    inside = np.zeros((len(cells), len(sub)), bool)
    per = max(1, max_points // len(sub))
    for c0 in range(0, len(cells), per):
        c = cells[c0:c0 + per].astype(float)
        pts = (c[:, None, :] + sub[None, :, :]) * grid.cell + grid.origin
        inside[c0:c0 + per] = (_winding(vertices, faces, pts.reshape(-1, 3)) > 0.5).reshape(len(c), len(sub))
    index = np.full(fraction.shape, -1, np.int32)
    index[tuple(cells.T)] = np.arange(len(cells), dtype=np.int32)
    return FineSamples(s, index, inside, fraction >= 0.5)


# --------------------------------------------------------------------------------------------- the fit


@dataclass
class Fit:
    """The shaped pieces chosen: pieces [(Brick, studs (w, d), seats (w, d))], taken (nx, ny, nz) their cells, forbid
    the cells that must stay empty (on a curved top, under an inverted piece's fall), gain [cells saved per piece]."""
    pieces: list
    taken: np.ndarray
    forbid: np.ndarray
    gain: List[float]


def types_for(families: Sequence[str]) -> List[PieceType]:
    return [t for t in CATALOGUE if t.family in families]


def fit_pieces(fine: FineSamples, occ: np.ndarray, allowed: np.ndarray, families: Sequence[str],
               min_gain: float = 0.12, slack: float = 0.05, seat_min: float = 0.0) -> Fit:
    """Choose shaped pieces (see the module docstring). `occ`: the cells that would be bricks; `allowed`: the cells a
    piece may cover (not thin-wall cells, not the inside of a hollow model). A piece must save `min_gain` cells in all
    and may not make any of its cells worse than the brick (or gap) there by more than `slack` of a cell - measured
    2026-11-12 on a sphere: without that rule inverted slopes won by their good cell while their thin end stuck out
    of the equator like a blade."""
    from .layout import Brick
    nx, ny, nz = occ.shape
    taken = np.zeros(occ.shape, bool)
    forbid = np.zeros(occ.shape, bool)
    types = types_for(families)
    if not types or fine.inside.shape[0] == 0:
        return Fit([], taken, forbid, [])
    s, N = fine.s, fine.n_points
    # the patterns: every footprint cell of every type and turn
    blocks, pats = [], []                         # blocks[(key, rot)] = [(a, b, pattern)], pats: (P, s^3)
    block_of = {}
    for t in types:
        for rot in t.rotations:
            m = mask(t.key, rot, s)
            w, d = t.footprint(rot)
            lst = []
            for a in range(w):
                for b in range(d):
                    lst.append((a, b, len(pats)))
                    pats.append(m[a * s:(a + 1) * s, b * s:(b + 1) * s, :].ravel())
            block_of[(t.key, rot)] = lst
    B = np.asarray(pats, np.float32)
    n_p = B.sum(axis=1)
    F = fine.inside.astype(np.float32)
    dot = F @ B.T                                                    # (n_band, P)
    n_c = fine.inside.sum(axis=1).astype(np.float32)
    count = fine.count().astype(np.float32)
    base = np.where(occ, N - count, count)                           # the bricks' (or the gap's) error per cell
    band = fine.index >= 0

    def cost(ci, cj, ck, p):
        bi = fine.index[ci, cj, ck]
        inb = bi >= 0
        bi = np.where(inb, bi, 0)
        return np.where(inb, n_c[bi] + n_p[p] - 2.0 * dot[bi, p],
                        np.where(fine.solid[ci, cj, ck], N - n_p[p], n_p[p]))

    cands = []                                                       # (gain, area, type, rot, i, j, k)
    for ti, t in enumerate(types):
        for rot in t.rotations:
            w, d = t.footprint(rot)
            if w > nx or d > ny:
                continue
            mx, my = nx - w + 1, ny - d + 1
            anyband = np.zeros((mx, my, nz), bool)
            allok = np.ones((mx, my, nz), bool)
            for a in range(w):
                for b in range(d):
                    anyband |= band[a:a + mx, b:b + my, :]
                    allok &= allowed[a:a + mx, b:b + my, :]
            ai, aj, ak = np.nonzero(anyband & allok)
            if not len(ai):
                continue
            E = np.zeros(len(ai), np.float32)
            S = np.zeros(len(ai), np.float32)
            worst = np.full(len(ai), -np.inf, np.float32)
            for a, b, p in block_of[(t.key, rot)]:
                e = cost(ai + a, aj + b, ak, p)
                s0 = base[ai + a, aj + b, ak]
                E += e
                S += s0
                worst = np.maximum(worst, e - s0)
            gain = (S - E) / N
            keep = (gain > min_gain) & (worst <= slack * N)
            for g, i, j, k in zip(gain[keep].tolist(), ai[keep].tolist(), aj[keep].tolist(), ak[keep].tolist()):
                cands.append((g, w * d, ti, rot, i, j, k))
    cands.sort(key=lambda c: (-c[0], -c[1]))

    filled = occ.copy()
    studs_g = np.zeros(occ.shape, bool)
    seats_g = np.zeros(occ.shape, bool)
    pieces, gains = [], []
    for g, _area, ti, rot, i, j, k in cands:
        t = types[ti]
        w, d = t.footprint(rot)
        sl = (slice(i, i + w), slice(j, j + d), k)
        if taken[sl].any() or forbid[sl].any():
            continue
        studs, seats = cell_props(t.key, rot, seat_min)
        up = filled[i:i + w, j:j + d, k + 1] if k + 1 < nz else None
        dn = filled[i:i + w, j:j + d, k - 1] if k > 0 else None
        if not t.inverted and up is not None and (up & ~studs).any():
            continue                                     # something would sit on its curved top
        if t.inverted and dn is not None and (dn & ~seats).any():
            continue                                     # something would be under its fall
        held = k == 0
        if not held and dn is not None:                  # on studs below (a brick's, or a piece's stud cell)
            stud_dn = ~taken[i:i + w, j:j + d, k - 1] | studs_g[i:i + w, j:j + d, k - 1]
            held = bool((dn & seats & stud_dn).any())
        if not held and up is not None:                  # under something that sits on its studs
            seat_up = ~taken[i:i + w, j:j + d, k + 1] | seats_g[i:i + w, j:j + d, k + 1]
            held = bool((up & studs & seat_up).any())
        if not held:
            continue
        taken[sl] = True
        filled[sl] = True
        studs_g[sl] = studs
        seats_g[sl] = seats
        if not t.inverted and k + 1 < nz:
            forbid[i:i + w, j:j + d, k + 1] |= ~studs
        if t.inverted and k > 0:
            forbid[i:i + w, j:j + d, k - 1] |= ~seats
        pieces.append((Brick(k, i, j, w, d, t.key, rot), studs, seats))
        gains.append(round(float(g), 3))
    order = sorted(range(len(pieces)), key=lambda n: (pieces[n][0].layer, pieces[n][0].j, pieces[n][0].i))
    return Fit([pieces[n] for n in order], taken, forbid, [gains[n] for n in order])


def special_cells(fine: FineSamples, occ: np.ndarray, allowed: np.ndarray, taken: np.ndarray, forbid: np.ndarray,
                  tolerance: float = 0.3, min_material: float = 0.05, thin: Optional[np.ndarray] = None,
                  thin_material: float = 0.1) -> np.ndarray:
    """(nx, ny, nz) bool: the cells that become special pieces - near the surface, at least `min_material` of the cell
    material, and a brick there (or none, for an empty cell) more than `tolerance` of the cell wrong; not under a
    shaped piece; connected (face to face, through other special cells) to the model. `thin`: thin-wall cells (not in
    `allowed`) that may still become special when at least `thin_material` of them is material - a cone's slim tip,
    not a sheet (2026-11-12: a 0.3 mm flange fills 4 % of a cell; a whole sheet-metal part would turn special)."""
    from scipy import ndimage
    N = fine.n_points
    count = fine.count()
    wrong = np.where(occ, N - count, count) / N
    ok = allowed if thin is None else allowed | (thin & (count >= thin_material * N))
    cand = (fine.index >= 0) & ok & ~taken & ~forbid & (wrong > tolerance) & (count >= min_material * N)
    if not cand.any():
        return cand
    model = (occ & ~cand) | taken
    lab, _n = ndimage.label(cand | model, structure=ndimage.generate_binary_structure(3, 1))
    keep = np.unique(lab[model])
    return cand & np.isin(lab, keep[keep > 0])


def special_pieces(cells: np.ndarray, fine: FineSamples, sizes) -> list:
    """The special cells joined into rectangles per layer (the brick catalogue) -> [(Brick, studs, seats)]: studs
    where the piece's top face is at least half material, seats where its bottom face is."""
    from .layout import Brick, layout_layer
    top = fine.face_fraction(True) >= 0.5
    bottom = fine.face_fraction(False) >= 0.5
    out = []
    for k in range(cells.shape[2]):
        if not cells[:, :, k].any():
            continue
        for b in layout_layer(cells[:, :, k].copy(), None, sizes, k):
            sl = (slice(b.i, b.i + b.w), slice(b.j, b.j + b.d), k)
            out.append((Brick(k, b.i, b.j, b.w, b.d, SPECIAL, 0), top[sl].copy(), bottom[sl].copy()))
    return out


# --------------------------------------------------------------------------------------------- geometry


def outline(key: str, pitch: float, height: float, clearance: float = 0.0, n: int = 12) -> Dict:
    """The piece's body in mm, in its own frame (u along x, v along y, the footprint from 0 to L*pitch, W*pitch;
    inset by `clearance` at the sides), as one closed outline and an operation:
        {"op": "extrude_y", "poly": [(x, z), ...], "y0", "length"}   slopes and curves: the side profile, extruded
        {"op": "extrude_z", "poly": [(x, y), ...], "height"}         round corners: the plan, extruded
        {"op": "cylinder", "centre": (x, y), "radius", "height"}      round bricks
        {"op": "revolve", "poly": [(r, z), ...], "centre": (x, y)}    dome corners: a quarter turn about z
    "curve": (first, last) - the indices of `poly` that lie on its curved run (a spline in FreeCAD), or None.
    Outlines run counter-clockwise."""
    t = TYPES[key]
    c = clearance
    L, W = t.L, t.W
    sx, sy = (L * pitch - 2 * c) / L, (W * pitch - 2 * c) / W

    def ccw(poly):
        a = np.asarray(poly, float)
        area = 0.5 * np.sum(a[:, 0] * np.roll(a[:, 1], -1) - np.roll(a[:, 0], -1) * a[:, 1])
        return (poly[::-1], True) if area < 0 else (poly, False)

    if t.form in ("slope", "curve"):
        if t.form == "slope":
            run = [(L, LIP)] + ([(1.0, 1.0)] if L > 1 else []) + [(0.0, 1.0)]
            curve = None
        else:
            us = np.linspace(L, 0, n * L + 1)
            run = [(float(u), float(t.height(u, 0))) for u in us]
            curve = (2, 2 + len(run) - 1)
        prof = [(0.0, 0.0), (float(L), 0.0)] + run
        if t.inverted:
            prof = [(u, 1.0 - z) for u, z in prof]
        poly = [(c + u * sx, z * height) for u, z in prof]
        poly, flipped = ccw(poly)
        if flipped and curve is not None:
            curve = (len(poly) - 1 - curve[1], len(poly) - 1 - curve[0])
        return {"op": "extrude_y", "poly": poly, "y0": c, "length": W * pitch - 2 * c, "curve": curve}
    if t.form == "quarter":
        R = L * pitch - 2 * c
        a = np.linspace(0, np.pi / 2, n * L + 1)
        arc = [(c + R * np.cos(x), c + R * np.sin(x)) for x in a]
        poly = [(c, c)] + arc
        return {"op": "extrude_z", "poly": poly, "height": height, "curve": (1, len(poly) - 1)}
    if t.form == "round":
        return {"op": "cylinder", "centre": (L * pitch / 2, W * pitch / 2), "radius": L * pitch / 2 - c,
                "height": height, "curve": None}
    if t.form == "dome":
        R = L * pitch - 2 * c
        rs = np.linspace(1, 0, n * L + 1)
        run = [(R * r, height * (LIP + (1 - LIP) * np.sqrt(max(0.0, 1 - r * r)))) for r in rs]
        poly = [(0.0, 0.0), (R, 0.0)] + run
        return {"op": "revolve", "poly": poly, "centre": (c, c), "curve": (2, len(poly) - 1)}
    raise ValueError(t.form)


def stud_cells_local(key: str) -> List[Tuple[int, int]]:
    """The cells (a along u, b along v) with a stud, in the piece's own frame (turn 0)."""
    studs, _ = cell_props(key, 0)
    return [(int(a), int(b)) for a, b in np.argwhere(studs)]


def placement_offset(key: str, rot: int, pitch: float) -> Tuple[float, float]:
    """Where the piece's own origin lands in its footprint when turned by rot x 90 degrees about z."""
    t = TYPES[key]
    Lp, Wp = t.L * pitch, t.W * pitch
    return {0: (0.0, 0.0), 1: (Wp, 0.0), 2: (Lp, Wp), 3: (0.0, Lp)}[rot % 4]


def deviation(model, fine: FineSamples, part_cells: float, ignore: Optional[np.ndarray] = None) -> float:
    """How far the model is from the part: the volume where they disagree (a brick's empty corners, the part's
    material no part covers, a piece's misfit; special pieces are exact) over the part's volume, measured on the
    fine samples of the cells near the surface. `part_cells`: the part's volume in cells; `ignore`: cells left empty
    on purpose (the inside of a hollow model)."""
    N = fine.n_points
    band = fine.index >= 0
    if ignore is not None:
        band = band & ~ignore
    count = fine.count()
    ids = model.ids
    kinds = np.array([b.kind for b in model.bricks] + [""], object)
    kind_of = np.where(ids >= 0, kinds[ids], "")
    wrong = np.zeros(ids.shape, float)
    empty = band & (ids < 0)
    wrong[empty] = count[empty]
    brick = band & (ids >= 0) & (kind_of == "")
    wrong[brick] = N - count[brick]
    for n, b in enumerate(model.bricks):
        if not b.kind or b.is_special:
            continue
        m = mask(b.kind, b.rot, fine.s)
        s = fine.s
        for a in range(b.w):
            for q in range(b.j, b.j + b.d):
                cell = (b.i + a, q, b.layer)
                if not band[cell]:
                    continue
                block = m[a * s:(a + 1) * s, (q - b.j) * s:(q - b.j + 1) * s, :].ravel()
                row = fine.index[cell]
                wrong[cell] = np.count_nonzero(block != fine.inside[row])
    return float(wrong.sum() / N / max(part_cells, 1e-9))
