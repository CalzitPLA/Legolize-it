"""Artsy mode: a model built with craft rather than to the last cell (`LegoSettings.style = "artsy"`).

The part is read the way a builder reads it, and every kind of shape gets its own technique:

    round bodies   an axle with parts that have a cross hole threaded on it (lego_engine.technic, .assembly) - the
                   profile along the axle in round plates and bricks, gears, discs, rims; lying axles held by bricks
                   with an axle hole in the model (or on posts); bodies that share an axle become one stack, each part
                   in the colour of the outermost body there
    small details  a printed piece carrying the part's exact shape, about one brick in size (shapes.special_pieces)
    the rest       cells, made symmetric where the part nearly is (mirror planes), without stray cells and pinholes,
                   then laid with round and curved pieces and the details (tiles, rods, bars)

Accuracy is not what this mode optimises: the deviation is still measured and shown, beside a craft report
(`stats["craft"]`): symmetry, techniques, part types, colour regions, visible studs.

    prepare(mesh, placed, grid, frac, touched, settings, unit) -> Plan (occupancy, assemblies, printed cells, report)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np

from .assembly import Assembly, axle_height, cells_mask, place
from .technic import HOLE_Z, ON_AXLE, PARTS, axles_for

ROUND_MAX_SCORE = 0.12          # a body is round about an axis when its outline varies less than this around it
ROUND_MIN_COVER = 0.6           # ... and its surface goes at least this share of the way round, slice by slice
SYMMETRY_MIN = 0.85             # a mirror plane is enforced where the material matches its mirror image this well
MAX_COMPONENTS = 512           # an input of more separate parts is read by its colours only
DETAIL_CELLS = 1.6              # a component this many cells across (or less) and detailed is printed
UPRIGHT_PARTS = ("round_plate_2x2", "round_brick_2x2", "round_brick_2x2_grille", "round_plate_4x4",
                 "round_brick_4x4", "round_plate_6x6")        # stud-up round parts: their studs meet the layers
RADIUS_TOLERANCE = 0.25         # a run of the profile: its radius within this share of its mean
BASE_COLOUR = "black"           # the display base (as CERN Science Gateway's LEGO detectors stand on)
RADIUS_WEIGHT = 10.0            # a part's radius this share off the profile counts like RADIUS_WEIGHT x as many parts


@dataclass
class Body:
    name: str                   # "colour red", "component 3", "the part"
    faces: np.ndarray           # face indices into the mesh
    axis: int = -1              # round: the grid axis it turns about
    line: Tuple[float, float] = (0.0, 0.0)
    t0: float = 0.0
    t1: float = 0.0
    score: float = 1.0
    profile: Optional[np.ndarray] = None     # (n, 3): t, outer radius, inner radius (mm)
    colour: str = ""
    extent: Optional[np.ndarray] = None      # bbox size (mm)


@dataclass
class Plan:
    occupancy: np.ndarray                    # the cells the stud-up model is laid in (without the assemblies' cells)
    assemblies: List[Assembly] = field(default_factory=list)
    printed: Optional[np.ndarray] = None     # cells that become printed pieces
    contacts: Optional[np.ndarray] = None    # cells under and over the assemblies' contacts: plain studded bricks there
    base: Optional[np.ndarray] = None        # a display base under parts that stand apart (BASE_COLOUR)
    mirrored: Tuple[str, ...] = ()
    report: Dict = field(default_factory=dict)


# --------------------------------------------------------------------------------------------- reading the part


def sample_surface(vertices: np.ndarray, faces: np.ndarray, n: int = 4000, seed: int = 0) -> np.ndarray:
    tri = vertices[faces]
    area = 0.5 * np.linalg.norm(np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0]), axis=1)
    if area.sum() <= 0:
        return tri.reshape(-1, 3)
    rng = np.random.default_rng(seed)
    pick = rng.choice(len(tri), size=n, p=area / area.sum())
    a, b = rng.random(n), rng.random(n)
    flip = a + b > 1
    a[flip], b[flip] = 1 - a[flip], 1 - b[flip]
    t = tri[pick]
    return t[:, 0] + a[:, None] * (t[:, 1] - t[:, 0]) + b[:, None] * (t[:, 2] - t[:, 0])


def roundness(points: np.ndarray, axis: int, slice_mm: float = 2.0, bins: int = 16):
    """How round the points are about a line parallel to `axis` through their outline's centre: (score - the median,
    over slices along the axis, of the outer radius' relative spread around it; 1 when it is not round), line, t0, t1,
    profile (t, outer radius, inner radius per slice)."""
    tr = [q for q in range(3) if q != axis]
    u, v, t = points[:, tr[0]], points[:, tr[1]], points[:, axis]
    cu, cv = (u.min() + u.max()) / 2, (v.min() + v.max()) / 2
    r = np.hypot(u - cu, v - cv)
    phi = ((np.arctan2(v - cv, u - cu) + np.pi) / (2 * np.pi) * bins).astype(int) % bins
    t0, t1 = float(t.min()), float(t.max())
    n = int(np.clip((t1 - t0) / slice_mm, 3, 96))
    edges = np.linspace(t0, t1 + 1e-9, n + 1)
    which = np.clip(np.searchsorted(edges, t, side="right") - 1, 0, n - 1)
    scores, prof = [], []
    for s in range(n):
        m = which == s
        if m.sum() < 8:
            continue
        ro = np.full(bins, np.nan)
        ri = np.full(bins, np.nan)
        for b in range(bins):
            mb = m & (phi == b)
            if mb.any():
                ro[b] = r[mb].max()
                ri[b] = r[mb].min()
        cover = np.isfinite(ro).mean()
        mean = np.nanmean(ro)
        if cover < ROUND_MIN_COVER or mean <= 1e-6:
            scores.append(1.0)
            continue
        scores.append(float(np.nanstd(ro) / mean))
        prof.append(((edges[s] + edges[s + 1]) / 2, float(mean), float(np.nanmin(ri))))
    if not prof:
        return 1.0, (cu, cv), t0, t1, None
    return float(np.median(scores)), (float(cu), float(cv)), t0, t1, np.array(prof)


def components(vertices: np.ndarray, faces: np.ndarray) -> List[np.ndarray]:
    """The input's separate parts as arrays of face indices: faces joined by a shared vertex (positions to 1 um) - an
    edge split needs clean edges, and a mesh written without welded vertices falls apart into triangles there."""
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components
    _u, vid = np.unique(np.round(np.asarray(vertices, float), 3), axis=0, return_inverse=True)
    f = vid.reshape(-1)[np.asarray(faces)]
    nf, nv = len(f), int(vid.max()) + 1 if len(vid) else 0
    rows = np.repeat(np.arange(nf), 3)
    g = coo_matrix((np.ones(3 * nf, np.int8), (rows, nf + f.ravel())), shape=(nf + nv, nf + nv))
    n, lab = connected_components(g, directed=False)
    flab = lab[:nf]
    order = np.argsort(flab, kind="stable")
    cuts = np.nonzero(np.diff(flab[order]))[0] + 1
    return [c for c in np.split(order, cuts) if len(c)]


def candidates(mesh_vertices, faces, face_colours=None) -> List[Body]:
    """The bodies to look at: the input's colours (one body each), its connected parts, and the whole."""
    out = []
    if face_colours is not None:
        from .palette import NAMES, snap_index
        idx = snap_index(np.asarray(face_colours, float)[:, :3])
        for k in np.unique(idx):
            out.append(Body(f"colour {NAMES[k]}", np.nonzero(idx == k)[0], colour=NAMES[k]))
    comps = components(mesh_vertices, faces) if len(faces) < 4_000_000 else []
    if 1 < len(comps) <= MAX_COMPONENTS:
        for q, c in enumerate(comps):
            out.append(Body(f"component {q}", c))
    out.append(Body("the part", np.arange(len(faces))))
    return out


