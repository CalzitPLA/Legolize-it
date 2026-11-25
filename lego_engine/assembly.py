"""Sub-assemblies on an axle (artsy mode): an axle along x, y or z with parts threaded on it (lego_engine.technic),
held - when the axle lies on its side - by bricks with an axle hole that sit in the stud-up model.

An Assembly is one rigid body: parts with a cross hole cannot turn or slide on their axle once bushes and neighbours
close the row, so `analysis.connectivity` treats it as one node, joined to the model where its support bricks sit on
studs (their bottom cells) and carry something (their top cells). An upright axle (axis z) has no supports: its round
plates and bricks are stud-up parts, joined through their own bottom and top cells.

The cells an assembly takes (`cells`) are left empty by the layout - the parts' cylinders and the support bricks.

Frames: model space is the grid's (mm). The axle line runs along `axis` through the point `line` (the other two
coordinates, mm); `start` is where its first axle begins along the axis (mm). A part's `offset` is its start along
the axle from `start`.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np

from .technic import HOLE_Z, PARTS, part_mesh


@dataclass
class Assembly:
    axis: int                                   # 0, 1, 2: the axle along x, y, z
    line: Tuple[float, float]                   # the axle line's other two coordinates (mm): (y, z), (x, z) or (x, y)
    start: float                                # where the first axle begins along the axis (mm)
    axles: Tuple[str, ...]                      # axles and connectors, end to end from `start`
    parts: Tuple[Tuple[str, float, str], ...]   # (part key, offset along the axle from start (mm), palette colour)
    supports: Tuple[Tuple[int, int, int], ...] = ()   # support bricks (bottom cell i, j, k) - brick 1x1 with axle hole
    support_layers: int = 1                     # layers a support brick spans: 1 (brick layers) or 3 (plate layers)
    axle_colour: str = "auto"                   # "auto": each axle in LEGO's colour for its length (technic)
    support_colour: str = "dark_bluish_grey"
    cells: Tuple[Tuple[int, int, int], ...] = ()   # the cells it takes (set by `place`)
    name: str = ""

    @property
    def length(self) -> float:
        return float(sum(PARTS[k].length for k in self.axles))

    @property
    def mount_layer(self) -> int:
        """The layer in which it goes in: its lowest support (an upright stack: its lowest cell)."""
        if self.supports:
            return min(k for _i, _j, k in self.supports)
        return min((k for _i, _j, k in self.cells), default=0)

    def part_counts(self) -> Dict[Tuple[str, str], int]:
        """{(part key, colour): count} - axles, connectors, threaded parts and support bricks."""
        out: Dict[Tuple[str, str], int] = {}
        for k in self.axles:
            c = self.colour_of_axle(k)
            out[(k, c)] = out.get((k, c), 0) + 1
        for k, _o, c in self.parts:
            out[(k, c)] = out.get((k, c), 0) + 1
        if self.supports:
            out[("support_1x1", self.support_colour)] = out.get(("support_1x1", self.support_colour), 0) + len(self.supports)
        return out

    def colour_of_axle(self, key: str) -> str:
        from .technic import standard_colour
        return standard_colour(key) if self.axle_colour == "auto" else self.axle_colour

    def seat_cells(self) -> List[Tuple[int, int, int]]:
        """Cells whose underside sits on a stud below: the support bricks' bottoms; an upright stack (round plates and
        bricks 2x2, stud-up): its lowest cells."""
        if self.axis == 2 and not self.supports and self.cells:
            low = min(k for _i, _j, k in self.cells)
            return [c for c in self.cells if c[2] == low]
        return [(i, j, k) for i, j, k in self.supports]

    def stud_cells(self) -> List[Tuple[int, int, int]]:
        """Cells with a stud on top that something above can sit on: the support bricks' tops; an upright stack: its
        highest cells."""
        if self.axis == 2 and not self.supports and self.cells:
            top = max(k for _i, _j, k in self.cells)
            return [c for c in self.cells if c[2] == top]
        return [(i, j, k + self.support_layers - 1) for i, j, k in self.supports]

    def to_dict(self) -> Dict:
        d = asdict(self)
        d["cells"] = [list(c) for c in self.cells]
        return d

    @classmethod
    def from_dict(cls, d: Dict) -> "Assembly":
        d = dict(d)
        d["line"] = tuple(d["line"])
        d["axles"] = tuple(d["axles"])
        d["parts"] = tuple(tuple(p) for p in d["parts"])
        d["supports"] = tuple(tuple(s) for s in d.get("supports", ()))
        d["cells"] = tuple(tuple(c) for c in d.get("cells", ()))
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


def frame(a: Assembly) -> np.ndarray:
    """4x4: a part's own frame (the axle along +z from 0, centred on x = y = 0) -> model space at offset 0."""
    m = np.eye(4)
    if a.axis == 0:                                       # local z -> x, local x -> y, local y -> z
        m[:3, :3] = [[0, 0, 1], [1, 0, 0], [0, 1, 0]]
        m[:3, 3] = (a.start, a.line[0], a.line[1])
    elif a.axis == 1:                                     # local z -> y, local x -> z, local y -> x
        m[:3, :3] = [[0, 1, 0], [0, 0, 1], [1, 0, 0]]
        m[:3, 3] = (a.line[0], a.start, a.line[1])
    else:
        m[:3, 3] = (a.line[0], a.line[1], a.start)
    return m


