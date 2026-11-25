"""Technic parts for artsy mode: axles and the parts with a cross hole that thread onto them, and the bricks with a
cross hole that hold an axle lying on its side - real elements, bought, not printed.

Every part is described along its axle: `radius` (mm, outer) and `length` (mm along the axle); `fixed` parts have a
cross hole (they turn with the axle and stay where they are), the others a round hole (they need a bush beside them).
Design numbers are LEGO Pick a Brick's (the public listing, 2026-11-24 - some differ from BrickLink's: a 2M axle is
32062 there, a bush 42798). Sizes are LEGO geometry where it is fixed (axle cross 4.8 mm, plate 3.2, brick 9.6, a
Technic hole 5.8 mm above the bottom of its brick); thicknesses marked "approx." are near enough for the picture and
the spacing along the axle, not for printing (these parts are bought).

    PARTS[key] -> TechnicPart
    axles_for(length_studs) -> [axle keys and connectors] covering that span, shortest first
    part_mesh(key, system) -> (vertices, faces): the part in its own frame - the axle along +z from z = 0, centred on
                                x = y = 0 (a support brick: its footprint from the origin, bottom at z = 0)
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Dict, List, Tuple

import numpy as np

AXLE_CROSS = 4.8                  # across the cross, mm
AXLE_ARM = 1.8                    # width of an arm of the cross
HOLE_Z = 5.8                      # a Technic hole's centre above the bottom of its brick


@dataclass(frozen=True)
class TechnicPart:
    key: str
    name: str                     # what the parts list says
    designs: Tuple[str, ...]      # Pick a Brick design numbers (the first is preferred)
    role: str                     # "axle", "connector", "on_axle", "support"
    radius: float                 # mm, outer (support: half the footprint's side)
    length: float                 # mm along the axle (axle: its length; support: brick height)
    fixed: bool = True            # a cross hole (False: a round hole - it spins and needs a bush beside it)
    shape: str = "cylinder"       # display: cylinder, bush, gear, rim, axle, box
    teeth: int = 0
    studs: int = 0                # round plates and bricks: the side of their footprint (2, 4, 6) - studs on the
                                  # face where the stud grid falls inside the circle
    studs_l: int = 0              # axle length in studs (axles and connectors)


def _parts() -> Dict[str, TechnicPart]:
    p: List[TechnicPart] = []
    for n, d in ((2, "32062"), (3, "4519"), (4, "3705"), (5, "32073"), (6, "3706"), (7, "44294"), (8, "3707"),
                 (9, "60485"), (10, "3737"), (11, "23948"), (12, "3708"), (16, "69732"), (32, "13927")):
        p.append(TechnicPart(f"axle_{n}", f"axle {n}L", (d,), "axle", AXLE_CROSS / 2, 8.0 * n, shape="axle",
                             studs_l=n))
    p += [
        TechnicPart("axle_joiner_2", "axle connector 2L", ("59443",), "connector", 3.0, 16.0, studs_l=2),
        TechnicPart("bush", "bush", ("42798",), "on_axle", 3.9, 7.8, shape="bush"),
        TechnicPart("half_bush", "half bush", ("42136",), "on_axle", 3.6, 3.9, shape="bush"),
        TechnicPart("round_plate_2x2", "round plate 2x2 with axle hole", ("4032",), "on_axle", 7.9, 3.2, studs=2),
        TechnicPart("round_brick_2x2", "round brick 2x2 with axle hole", ("6143",), "on_axle", 7.9, 9.6, studs=2),
        TechnicPart("round_brick_2x2_grille", "round brick 2x2 with grille and axle hole", ("92947",), "on_axle", 7.9,
                    9.6, studs=2, shape="grille"),
        # larger round parts have a round (pin) hole: on an upright axle their studs hold them
        TechnicPart("round_plate_4x4", "round plate 4x4 with hole", ("60474",), "on_axle", 15.9, 3.2, fixed=False,
                    studs=4),
        TechnicPart("round_brick_4x4", "round brick 4x4 with hole", ("87081",), "on_axle", 15.9, 9.6, fixed=False,
                    studs=4),
        TechnicPart("round_plate_6x6", "round plate 6x6 with hole", ("11213",), "on_axle", 23.9, 3.2, fixed=False,
                    studs=6),
        TechnicPart("disc_3x3", "Technic disc 3x3", ("2723",), "on_axle", 12.0, 2.4),               # thickness approx.
        TechnicPart("gear_8", "gear 8 teeth", ("10928",), "on_axle", 5.0, 4.0, shape="gear", teeth=8),     # approx.
        TechnicPart("gear_16", "gear 16 teeth", ("94925",), "on_axle", 9.0, 4.0, shape="gear", teeth=16),
        TechnicPart("gear_24", "gear 24 teeth", ("24505",), "on_axle", 13.0, 4.0, shape="gear", teeth=24),
        TechnicPart("gear_40", "gear 40 teeth", ("34432",), "on_axle", 21.0, 4.0, shape="gear", teeth=40),
        TechnicPart("rim_18x7", "rim 18x7 with axle hole", ("56903",), "on_axle", 9.0, 7.0, shape="rim"),
        TechnicPart("rim_18x14", "rim 18x14 with axle hole", ("55982",), "on_axle", 9.0, 14.0, shape="rim"),
        TechnicPart("rim_30x20", "rim 30x20 with axle hole", ("42716", "56145"), "on_axle", 15.0, 20.0, shape="rim"),
        TechnicPart("rim_56x13", "rim 56x13 with axle hole", ("7877",), "on_axle", 28.0, 13.0, shape="rim"),
        TechnicPart("rim_56x34", "rim 56x34 with axle hole", ("49294",), "on_axle", 28.0, 34.0, shape="rim"),
        TechnicPart("support_1x1", "brick 1x1 with axle hole", ("73230",), "support", 4.0, 9.6, shape="box"),
    ]
    return {x.key: x for x in p}


PARTS: Dict[str, TechnicPart] = _parts()
AXLES = sorted((x for x in PARTS.values() if x.role == "axle"), key=lambda x: x.studs_l)
ON_AXLE = [x for x in PARTS.values() if x.role == "on_axle"]


def axles_for(n: int) -> List[str]:
    """Axles (and 2L connectors between them) for a span of n studs: one axle of exactly n if there is one, else the
    fewest axles of the stock lengths joined end to end (a connector between two, its 2 studs inside the span)."""
    n = int(n)
    by_len = {x.studs_l: x.key for x in AXLES}
    if n in by_len:
        return [by_len[n]]
    if n < min(by_len):
        return [by_len[min(by_len)]]
    best = None
    for a in sorted(by_len, reverse=True):
        rest = n - a
        if rest in by_len and (best is None or a > best[0]):
            best = (a, rest)
    if best:
        return [by_len[best[0]], "axle_joiner_2", by_len[best[1]]]
    longest = max(a for a in by_len if a <= n)
    return [by_len[longest], "axle_joiner_2"] + axles_for(n - longest)


def standard_colour(key: str) -> str:
    """LEGO's colour for an axle of that length (even: black, odd: light bluish grey, as sold), a connector grey."""
    t = PARTS[key]
    if t.role == "axle":
        return "black" if t.studs_l % 2 == 0 else "light_bluish_grey"
    return "light_bluish_grey"


