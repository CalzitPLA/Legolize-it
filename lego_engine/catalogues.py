"""The parts catalogue: every part Legolize it can lay, and the makers' ranges a model can be limited to.

    GROUPS(unit)                    every part, grouped: [(group, [(id, label), ...]), ...]
    CATALOGUES[name]                Catalogue(name, label, url, note): standard (print your own), lego, bluebrixx
    available(name, unit)           the ids in that range
    restrict(name, unit, off=())    (sizes, parts_off) for LegoSettings: the brick sizes to use, and every other part
                                    left out - outside the range, or switched off by hand (`off`)

Ids: a brick / plate size "2x4"; a piece type of lego_engine.shapes ("tile_4x2", "bar_5", "round_1", "curve_2x1");
a Technic part of lego_engine.technic ("axle_8", "gear_24", "round_brick_2x2", "support_1x1"). The engine follows
`LegoSettings.parts_off` everywhere a part is chosen: shaped pieces, tiles, rods, bars, axle stacks.

The makers' ranges are kept to the facts Legolize it needs - which sizes and part types exist - with no names,
pictures, prices or descriptions of theirs: for those, and for colours and stock, their websites (`url`). The LEGO
range is the one read at LEGO Pick a Brick on 2026-11-24 (sizes and part types only); BlueBrixx's comes from the
part-pack catalogue (lego_engine.shop: an own `shop crawl`, else the bundled snapshot), which covers standard bricks
and plates only - other BlueBrixx parts are not in the data, see the website. Shaped pieces (slopes, curves, round
corners, domes) are Legolize it's own shapes, printed; they are not checked against a maker's range.
"""
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Set, Tuple

from .shapes import BAR_LENGTHS, CATALOGUE as SHAPES, FAMILIES, TILE_SIZES

NOTICE = ("LEGO\u00ae and Technic\u2122 are trademarks of the LEGO Group, which does not sponsor, authorise or endorse "
          "this software. BlueBrixx\u2122 is a trademark of its owner, who is not affiliated with this software either. "
          "Part numbers only identify parts; for parts, colours, prices and stock see the makers' websites.")
# (R) only for LEGO, a registered trademark for certain; (TM) for the others - (R) on a mark that is not registered
# can itself be misleading (in Germany: UWG), (TM) only says it is a trademark. TRADEMARKS.md says the same at length.


@dataclass(frozen=True)
class Catalogue:
    name: str
    label: str
    url: Optional[str]
    note: str


CATALOGUES: Dict[str, Catalogue] = {
    "standard": Catalogue("standard", "Standard", None,
                          "every part Legolize it can lay - to print yourself (the STL stack); bought Technic "
                          "parts are listed apart"),
    "lego": Catalogue("lego", "LEGO", "https://www.lego.com/pick-and-build/pick-a-brick",
                      "the sizes and part types LEGO sells single (Pick a Brick, as read 2026-11-24); prices, "
                      "colours and stock on the website"),
    "bluebrixx": Catalogue("bluebrixx", "BlueBrixx", "https://www.bluebrixx.com",
                           "the brick and plate sizes in BlueBrixx's part packs (the shop catalogue: an own crawl, "
                           "else the bundled snapshot); other BlueBrixx parts are not in the data - see the website"),
}

# brick / plate sizes Legolize it offers (short side first)
SIZES: Tuple[Tuple[int, int], ...] = (
    (1, 1), (1, 2), (1, 3), (1, 4), (1, 6), (1, 8), (1, 10), (1, 12), (1, 16),
    (2, 2), (2, 3), (2, 4), (2, 6), (2, 8), (2, 10), (2, 12),
    (4, 4), (4, 6), (4, 8), (4, 10), (4, 12), (6, 6), (6, 8), (6, 10), (6, 12), (8, 8))

# LEGO Pick a Brick, 2026-11-24: the sizes sold single (any colour) - facts only
_LEGO_BRICKS = {"1x1", "1x2", "1x3", "1x4", "1x6", "1x8", "1x10", "1x12", "1x16", "2x2", "2x3", "2x4", "2x6", "2x8",
                "2x10"}
_LEGO_PLATES = {"1x1", "1x2", "1x3", "1x4", "1x6", "1x8", "1x10", "1x12", "2x2", "2x3", "2x4", "2x6", "2x8", "2x10",
                "2x12", "4x4", "4x6", "4x8", "4x10", "4x12", "6x6", "6x8", "6x10", "6x12", "8x8"}
_BLUEBRIXX_FALLBACK = {"1x1", "1x2", "1x3", "1x4", "1x6", "1x8", "2x2", "2x3", "2x4", "2x6", "2x8"}