def _placed(a: Assembly, key: str, offset: float, stud_diameter=4.8, stud_height=1.7):
    v, f = part_mesh(key, stud_diameter, stud_height)
    m = frame(a)
    v = (v + np.array([0.0, 0.0, offset])) @ m[:3, :3].T + m[:3, 3]
    return v, f


def meshes(a: Assembly, grid, system=None) -> List[Tuple[np.ndarray, np.ndarray, str]]:
    """Display meshes [(vertices, faces, palette colour)] of the axles, the threaded parts and the support bricks."""
    sd = getattr(system, "stud_diameter", 4.8)
    sh = getattr(system, "stud_height", 1.7)
    out = []
    pos = 0.0
    for k in a.axles:
        v, f = _placed(a, k, pos, sd, sh)
        out.append((v, f, a.colour_of_axle(k)))
        pos += PARTS[k].length
    for k, off, colour in a.parts:
        v, f = _placed(a, k, off, sd, sh)
        out.append((v, f, colour))
    if a.supports:
        v0, f = part_mesh("support_1x1", sd, sh)                   # the hole along x: turn it for an axle along y
        h = float(grid.cell[2])
        for i, j, k in a.supports:
            v = v0.copy()
            if a.axis == 1:
                v = np.c_[8.0 - v[:, 1], v[:, 0], v[:, 2]]
            v = v + grid.origin + np.array([i * grid.cell[0], j * grid.cell[1], k * h])
            out.append((v, f, a.support_colour))
    return out


def axle_height(k: int, grid) -> float:
    """Where an axle lies (model z, mm) held by support bricks whose bottom is in layer k."""
    return float(grid.origin[2] + k * grid.cell[2] + HOLE_Z)


def place(a: Assembly, grid) -> Assembly:
    """The cells the assembly takes: every cell its parts' cylinders (and the axles) reach into, and the support
    bricks' cells. Returns a copy with `cells` set."""
    nx, ny, nz = grid.shape
    cell = np.asarray(grid.cell, float)
    taken = set()
    ax = a.axis
    tr = [q for q in range(3) if q != ax]
    centres = [grid.origin[q] + (np.arange(grid.shape[q]) + 0.5) * cell[q] for q in range(3)]
    items = [(k, pos) for k, pos in _axle_positions(a)] + [(k, off) for k, off, _c in a.parts]
    for key, off in items:
        t = PARTS[key]
        s0, s1 = a.start + off, a.start + off + t.length
        lo = int(np.floor((s0 - grid.origin[ax]) / cell[ax] + 1e-6))
        hi = int(np.ceil((s1 - grid.origin[ax]) / cell[ax] - 1e-6))
        along = range(max(0, lo), min(grid.shape[ax], hi))
        # the cells whose box comes within the radius of the axle line, in the plane across it
        c0, c1 = centres[tr[0]], centres[tr[1]]
        d0 = np.maximum(0.0, np.abs(c0 - a.line[0]) - cell[tr[0]] / 2)
        d1 = np.maximum(0.0, np.abs(c1 - a.line[1]) - cell[tr[1]] / 2)
        near = np.argwhere(d0[:, None] ** 2 + d1[None, :] ** 2 < (t.radius - 0.05) ** 2)
        for u in along:
            for p, q in near:
                idx = [0, 0, 0]
                idx[ax], idx[tr[0]], idx[tr[1]] = u, p, q
                taken.add(tuple(int(x) for x in idx))
    for i, j, k in a.supports:
        for q in range(a.support_layers):
            if 0 <= k + q < nz:
                taken.add((i, j, k + q))
    cells = tuple(sorted(c for c in taken if 0 <= c[0] < nx and 0 <= c[1] < ny and 0 <= c[2] < nz))
    out = Assembly(**{**a.__dict__})
    out.cells = cells
    return out


def _axle_positions(a: Assembly):
    pos = 0.0
    for k in a.axles:
        yield k, pos
        pos += PARTS[k].length


def cells_mask(assemblies, shape) -> np.ndarray:
    m = np.zeros(shape, bool)
    for a in assemblies or ():
        for i, j, k in a.cells:
            m[i, j, k] = True
    return m