# ------------------------------------------------------------------------------------------------- display meshes


def _mesh(m) -> Tuple[np.ndarray, np.ndarray]:
    out = m.to_mesh()
    return np.asarray(out.vert_properties)[:, :3].astype(float), np.asarray(out.tri_verts).astype(np.int64)


@lru_cache(maxsize=None)
def part_mesh(key: str, stud_diameter: float = 4.8, stud_height: float = 1.7) -> Tuple[np.ndarray, np.ndarray]:
    """The display mesh of a part in its own frame (see the module docstring), cached."""
    import manifold3d as mf
    t = PARTS[key]
    r, L = t.radius, t.length

    def cyl(rad, h, z0=0.0, seg=48):
        return mf.Manifold.cylinder(h, rad, circular_segments=seg).translate((0, 0, z0))

    if t.shape == "axle":
        arm = AXLE_ARM
        cs = mf.CrossSection.batch_boolean([mf.CrossSection.square((AXLE_CROSS, arm), center=True),
                                            mf.CrossSection.square((arm, AXLE_CROSS), center=True)], mf.OpType.Add)
        return _mesh(mf.Manifold.extrude(cs, L))
    if t.shape == "box":                                   # a support brick: 1x1, the hole along x
        body = mf.Manifold.cube((8.0 - 0.2, 8.0 - 0.2, L)).translate((0.1, 0.1, 0))
        stud = cyl(stud_diameter / 2, stud_height, L, 24).translate((4.0, 4.0, 0))
        hole = mf.Manifold.cylinder(8.0 + 0.2, 2.4, circular_segments=32).rotate((0, 90, 0)).translate((-0.1, 4.0, HOLE_Z))
        return _mesh((body - hole) + stud)
    if t.shape == "gear":
        n = t.teeth
        pts = []
        for q in range(n * 4):
            a = 2 * np.pi * q / (n * 4)
            rr = r if q % 4 in (1, 2) else r - 1.6
            pts.append((rr * np.cos(a), rr * np.sin(a)))
        body = mf.Manifold.extrude(mf.CrossSection([pts]), L)
        return _mesh(body + cyl(min(r - 1.0, 5.5), L + 0.6, -0.3))
    if t.shape == "rim":                                   # tyre seat ring, hub, spokes
        ring = cyl(r, L) - cyl(r - 2.2, L + 2, -1)
        hub = cyl(4.2, L)
        spokes = [mf.Manifold.cube((r - 3.0, 1.6, L * 0.6)).translate((2.0, -0.8, L * 0.2)).rotate((0, 0, 72 * q))
                  for q in range(5)]
        return _mesh(ring + hub + mf.Manifold.batch_boolean(spokes, mf.OpType.Add))
    if t.shape == "bush":
        flange = cyl(r, min(1.2, L / 3))
        return _mesh(flange + cyl(r * 0.8, L))
    body = cyl(r, L)
    if t.shape == "grille":
        ribs = [mf.Manifold.cube((0.8, 1.2, L * 0.7)).translate((r - 0.2, -0.6, L * 0.15)).rotate((0, 0, 360 * q / 16))
                for q in range(16)]
        body = body - mf.Manifold.batch_boolean(ribs, mf.OpType.Add)
    if t.studs:
        grid = [(a - (t.studs - 1) / 2) * 8.0 for a in range(t.studs)]
        studs = [cyl(stud_diameter / 2, stud_height, L, 24).translate((x, y, 0)) for x in grid for y in grid
                 if np.hypot(x, y) <= r - stud_diameter / 2 - 0.3]
        body = body + mf.Manifold.batch_boolean(studs, mf.OpType.Add)
    return _mesh(body)