def analyse(placed: np.ndarray, faces: np.ndarray, face_colours=None, samples: int = 4000) -> List[Body]:
    """Every candidate body with its roundness about each grid axis (the best one kept) and its size."""
    bodies = candidates(placed, faces, face_colours)
    for b in bodies:
        pts = sample_surface(placed, faces[b.faces], samples)
        b.extent = pts.max(axis=0) - pts.min(axis=0)
        best = None
        for axis in range(3):
            got = roundness(pts, axis)
            if best is None or got[0] < best[1][0]:
                best = (axis, got)
        axis, (score, line, t0, t1, prof) = best
        b.axis, b.score, b.line, b.t0, b.t1, b.profile = axis, score, line, t0, t1, prof
    return bodies


# --------------------------------------------------------------------------------------------- symmetry, cleaning


def mirror_score(frac: np.ndarray, axis: int) -> float:
    """How well the material matches its mirror image about the middle of its extent along `axis` (1 = exactly)."""
    m = frac > 0
    if not m.any():
        return 0.0
    idx = np.nonzero(m.any(axis=tuple(q for q in range(3) if q != axis)))[0]
    lo, hi = idx.min(), idx.max()
    sl = [slice(None)] * 3
    sl[axis] = slice(lo, hi + 1)
    f = frac[tuple(sl)]
    g = np.flip(f, axis=axis)
    return float(np.minimum(f, g).sum() / max(1e-9, f.sum()))


