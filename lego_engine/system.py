"""The brick system: every dimension of a brick, in mm, and the catalogue of brick sizes.

The defaults are the common 8 mm stud-and-tube system: a stud pitch of 8.0, a plate 3.2 high, a brick three plates
(9.6), studs of diameter 4.8 and height 1.7, and 0.1 of play per side between neighbouring bricks (a 1x1 is 7.8 wide).
The underside of a hollow brick has walls 1.2 thick, a top 1.0 thick, tubes of 6.51 / 4.8 diameter between the studs
of bricks two or more wide and a post of 3.2 under one-wide bricks. Printers differ: `socket_clearance` (the radial play
of a printed socket around a stud) is the number to tune first.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from typing import Dict, Iterable, Tuple

# (short side, long side) in studs; a brick of size (a, b) can be laid along x or along y
DEFAULT_SIZES: Tuple[Tuple[int, int], ...] = (
    (1, 1), (1, 2), (1, 3), (1, 4), (1, 6), (1, 8),
    (2, 2), (2, 3), (2, 4), (2, 6), (2, 8),
)
UNITS = ("brick", "plate")


@dataclass(frozen=True)
class BrickSystem:
    pitch: float = 8.0               # stud spacing = the cell size in x and y
    plate_height: float = 3.2
    plates_per_brick: int = 3
    stud_diameter: float = 4.8
    stud_height: float = 1.7
    clearance: float = 0.1           # per side between neighbouring bricks
    wall: float = 1.2                # hollow underside: wall thickness
    top: float = 1.0                 # hollow underside: thickness of the top
    tube_outer: float = 6.51         # hollow underside: tubes of bricks >= 2 wide
    tube_inner: float = 4.8
    post_diameter: float = 3.2       # hollow underside: posts of one-wide bricks
    socket_clearance: float = 0.15   # printed slabs: radial play of a socket around a stud
    segments: int = 32               # facets of round parts in exported solids

    @property
    def brick_height(self) -> float:
        return self.plate_height * self.plates_per_brick

    def layer_height(self, unit: str) -> float:
        """Height of one layer of the grid: a brick (9.6) or a plate (3.2)."""
        if unit == "brick":
            return self.brick_height
        if unit == "plate":
            return self.plate_height
        raise ValueError(f"unit must be one of {UNITS}, not {unit!r}")

    def to_dict(self) -> Dict[str, float]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict) -> "BrickSystem":
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in (d or {}).items() if k in known})


def size_name(w: int, d: int) -> str:
    """'2x4' for a brick 2 x 4 studs in either orientation (short side first)."""
    a, b = sorted((int(w), int(d)))
    return f"{a}x{b}"


def parse_sizes(text: str) -> Tuple[Tuple[int, int], ...]:
    """'1x1, 1x2, 2x4' -> ((1, 1), (1, 2), (2, 4)); each size short side first, duplicates removed."""
    out = []
    for part in text.replace(";", ",").split(","):
        part = part.strip().lower()
        if not part:
            continue
        a, b = part.split("x")
        s = tuple(sorted((int(a), int(b))))
        if min(s) < 1:
            raise ValueError(f"brick size {part!r} must be at least 1x1")
        if s not in out:
            out.append(s)
    return tuple(out)


def normalise_sizes(sizes: Iterable[Tuple[int, int]], max_length: int = 0) -> Tuple[Tuple[int, int], ...]:
    """The catalogue as used by the layout: short side first, sorted, at most `max_length` long (0 = no limit), and
    always with the 1x1, so every occupied cell can be covered."""
    out = {tuple(sorted((int(a), int(b)))) for a, b in sizes}
    if max_length:
        out = {s for s in out if s[1] <= max_length}
    out.add((1, 1))
    return tuple(sorted(out, key=lambda s: (s[0] * s[1], s)))
