"""The brick system: every dimension of a brick, in mm, and the catalogue of brick sizes.

The defaults are the common 8 mm stud-and-tube system: a stud pitch of 8.0, a plate 3.2 high, a brick three plates
(9.6), studs of diameter 4.8 and height 1.7, and 0.1 of play per side between neighbouring bricks (a 1x1 is 7.8 wide).

The fit of a stud in what sits on it is one number, the stud play `socket_clearance` (radial, per side; 0.15): every
part that receives a stud is sized from it, so all of them hold alike (`fit()` lists the sizes and the gaps):

    wall    = pitch / 2 - stud radius - clearance - play      1.35  (the inside of the walls touches a stud's side)
    tube    = 2 (pitch / sqrt 2 - stud radius - play)          6.21  (a tube between four studs, bricks >= 2 wide)
    post    = 2 (pitch / 2 - stud radius - play)               2.90  (a post between two studs, one-wide bricks)
    socket  = stud diameter + 2 play, stud height + 0.2 deep   5.10  (printed slabs, shaped and special pieces)

At play 0 these are the molded system's numbers (tube 6.51, post 3.2). `wall`, `tube_outer`, `post_diameter` > 0 fix a
size by hand instead (results saved before 2026-11-12 carry wall 1.2, tube 6.51, post 3.2: 0.3 of play at the walls,
none at the tubes - the two did not agree). Printers differ: the play is the number to tune first.
"""
from __future__ import annotations

import math
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
    wall: float = 0.0                # hollow underside: wall thickness (0: from the stud play)
    top: float = 1.0                 # hollow underside: thickness of the top
    tube_outer: float = 0.0          # hollow underside: tubes of bricks >= 2 wide (0: from the stud play)
    tube_inner: float = 4.8
    post_diameter: float = 0.0       # hollow underside: posts of one-wide bricks (0: from the stud play)
    socket_clearance: float = 0.15   # the stud play: radial play of a stud in a socket, between walls, tubes, posts
    segments: int = 32               # facets of round parts in exported solids

    @property
    def brick_height(self) -> float:
        return self.plate_height * self.plates_per_brick

    @property
    def stud_play(self) -> float:
        return self.socket_clearance

    @property
    def wall_thickness(self) -> float:
        if self.wall > 0:
            return self.wall
        return self.pitch / 2 - self.stud_diameter / 2 - self.clearance - self.socket_clearance

    @property
    def tube_diameter(self) -> float:
        if self.tube_outer > 0:
            return self.tube_outer
        return 2 * (self.pitch / math.sqrt(2) - self.stud_diameter / 2 - self.socket_clearance)

    @property
    def post_d(self) -> float:
        if self.post_diameter > 0:
            return self.post_diameter
        return 2 * (self.pitch / 2 - self.stud_diameter / 2 - self.socket_clearance)

    @property
    def socket_diameter(self) -> float:
        return self.stud_diameter + 2 * self.socket_clearance

    @property
    def socket_depth(self) -> float:
        return self.stud_height + 0.2

    def fit(self) -> Dict[str, float]:
        """The sizes that receive a stud and the radial gap each leaves around it (mm)."""
        r = self.stud_diameter / 2
        return {
            "stud_diameter": self.stud_diameter, "stud_height": self.stud_height, "stud_play": self.socket_clearance,
            "side_clearance": self.clearance, "wall": round(self.wall_thickness, 4),
            "tube_outer": round(self.tube_diameter, 4), "post_diameter": round(self.post_d, 4),
            "socket_diameter": round(self.socket_diameter, 4), "socket_depth": round(self.socket_depth, 4),
            "gap_wall": round(self.pitch / 2 - r - self.clearance - self.wall_thickness, 4),
            "gap_tube": round(self.pitch / math.sqrt(2) - r - self.tube_diameter / 2, 4),
            "gap_post": round(self.pitch / 2 - r - self.post_d / 2, 4),
            "gap_socket": round(self.socket_diameter / 2 - r, 4),
        }

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


def name_area(name: str) -> int:
    """The footprint of a parts-list name: '2x4' -> 8, 'curve 3x1' -> 3, 'special 1x2' -> 2."""
    try:
        a, b = name.split()[-1].split("x")
        return int(a) * int(b)
    except ValueError:
        return 0


def name_sort_key(name: str):
    """Parts-list order: bricks first, biggest first; then the pieces by name."""
    return (" " in name, -name_area(name), name)


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