def symmetrise(frac: np.ndarray, touched: Optional[np.ndarray], axes) -> Tuple[np.ndarray, Optional[np.ndarray]]:
    """The fill made mirror-symmetric about the middle of the material along each of `axes` (the mean of a cell and
    its mirror cell), the surface cells too (either side)."""
    f = frac.astype(np.float32).copy()
    t = None if touched is None else touched.copy()
    for axis in axes:
        idx = np.nonzero((f > 0).any(axis=tuple(q for q in range(3) if q != axis)))[0]
        lo, hi = idx.min(), idx.max()
        sl = [slice(None)] * 3
        sl[axis] = slice(lo, hi + 1)
        sl = tuple(sl)
        f[sl] = 0.5 * (f[sl] + np.flip(f[sl], axis=axis))
        if t is not None:
            t[sl] = t[sl] | np.flip(t[sl], axis=axis)
    return f, t


def clean(occ: np.ndarray, min_island: int = 3) -> np.ndarray:
    """No stray bits: pieces under `min_island` cells go, cells with no face neighbour go, single-cell pinholes (an
    empty cell with five or six filled neighbours) are filled."""
    from scipy import ndimage
    occ = occ.copy()
    for _ in range(2):
        n6 = ndimage.convolve(occ.astype(np.int8), ndimage.generate_binary_structure(3, 1).astype(np.int8),
                              mode="constant") - occ
        occ &= n6 > 0
        occ |= ~occ & (n6 >= 5)
    lab, n = ndimage.label(occ, structure=ndimage.generate_binary_structure(3, 3))
    if n:
        size = ndimage.sum(occ, lab, index=np.arange(1, n + 1))
        occ &= ~np.isin(lab, 1 + np.nonzero(size < min_island)[0])
    return occ


def block_colours(labels: np.ndarray, occ: np.ndarray, min_region: int = 3) -> np.ndarray:
    """Colour blocking on the visible cells' palette labels (-1 inside, -2 empty): a region of one colour smaller than
    `min_region` cells takes the colour most of its neighbours have."""
    from scipy import ndimage
    out = labels.copy()
    vis = out >= 0
    for c in np.unique(out[vis]):
        lab, n = ndimage.label(out == c, structure=ndimage.generate_binary_structure(3, 3))
        for q in range(1, n + 1):
            region = lab == q
            if region.sum() >= min_region:
                continue
            ring = ndimage.binary_dilation(region, structure=ndimage.generate_binary_structure(3, 3)) & ~region & vis
            if not ring.any():
                continue
            vals, counts = np.unique(out[ring], return_counts=True)
            out[region] = vals[np.argmax(counts)]
    return out


def six_connect(occ: np.ndarray, weight: Optional[np.ndarray] = None, rounds: int = 4) -> Tuple[np.ndarray, int]:
    """Cells that touch only along an edge (a staircase one cell thick - an octagonal ring, a sheet at an angle) share
    no stud: fill one corner of every such pair (the one with more material), so the staircase holds. Returns (occ,
    cells added)."""
    occ = occ.copy()
    w = np.zeros(occ.shape, np.float32) if weight is None else np.asarray(weight, np.float32)
    shape = np.array(occ.shape)
    added = 0
    for _ in range(rounds):
        new = 0
        for a, b in ((0, 1), (0, 2), (1, 2)):
            for sb in (1, -1):
                da = np.zeros(3, int)
                da[a] = 1
                db = np.zeros(3, int)
                db[b] = sb
                d = da + db
                lo, hi = np.maximum(0, -d), shape - np.maximum(0, d)
                p = tuple(slice(x, y) for x, y in zip(lo, hi))
                q = tuple(slice(x + o, y + o) for x, y, o in zip(lo, hi, d))
                pa = tuple(slice(x + o, y + o) for x, y, o in zip(lo, hi, da))
                pb = tuple(slice(x + o, y + o) for x, y, o in zip(lo, hi, db))
                pair = occ[p] & occ[q] & ~occ[pa] & ~occ[pb]
                if not pair.any():
                    continue
                use_a = w[pa] >= w[pb]
                va, vb = occ[pa], occ[pb]
                va[pair & use_a] = True
                vb[pair & ~use_a] = True
                new += int(pair.sum())
        added += new
        if not new:
            break
    return occ, added


def base_layers(settings) -> int:
    """Layers left free under the model for a display base (artsy, base not off): one brick or two plates."""
    if getattr(settings, "style", "exact") != "artsy" or getattr(settings, "artsy_base", "auto") == "off":
        return 0
    return 2 if settings.unit == "plate" else 1