def size_id(a: int, b: int) -> str:
    return f"{min(a, b)}x{max(a, b)}"


def _technic() -> List[Tuple[str, str, str]]:
    from .technic import PARTS
    return [(k, p.name, p.role) for k, p in PARTS.items()]


def GROUPS(unit: str = "plate") -> List[Tuple[str, List[Tuple[str, str]]]]:
    """Every part Legolize it can lay, grouped for a list with a checkbox each."""
    kind = "plate" if unit == "plate" else "brick"
    out = [(f"{kind.capitalize()}s", [(size_id(a, b), f"{kind} {a}x{b}") for a, b in SIZES])]
    out.append(("Tiles", [(f"tile_{L}x{W}", f"tile {W}x{L}") for L, W in TILE_SIZES]))
    out.append(("Rods and bars", [("round_1", f"round {kind} 1x1 (rods)")] +
                [(f"bar_{L}", f"bar {L - 1}L on two clips (over {L} studs)") for L in BAR_LENGTHS]))
    fam_label = {"slope": "Slopes", "curve": "Curved slopes", "round": "Round corners and round bricks",
                 "dome": "Dome corners", "inverted": "Inverted slopes and curves"}
    for fam in FAMILIES:
        items = [(t.key, t.label) for t in SHAPES if t.family == fam and t.key != "round_1"]
        if items:
            out.append((f"Shaped: {fam_label.get(fam, fam)}", items))
    tech = _technic()
    out.append(("Technic: on the axle", [(k, n) for k, n, r in tech if r == "on_axle"]))
    out.append(("Technic: axles and connectors", [(k, n) for k, n, r in tech if r in ("axle", "connector")]))
    out.append(("Technic: holders", [(k, n) for k, n, r in tech if r not in ("on_axle", "axle", "connector")]))
    return [(g, items) for g, items in out if items]


def all_ids(unit: str = "plate") -> List[str]:
    return [i for _g, items in GROUPS(unit) for i, _l in items]


def _bluebrixx_sizes(unit: str) -> Set[str]:
    try:
        from .shop import Catalogue as ShopCatalogue
        cat = ShopCatalogue.load()
        got = {p.size for p in cat.packs if p.unit == unit and p.size.replace("x", "").isdigit()}
        return {size_id(*map(int, s.split("x"))) for s in got} or set(_BLUEBRIXX_FALLBACK)
    except Exception:
        return set(_BLUEBRIXX_FALLBACK)


def available(name: str, unit: str = "plate") -> Set[str]:
    """The ids in the range `name` (CATALOGUES) for bricks or plates."""
    ids = all_ids(unit)
    if name in (None, "", "standard"):
        return set(ids)
    if name not in CATALOGUES:
        raise ValueError(f"unknown catalogue {name!r}: {', '.join(CATALOGUES)}")
    if name == "lego":
        sizes = _LEGO_PLATES if unit == "plate" else _LEGO_BRICKS
        tiles = {f"tile_{L}x{W}" for L, W in TILE_SIZES}
        bars = {f"bar_{L}" for L in BAR_LENGTHS}
        technic = {k for k, _n, _r in _technic()}
        return sizes | tiles | bars | {"round_1"} | technic
    return _bluebrixx_sizes(unit)                       # bluebrixx: bricks and plates only (the data has no more)


def restrict(name: str, unit: str = "plate", off: Iterable[str] = ()) -> Tuple[Tuple[Tuple[int, int], ...],
                                                                             Tuple[str, ...]]:
    """(sizes, parts_off) for LegoSettings: the sizes in the range and not switched off (1x1 always: it fills any
    cell), and every other part that is outside the range or switched off."""
    avail, off = available(name, unit), set(off)
    sizes = tuple(s for s in SIZES if s == (1, 1) or (size_id(*s) in avail and size_id(*s) not in off))
    size_ids = {size_id(*s) for s in SIZES}
    parts_off = tuple(i for i in all_ids(unit) if i not in size_ids and (i not in avail or i in off))
    return sizes, parts_off


def parse_parts(text) -> Tuple[str, ...]:
    """'gear_24, tile_4x2' / ['bar_7'] / 'none' -> a tuple of known ids (any unit)."""
    if text is None:
        return ()
    items = [x.strip() for x in (text.split(",") if isinstance(text, str) else text) if str(x).strip()]
    if items in (["none"], [""]):
        return ()
    known = set(all_ids("plate")) | set(all_ids("brick"))
    bad = [x for x in items if x not in known]
    if bad:
        raise ValueError(f"unknown parts {bad}: ids like 2x4, tile_4x2, bar_5, round_1, curve_2x1, gear_24 "
                         f"(legolize-it cli --list-parts)")
    return tuple(dict.fromkeys(items))