def add_base(occ, taken, pad: int, force: bool = False):
    """A display base in the `pad` free layers under the model when it stands on parts that are apart (or `force`):
    the rectangle under the lowest tenth of the model (one brick, or two plates - one layer of plates does not hold
    together by itself). Returns (occ, base mask or None)."""
    from scipy import ndimage
    if pad <= 0:
        return occ, None
    nz = occ.shape[2]
    every = occ | taken
    _lab, n = ndimage.label(every[:, :, pad])
    if n <= 1 and not force:
        return occ, None
    low = max(1, (nz - pad) // 10)
    ii, jj = np.nonzero(every[:, :, pad:pad + low].any(axis=2))
    if not len(ii):
        return occ, None
    base = np.zeros_like(occ)
    base[ii.min():ii.max() + 1, jj.min():jj.max() + 1, :pad] = True
    return occ | base, base


# --------------------------------------------------------------------------------------------- axle stacks


def radius_classes(upright: bool, off=()) -> Dict[float, List[str]]:
    """{radius: parts of that radius} - upright stacks only round plates and bricks 2x2 (studs on the grid); `off`:
    parts switched off (lego_engine.catalogues)."""
    out: Dict[float, List[str]] = {}
    for p in ON_AXLE:
        if (upright and p.key not in UPRIGHT_PARTS) or p.key in off:
            continue
        out.setdefault(p.radius, []).append(p.key)
    return out


def fill_run(keys: List[str], length: float) -> List[str]:
    """Parts (all of one radius) whose lengths add up closest to `length` with few parts: every mix of up to the three
    lengths the radius has, scored by the length missed or overshot (mm) plus one per part - a 16.5 mm stripe at 8 mm
    takes a round brick and two round plates (16.0), not two bricks (19.2: 2.7 mm too long, every stripe)."""
    import itertools
    by_len: Dict[float, str] = {}
    for k in keys:
        by_len.setdefault(PARTS[k].length, k)
    lens = sorted(by_len, reverse=True)[:3]
    best = None
    for counts in itertools.product(*[range(0, int(length // L) + 2) for L in lens]):
        n = sum(counts)
        if n == 0:
            continue
        total = sum(c * L for c, L in zip(counts, lens))
        score = abs(length - total) + 1.0 * n
        if best is None or score < best[0]:
            best = (score, counts)
    out: List[str] = []
    for c, L in zip(best[1], lens):
        out += [by_len[L]] * c
    return out


def stack_parts(profile: np.ndarray, colours: List[str], upright: bool, snap: float = 0.0,
                avail: Optional[Dict[str, tuple]] = None, off=()) -> List[Tuple[str, float, str]]:
    """The parts along an axle for a profile [(t, outer radius, ...)] with a colour per slice. The profile is cut into
    runs of one colour whose radius stays within RADIUS_TOLERANCE of the run's mean; each run gets the radius class
    with the best score - its parts (as few and as thick as fill the run) plus RADIUS_WEIGHT per unit of relative
    radius error - so a run of 64 mm at 12 mm takes three rims 30x20 (5.5), not 27 discs 3x3 (27). Offsets from the
    profile's start (mm); no part overlaps the one before it; `snap` (mm, upright stacks: the layer height) puts every
    part's start on a multiple of it, so studs meet the layers. `avail` {part: colours a shop sells it in}: only
    those parts in the run's colour (no entry: any colour). `off`: parts switched off (lego_engine.catalogues)."""
    classes = radius_classes(upright, off)
    if not classes:
        return []
    radii = np.array(sorted(classes))
    t = profile[:, 0]
    step = float(np.median(np.diff(t))) if len(t) > 1 else 2.0
    t_start = t[0] - step / 2
    out = []
    s = 0
    while s < len(t):
        e = s
        while e + 1 < len(t) and colours[e + 1] == colours[s] and \
                abs(profile[e + 1, 1] - profile[s:e + 1, 1].mean()) <= RADIUS_TOLERANCE * profile[s:e + 1, 1].mean():
            e += 1
        r = float(profile[s:e + 1, 1].mean())
        run = (t[e] - t[s]) + step
        best = None
        for rc in radii:
            err = abs(rc - r) / max(r, 1e-6)
            sold = [k for k in classes[rc] if not avail or k not in avail or colours[s] in avail[k]]
            if err > 2 * RADIUS_TOLERANCE or not sold:
                continue
            keys = fill_run(sold, run)
            score = len(keys) + RADIUS_WEIGHT * err
            if best is None or score < best[0]:
                best = (score, keys)
        if best is None:                                    # nothing near: the nearest radius
            best = (0, fill_run(classes[radii[int(np.argmin(np.abs(radii - r)))]], run))
        pos = t[s] - step / 2 - t_start
        end_all = t[-1] + step / 2 - t_start
        for k in best[1]:
            if out:
                prev_end = out[-1][1] + PARTS[out[-1][0]].length
                pos = prev_end if pos < prev_end + 1.5 * step else pos     # runs that meet close up, no slivers
            if pos >= end_all - 0.25 * PARTS[k].length:
                break                                                       # not beyond the body

            if snap:
                pos = np.ceil(pos / snap - 1e-6) * snap
            out.append((k, round(float(pos), 2), colours[s]))
            pos += PARTS[k].length
        s = e + 1
    return out


def merge_stacks(bodies: List[Body], grid, tol_cells: float = 1.0) -> List[List[Body]]:
    """Round bodies grouped by their axle: the same axis and a line within `tol_cells` cells."""
    groups: List[List[Body]] = []
    for b in sorted(bodies, key=lambda b: -(b.t1 - b.t0)):
        for g in groups:
            h = g[0]
            if h.axis == b.axis and np.hypot(h.line[0] - b.line[0], h.line[1] - b.line[1]) <= tol_cells * grid.cell[0]:
                g.append(b)
                break
        else:
            groups.append([b])
    return groups


def stack_profile(group: List[Body], step: float = 2.0):
    """The outer profile of bodies sharing an axle (the largest radius at each place along it) and the colour of the
    body that has it there."""
    t0 = min(b.t0 for b in group)
    t1 = max(b.t1 for b in group)
    n = max(2, int(round((t1 - t0) / step)))
    ts = np.linspace(t0 + step / 2, t1 - step / 2, n)
    r = np.zeros(n)
    col = [""] * n
    for b in group:
        p = b.profile
        inside = (ts >= p[0, 0] - step) & (ts <= p[-1, 0] + step)
        rb = np.interp(ts, p[:, 0], p[:, 1])
        better = inside & (rb > r)
        r[better] = rb[better]
        for q in np.nonzero(better)[0]:
            col[q] = b.colour or "light_bluish_grey"
    keep = r > 0
    return np.c_[ts[keep], r[keep], np.zeros(keep.sum())], [c for c, k in zip(col, keep) if k]


def _axial(grid, axis: int, line: Tuple[float, float]):
    """Per cell: its centre's place along the axle (t) and its distance from the axle line (mm)."""
    c = [grid.origin[q] + (np.arange(grid.shape[q]) + 0.5) * grid.cell[q] for q in range(3)]
    X, Y, Z = np.meshgrid(c[0], c[1], c[2], indexing="ij")
    P = (X, Y, Z)
    tr = [q for q in range(3) if q != axis]
    return P[axis], np.hypot(P[tr[0]] - line[0], P[tr[1]] - line[1])


def envelope(profile: np.ndarray, grid, axis: int, line, t0: float, t1: float) -> np.ndarray:
    """The cells inside the outline of a round body between t0 and t1 (its radius at the cell's place, plus half a
    cell): what a stack there replaces."""
    t, d = _axial(grid, axis, line)
    r = np.interp(t, profile[:, 0], profile[:, 1], left=0.0, right=0.0)
    half = 0.5 * float(max(grid.cell[q] for q in range(3) if q != axis))
    return (t >= t0) & (t <= t1) & (r > 0) & (d <= r + half)


def visible(profile: np.ndarray, grid, axis: int, line, others: np.ndarray, ring: float = 1.5) -> np.ndarray:
    """Per profile slice: whether the body shows there - fewer than half the cells of a ring just outside its
    outline (up to `ring` cells out) belong to other parts of the model."""
    t, d = _axial(grid, axis, line)
    cell = float(max(grid.cell[q] for q in range(3) if q != axis))
    tr = [q for q in range(3) if q != axis]
    area = float(grid.cell[tr[0]] * grid.cell[tr[1]])
    step = float(profile[1, 0] - profile[0, 0]) if len(profile) > 1 else cell
    out = np.zeros(len(profile), bool)
    for q, (tq, rq, _ri) in enumerate(profile):
        band = (np.abs(t - tq) <= max(step, grid.cell[axis]) / 2 + 1e-6) & (d > rq + 0.5 * cell) & (d <= rq + ring * cell)
        # the whole ring, also where it leaves the grid (empty there): its cells in one layer across the axle
        whole = np.pi * ((rq + ring * cell) ** 2 - (rq + 0.5 * cell) ** 2) / area
        out[q] = others[band].sum() < 0.5 * max(whole, band.sum() / max(1, len(np.unique(t[band]))))
    return out


def longest_run(mask: np.ndarray) -> Tuple[int, int]:
    best, s = (0, -1), None
    for q, m in enumerate(list(mask) + [False]):
        if m and s is None:
            s = q
        elif not m and s is not None:
            if q - s > best[1] - best[0] + 1:
                best = (s, q - 1)
            s = None
    return best


# --------------------------------------------------------------------------------------------- the plan


def prepare(mesh, placed: np.ndarray, grid, frac: np.ndarray, touched: Optional[np.ndarray], settings,
            unit: str = "brick", threshold: float = 0.5, say=lambda _t: None) -> Plan:
    """Read the part and decide what is built how (see the module docstring). `frac`, `touched`: the engine's fill
    fractions and surface cells on `grid`; `placed`: the input's vertices on the grid (mm)."""
    from .voxelize import occupancy, thin_cells
    faces = np.asarray(mesh.faces)
    fc = None
    vis = getattr(mesh, "visual", None)
    if vis is not None and getattr(vis, "kind", None) == "face":
        fc = np.asarray(vis.face_colors)[:, :3] / 255.0
    elif vis is not None and getattr(vis, "kind", None) == "vertex":
        fc = np.asarray(vis.vertex_colors)[faces][:, :, :3].mean(axis=1) / 255.0
    report: Dict = {"techniques": []}
    # symmetry
    mirrored = []
    if getattr(settings, "artsy_symmetry", "auto") != "off":
        scores = {ax: mirror_score(frac, q) for q, ax in enumerate("xyz") if ax != "z"}
        report["mirror_scores"] = {k: round(v, 3) for k, v in scores.items()}
        mirrored = [ax for ax, s in scores.items() if s >= SYMMETRY_MIN]
        if mirrored:
            frac, touched = symmetrise(frac, touched, ["xyz".index(a) for a in mirrored])
            report["techniques"].append("mirror symmetry " + "/".join(mirrored))
    occ = occupancy(frac, threshold)
    if touched is not None and settings.thin_walls != "off":
        occ = occ | thin_cells(occ, touched)
    # round bodies -> axle stacks
    assemblies: List[Assembly] = []
    rmax = max(p.radius for p in ON_AXLE)
    if getattr(settings, "artsy_axles", True):
        bodies = analyse(placed, faces, fc)
        rounds = [b for b in bodies if b.score <= ROUND_MAX_SCORE and b.profile is not None
                  and 3.0 <= b.profile[:, 1].max() <= rmax * (1 + RADIUS_TOLERANCE) and b.t1 - b.t0 >= 4.0]
        report["bodies"] = [{"name": b.name, "axis": "xyz"[b.axis], "roundness": round(b.score, 3),
                             "diameter_mm": round(2 * float(b.profile[:, 1].max()), 1) if b.profile is not None else None,
                             "length_mm": round(b.t1 - b.t0, 1)} for b in bodies]
        # no body counted twice: the whole only if nothing smaller is round, components and colours otherwise
        chosen = [b for b in rounds if b.name != "the part"] or [b for b in rounds if b.name == "the part"]
        chosen = _not_nested(chosen)
        built = np.zeros(grid.shape, bool)
        for group in merge_stacks(chosen, grid):
            profile, colours = stack_profile(group)
            if len(profile) < 2:
                continue
            b0 = group[0]
            env = envelope(profile, grid, b0.axis, b0.line, profile[0, 0] - 2, profile[-1, 0] + 2)
            show = visible(profile, grid, b0.axis, b0.line, occ & ~env)
            s, e = longest_run(show)
            if e - s + 1 < 2:
                continue
            profile, colours = profile[s:e + 1], colours[s:e + 1]
            colours = slice_colours(profile, b0.axis, b0.line, mesh, placed) or colours
            a = _stack(group, profile, colours, grid, unit, dict(getattr(settings, "technic_colours", ()) or ()),
                       off=set(getattr(settings, "parts_off", ()) or ()))
            if a is not None:
                a = _surface_colours(a, mesh, placed, dict(getattr(settings, "technic_colours", ()) or ()))
                assemblies.append(a)
                step = float(profile[1, 0] - profile[0, 0])
                built |= envelope(profile, grid, b0.axis, b0.line, profile[0, 0] - step / 2, profile[-1, 0] + step / 2)
        occ = occ & ~built
        if assemblies:
            report["techniques"].append(f"{len(assemblies)} axle stack(s)")
    taken = cells_mask(assemblies, grid.shape)
    occ = occ & ~taken
    occ, corners = six_connect(occ, frac)
    if corners:
        report["corner_cells"] = corners
    pad = base_layers(settings)
    base = None
    if pad:
        occ, base = add_base(occ, taken, pad, force=getattr(settings, "artsy_base", "auto") == "on")
        if base is not None:
            report["techniques"].append("display base")
    floor = 0 if (base is not None or not pad) else pad
    # posts under the supports of lying axles, down to the next cell below (or the floor)
    for a in assemblies:
        for i, j, k in a.supports:
            kk = k - 1
            while kk >= floor and not occ[i, j, kk] and not taken[i, j, kk]:
                occ[i, j, kk] = True
                kk -= 1
    # small detailed parts -> printed pieces
    printed = np.zeros(grid.shape, bool)
    if getattr(settings, "artsy_printed", True):
        cell = float(np.max(grid.cell[:2]))
        for b in analyse_components(placed, faces):
            if b.extent is not None and b.extent.max() <= DETAIL_CELLS * cell:
                lo = np.floor((placed[faces[b.faces]].reshape(-1, 3).min(0) - grid.origin) / grid.cell).astype(int)
                hi = np.floor((placed[faces[b.faces]].reshape(-1, 3).max(0) - grid.origin) / grid.cell).astype(int)
                lo, hi = np.maximum(lo, 0), np.minimum(hi, np.asarray(grid.shape) - 1)
                box = np.zeros(grid.shape, bool)
                box[lo[0]:hi[0] + 1, lo[1]:hi[1] + 1, lo[2]:hi[2] + 1] = True
                printed |= box & (frac > 0.02) & ~taken
        if printed.any():
            report["techniques"].append(f"{int(printed.sum())} printed cell(s)")
    occ = clean(occ & ~printed) | printed
    contacts = np.zeros(grid.shape, bool)
    for a in assemblies:
        for i, j, k in a.seat_cells():
            if k > 0:
                contacts[i, j, k - 1] = True
        for i, j, k in a.stud_cells():
            if k + 1 < grid.shape[2]:
                contacts[i, j, k + 1] = True
    return Plan(occupancy=occ, assemblies=assemblies, printed=printed, contacts=contacts, mirrored=tuple(mirrored),
                report=report, base=base)


def analyse_components(placed, faces) -> List[Body]:
    comps = components(placed, faces)
    if len(comps) <= 1 or len(comps) > 4096:
        return []
    out = []
    for q, c in enumerate(comps):
        pts = placed[faces[c]].reshape(-1, 3)
        out.append(Body(f"component {q}", c, extent=pts.max(axis=0) - pts.min(axis=0)))
    return out


def slice_colours(profile: np.ndarray, axis: int, line, mesh, placed: np.ndarray) -> Optional[List[str]]:
    """The input's brick colour around the axle at every slice of a profile (12 points on its outline), or None for an
    input without colours - so the parts follow stripes and bands, and are chosen in colours a shop sells."""
    from .palette import NAMES, snap_index, surface_colours
    tr = [q for q in range(3) if q != axis]
    ang = np.linspace(0, 2 * np.pi, 12, endpoint=False)
    pts = []
    for t, r, _ri in profile:
        p = np.zeros((12, 3))
        p[:, axis] = t
        p[:, tr[0]] = line[0] + r * np.cos(ang)
        p[:, tr[1]] = line[1] + r * np.sin(ang)
        pts.append(p)
    col = surface_colours(mesh, np.concatenate(pts), placed)
    if col is None:
        return None
    idx = snap_index(np.asarray(col, float)).reshape(len(profile), 12)
    return [NAMES[int(np.bincount(row).argmax())] for row in idx]


def _surface_colours(a: Assembly, mesh, placed: np.ndarray, avail: Optional[Dict[str, tuple]] = None) -> Assembly:
    """Each threaded part in the colour of the input's surface around it (the most common brick colour of 12 points
    on its rim at mid-length); an input without colours keeps the stack's colours."""
    from .assembly import frame
    from .palette import NAMES, snap_index, surface_colours
    m = frame(a)
    parts = []
    for key, off, colour in a.parts:
        t = PARTS[key]
        ang = np.linspace(0, 2 * np.pi, 12, endpoint=False)
        local = np.c_[t.radius * np.cos(ang), t.radius * np.sin(ang), np.full(12, off + t.length / 2)]
        pts = local @ m[:3, :3].T + m[:3, 3]
        col = surface_colours(mesh, pts, placed)
        if col is not None:
            idx = snap_index(np.asarray(col, float))
            seen = NAMES[int(np.bincount(idx).argmax())]
            if not avail or key not in avail or seen in avail[key]:
                colour = seen
        parts.append((key, off, colour))
    out = Assembly(**{**a.__dict__})
    out.parts = tuple(parts)
    return out


def _not_nested(bodies: List[Body]) -> List[Body]:
    """Drop a colour body when components inside it are chosen too (and the other way round): keep the larger set of
    faces per overlap."""
    out = []
    for b in sorted(bodies, key=lambda b: -len(b.faces)):
        if all(len(np.intersect1d(b.faces, o.faces)) < 0.2 * len(b.faces) for o in out):
            out.append(b)
    return out


def _stack(group: List[Body], profile: np.ndarray, colours: List[str], grid, unit: str,
           avail: Optional[Dict[str, tuple]] = None, off=()) -> Optional[Assembly]:
    """One axle stack for bodies that share an axle: the line snapped to the grid (a lying axle through cell centres at
    a support brick's hole height; an upright one on a cell corner, for 2x2 round parts, its parts on layer
    boundaries), the parts from the outer profile, support bricks just beyond both ends for a lying axle."""
    b0 = group[0]
    axis = b0.axis
    upright = axis == 2
    if len(profile) < 2 or (not upright and "support_1x1" in off):      # a lying axle needs its holders
        return None
    step = float(profile[1, 0] - profile[0, 0])
    t_start = float(profile[0, 0]) - step / 2                  # where the bodies begin along the axle (mm)
    h = float(grid.cell[2])
    parts = stack_parts(profile, colours, upright, snap=h if upright else 0.0, avail=avail, off=off)
    if not parts:
        return None
    if upright:
        line = tuple(float(grid.origin[q] + np.round((b0.line[q] - grid.origin[q]) / grid.cell[q]) * grid.cell[q])
                     for q in (0, 1))
        k0 = int(np.clip(np.round((t_start - grid.origin[2]) / h), 0, grid.shape[2] - 1))
        start = float(grid.origin[2] + k0 * h)
        span = max(o + PARTS[k].length for k, o, _c in parts)
        n = int(np.floor(span / 8.0 + 1e-6))                # the axle stays inside the stack
        axles = tuple(axles_for(n, off)) if n >= 2 else ()
        return place(Assembly(axis=2, line=line, start=start, axles=axles, parts=tuple(parts), name="stack"), grid)
    u = 1 - axis if axis < 2 else 0                            # the horizontal coordinate across a lying axle
    j = int(np.clip(np.floor((b0.line[0] - grid.origin[u]) / grid.cell[u]), 0, grid.shape[u] - 1))
    k = int(np.clip(round((b0.line[1] - grid.origin[2] - HOLE_Z) / h), 0, grid.shape[2] - 1))
    line = (float(grid.origin[u] + (j + 0.5) * grid.cell[u]), axle_height(k, grid))
    pitch = float(grid.cell[axis])
    c0 = int(np.floor((t_start - grid.origin[axis]) / pitch))       # the first cell of the bodies
    s0 = c0 - 1                                                     # a support brick just before it
    if s0 < 0:
        return None
    start = float(grid.origin[axis] + s0 * pitch)
    offset0 = max(t_start - start, pitch)                           # the parts begin after the first support
    parts = [(key, round(o + offset0, 2), c) for key, o, c in parts]
    end = max(o + PARTS[key].length for key, o, _c in parts)
    s1 = int(np.ceil((start + end - grid.origin[axis]) / pitch - 1e-6))   # the cell after the last part
    if s1 >= grid.shape[axis]:
        return None
    supports = []
    for s in (s0, s1):
        idx = [0, 0, 0]
        idx[axis], idx[u], idx[2] = s, j, k
        supports.append(tuple(idx))
    axles = tuple(axles_for(s1 - s0 + 1, off))
    if not axles:                                                   # no axle left that spans it
        return None
    a = Assembly(axis=axis, line=line, start=start, axles=axles, parts=tuple(parts),
                 supports=tuple(supports), support_layers=3 if unit == "plate" else 1, name="stack")
    return place(a, grid)


# --------------------------------------------------------------------------------------------- the craft report


def craft(model, brick_colours, plan: Plan, st: Dict) -> Dict:
    """What artsy mode reports instead of chasing the deviation: the techniques used, the mirror planes, the parts
    (stud-up, Technic, printed), how many part types and colours, how many single-colour regions the visible bricks
    form (colour blocking: fewer is calmer), the share of studs that show."""
    from scipy import ndimage
    from .palette import snap_index
    out = {"techniques": list(plan.report.get("techniques", [])), "mirror": list(plan.mirrored),
           "assemblies": len(model.assemblies), "printed": sum(1 for b in model.bricks if b.is_special)}
    technic = {}
    for a in model.assemblies:
        for (key, _c), n in a.part_counts().items():
            technic[key] = technic.get(key, 0) + n
    out["technic_parts"] = sum(technic.values())
    out["parts"] = len(model.bricks) + out["technic_parts"]
    out["part_types"] = len(model.counts()) + len(technic)
    if brick_colours is not None and len(model.bricks):
        idx = snap_index(np.asarray(brick_colours, float))
        out["colours"] = int(len(np.unique(idx)))
        cell_col = np.full(model.ids.shape, -1, np.int32)
        occ = model.ids >= 0
        cell_col[occ] = idx[model.ids[occ]]
        regions = 0
        for c in np.unique(idx):
            _lab, n = ndimage.label(cell_col == c, structure=ndimage.generate_binary_structure(3, 1))
            regions += n
        out["colour_regions"] = int(regions)
    studs = int(model.studs.sum())
    out["studs_showing"] = round(st.get("studs_visible", 0) / studs, 3) if studs else 0.0
    out["deviation"] = st.get("deviation")
    return out
