"""Buying the bricks: BlueBrixx part packs - the catalogue, the cheapest packs for a build, the cheapest brick catalogue.

BlueBrixx (bluebrixx.com) sells part packs: one part in one colour, e.g. "BRICK 2X4 X 50" (4.95 EUR) or an XL pack
"BRICK 1X2 X 300" (19.95 EUR). A pack's colour is only on its product page (itemprop "mpn": BPP<design>-<colour>,
BrickLink-like names: light_bluish_gray, medium_dark_flesh, ...), and the in-stock brick and plate packs are marked
"Available exclusively online" (2026-11: not in the stores). Every part of a build costs at least one whole pack, so
the bill is driven by how many (part, colour) kinds a build needs, much less by how many bricks.

- `crawl()` reads the catalogue of standard bricks and plates (1x1 .. 2x8): the listings of partpacks/bricks and
  partpacks/plates, then the head of each standard pack's product page (colour, stock, the online note). One request a
  second; robots.txt allows these pages. Saved as JSON (`CACHE`); a crawl that stops resumes where it was.
- `needs(result)`, `needs_from_json(path)`, `needs_from_manual(tex)`: what a build needs,
  {(unit, part, palette colour): count} - unit "brick" or "plate", part "2x4" or a shaped piece's label.
- `price({model: needs}, catalogue)` -> `Order`: for each part the cheapest combination of in-stock packs
  (`cheapest`), the parts without a pack (shaped pieces; sizes or colours the shop lacks), the total, shipping.
- `cheapest_sizes(legolizer, settings, catalogue)`: the brick catalogue (settings.sizes) with the cheapest order at
  the same grid - same cells and resolution, other bricks. Greedy: drop the size whose removal lowers the cost most,
  while one does and the build holds together no worse (groups, floating). 1x1 always stays (it fills any cell).

The order list itself (LaTeX/PDF, Markdown, CSV; English or German) is lego_engine.export_order.
"""
from __future__ import annotations

import html
import json
import math
import re
import time
import urllib.request
from collections import defaultdict
from dataclasses import asdict, dataclass, field, replace
from datetime import date
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Tuple

import numpy as np

SHOP = "https://www.bluebrixx.com"
LISTINGS = {"brick": "/en/partpacks/bricks/", "plate": "/en/partpacks/plates/"}
CACHE = Path.home() / ".cache" / "legolize" / "bluebrixx" / "catalogue.json"
AGENT = "Mozilla/5.0 (X11; Linux x86_64) Gecko/20100101 Firefox/128.0"
SHIPPING, FREE_FROM = 5.95, 99.0          # Germany, EUR (the shop's shipping page, 2026-11)
NO_PACK = (100.0, 0.5)                    # what the search charges for a part no pack covers: per part, per piece
SUBSTITUTE = (1.0, 25.0)                  # ... for a part bought in the nearest other colour; the farthest (CIELAB)
STANDARD = re.compile(r"^(BRICK|PLATE) (\d+)X(\d+) X (\d+)$")
SIZES = {"1x1", "1x2", "1x3", "1x4", "1x6", "1x8", "2x2", "2x3", "2x4", "2x6", "2x8"}
HEAD_BYTES = 240_000                      # a product page's colour and stock are within its first ~225 kB

# palette names (lego_engine.palette) -> the shop's colour names to try, after the name itself and grey -> gray
SHOP_COLOURS: Dict[str, Tuple[str, ...]] = {
    "light_grey": ("light_gray", "light_bluish_gray"), "dark_grey": ("dark_gray", "dark_bluish_gray"),
    "medium_nougat": ("medium_dark_flesh",), "nougat": ("flesh", "medium_dark_flesh"),
    "light_nougat": ("light_flesh",), "azure": ("medium_azure", "dark_azure"), "medium_blue": ("bright_light_blue",),
    "light_yellow": ("bright_light_yellow",), "cream": ("light_yellow", "bright_light_yellow"),
    "lavender": ("medium_lavender",),
}


# ------------------------------------------------------------------------------------------------ the catalogue

@dataclass(frozen=True)
class Pack:
    item: str                 # the shop's item number (what its search and the store staff look up)
    name: str                 # "BRICK 2X4 X 50"
    unit: str                 # "brick" or "plate"
    size: str                 # "2x4"
    colour: str               # the shop's colour name
    pieces: int
    price: float              # EUR
    url: str
    in_stock: bool
    online_only: bool


@dataclass
class Catalogue:
    packs: List[Pack]
    date: str = ""

    def __post_init__(self):
        self.index: Dict[Tuple[str, str, str], List[Pack]] = defaultdict(list)
        for p in self.packs:
            if p.in_stock:
                self.index[(p.unit, p.size, p.colour)].append(p)

    @classmethod
    def load(cls, path=CACHE) -> "Catalogue":
        """From crawl()'s JSON ({"date", "products"}, or a bare list of product records)."""
        data = json.loads(Path(path).read_text())
        records = data["products"] if isinstance(data, dict) else data
        when = data.get("date", "") if isinstance(data, dict) else ""
        return cls([p for p in (pack_from_record(r) for r in records) if p is not None],
                   when or date.fromtimestamp(Path(path).stat().st_mtime).isoformat())

    def find(self, unit: str, part: str, colour: str) -> Tuple[Optional[str], List[Pack]]:
        """(the shop's colour, its in-stock packs) for a palette colour; (None, []) when none is sold."""
        for name in shop_colour_names(colour):
            packs = self.index.get((unit, part, name))
            if packs:
                return name, packs
        return None, []

    def nearest(self, unit: str, part: str, colour: str) -> Tuple[Optional[str], Optional[str], List[Pack]]:
        """(palette colour, shop colour, packs) of the colour nearest to `colour` (CIELAB, at most SUBSTITUTE[1]) in
        which the shop sells this part - for a part it does not sell in the colour itself (1x1 bricks: every layout
        needs some); (None, None, []) when nothing is near."""
        from .palette import PALETTE, to_lab
        here = to_lab(np.asarray(PALETTE[colour], float)) if colour in PALETTE else None
        best = (None, None, [], SUBSTITUTE[1])
        if here is None:
            return best[:3]
        for name in PALETTE:
            if name == colour:
                continue
            shop, packs = self.find(unit, part, name)
            if packs:
                d = float(np.linalg.norm(to_lab(np.asarray(PALETTE[name], float)) - here))
                if d < best[3]:
                    best = (name, shop, packs, d)
        return best[:3]


def shop_colour_names(colour: str) -> List[str]:
    names = [colour, colour.replace("grey", "gray"), *SHOP_COLOURS.get(colour, ())]
    return list(dict.fromkeys(names))


def pack_from_record(r: Dict) -> Optional[Pack]:
    """A standard brick or plate pack from a crawled product record, else None."""
    m = STANDARD.match(r.get("name") or "")
    mpn = r.get("mpn") or ""
    if not m or "-" not in mpn:
        return None
    a, b = sorted((int(m.group(2)), int(m.group(3))))
    return Pack(item=str(r["item"]), name=r["name"], unit=m.group(1).lower(), size=f"{a}x{b}",
                colour=mpn.split("-", 1)[1], pieces=int(m.group(4)), price=float(r["price"]), url=r["url"],
                in_stock=r.get("availability") == "InStock", online_only=bool(r.get("online_only")))


def parse_listing(page: str) -> List[Dict]:
    """The product cards of a listing page: name, price, item, url, pieces, badges."""
    out = []
    for card in re.split(r'class="card product-box', page)[1:]:
        m = re.search(r'data-product-information="([^"]+)"', card)
        if not m:
            continue
        info = json.loads(html.unescape(m.group(1)))
        url = re.search(r'href="(https://www\.bluebrixx\.com/en/prod/(\d+)/[^"]+)"', card)
        pcs = re.search(r'esbb-product-box-pieces-txt">\s*([\d.,]+)', card)
        out.append({"name": info.get("name"), "price": info.get("price"), "item": url.group(2) if url else None,
                    "url": url.group(1) if url else None, "pieces": pcs.group(1) if pcs else None,
                    "badges": [b.strip() for b in re.findall(r'class="badge[^"]*"[^>]*>\s*([^<]+?)\s*<', card)
                               if b.strip()]})
    return out


def listing_pages(page: str) -> int:
    return max([1] + [int(n) for n in re.findall(r"[?&]p=(\d+)", page)])


def parse_product(head: str) -> Dict:
    """Colour code (mpn), availability and the online-only note from the head of a product page."""
    mpn = re.search(r'itemprop="mpn"\s+content="([^"]*)"', head)
    av = re.search(r'itemprop="availability"\s+href="https?://schema.org/(\w+)"', head)
    ox = re.search(r'class="esbb-pdp-online-exclusive[^"]*">(.{0,3000}?)</div>', head, re.S)   # after an SVG icon
    note = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", ox.group(1))).strip() if ox else ""
    return {"mpn": mpn.group(1) if mpn else None, "availability": av.group(1) if av else None,
            "online_only": note or None}


def _get(url: str, limit: Optional[int] = None) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": AGENT})
    with urllib.request.urlopen(req, timeout=60) as r:
        return (r.read(limit) if limit else r.read()).decode("utf-8", "replace")


def crawl(path=CACHE, delay: float = 1.0, progress: Optional[Callable[[str], None]] = None, refresh: bool = False,
          fetch: Callable[..., str] = _get) -> Path:
    """Read the shop's standard brick and plate packs into `path` (see the module docstring). Products already in
    `path` are kept unless `refresh`; it is saved every 10 products."""
    say = progress or (lambda _t: None)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    old = {} if refresh or not path.exists() else {r["item"]: r for r in json.loads(path.read_text()).get("products", [])}
    cards = []
    for unit, listing in LISTINGS.items():
        first = fetch(SHOP + listing)
        pages = listing_pages(first)
        say(f"{unit}s: {pages} listing pages")
        for p in range(1, pages + 1):
            page = first if p == 1 else fetch(f"{SHOP}{listing}?p={p}")
            cards += parse_listing(page)
            time.sleep(delay)

    def wanted(c):
        m = STANDARD.match(c["name"] or "")
        return bool(m and c["item"] and f"{min(int(m.group(2)), int(m.group(3)))}x"
                    f"{max(int(m.group(2)), int(m.group(3)))}" in SIZES)
    todo = {c["item"]: c for c in cards if wanted(c)}
    records = dict(old)

    def save():
        path.write_text(json.dumps({"date": date.today().isoformat(), "shop": SHOP, "products": list(records.values())},
                                   indent=0))
    say(f"{len(todo)} standard packs, {len([i for i in todo if i not in old])} to read")
    for k, (item, c) in enumerate(todo.items()):
        if item in old:
            continue
        try:
            records[item] = dict(c, **parse_product(fetch(c["url"], HEAD_BYTES)))
        except Exception as e:                                         # a page that fails: noted, read next time
            say(f"{c['name']} ({item}): {e}")
        if k % 10 == 0:
            save()
            say(f"{k} / {len(todo)}")
        time.sleep(delay)
    save()
    return path


# ------------------------------------------------------------------------------------------------ what a build needs

Needs = Dict[Tuple[str, str, str], int]


def needs(result) -> Needs:
    """{(unit, part, palette colour): count} of a LegoResult (special pieces are printed: left out)."""
    from .palette import NAMES, snap_index
    idx = snap_index(np.asarray(result.colours(), float))
    out: Needs = defaultdict(int)
    for b, k in zip(result.model.bricks, idx):
        if not b.is_special:
            out[(result.settings.unit, b.name, NAMES[k])] += 1
    return dict(out)


def needs_from_json(path) -> Needs:
    """The same from a result.json (LegoResult.to_dict)."""
    from .palette import NAMES, snap_index
    from .shapes import label
    from .system import size_name
    d = json.loads(Path(path).read_text())
    unit = d["settings"]["unit"]
    out: Needs = defaultdict(int)
    for (_L, _i, _j, w, dd), kind, k in zip(d["bricks"], d["kinds"], snap_index(np.asarray(d["colours"], float))):
        if kind != "special":
            out[(unit, label(kind, w, dd) if kind else size_name(w, dd), NAMES[k])] += 1
    return dict(out)


def needs_from_manual(path) -> Needs:
    """The same from a building manual's LaTeX (lego_engine.export_manual): its parts list and unit."""
    tex = Path(path).read_text(encoding="utf-8")
    unit = "plate" if re.search(r"Layers & \d+ \(plates", tex) else "brick"
    m = re.search(r"\\section\*\{Parts\}(.*?)\\end\{longtable\}", tex, re.S)
    out: Needs = defaultdict(int)
    for line in (m.group(1).splitlines() if m else []):
        r = re.match(r"\s*([^&\\]+?)\s*&\s*\\swatch\{\w+\}\\ ([^&]+?)\s*&\s*(\d+)\s*\\\\", line)
        if r and not r.group(1).startswith("special"):
            out[(unit, r.group(1).strip(), r.group(2).strip().replace(" ", "_").replace("~", ""))] += int(r.group(3))
    return dict(out)


def needs_from_file(path) -> Needs:
    return needs_from_manual(path) if str(path).endswith(".tex") else needs_from_json(path)


# ------------------------------------------------------------------------------------------------ the order

def cheapest(packs: List[Pack], n: int) -> Tuple[List[Tuple[Pack, int]], float]:
    """The cheapest packs (with repeats) holding at least n pieces: ([(pack, how many)], cost)."""
    if not packs or n <= 0:
        return [], (0.0 if n <= 0 else math.inf)
    top = n + max(p.pieces for p in packs)
    best = [0.0] + [math.inf] * top
    pick = [-1] * (top + 1)
    for q in range(1, top + 1):
        for i, p in enumerate(packs):
            c = best[max(0, q - p.pieces)] + p.price
            if c < best[q] - 1e-9:
                best[q], pick[q] = c, i
    count: Dict[int, int] = defaultdict(int)
    q = n
    while q > 0:
        count[pick[q]] += 1
        q = max(0, q - packs[pick[q]].pieces)
    return [(packs[i], k) for i, k in sorted(count.items())], round(best[n], 2)


@dataclass
class Line:
    unit: str
    part: str
    colour: str               # palette name
    shop_colour: str
    need: int
    pack: Pack
    packs: int
    per_model: Dict[str, int] = field(default_factory=dict)
    instead_of: str = ""      # bought in another colour: the palette colour the model has (the shop lacks it here)
    plates_per_brick: int = 1  # 3: a brick bought as three plates of its size and colour (stacked: one brick high)

    @property
    def cost(self) -> float:
        return round(self.packs * self.pack.price, 2)


@dataclass
class Missing:
    unit: str
    part: str
    colour: str
    need: int
    why: str                  # "shaped" (no standard pack), "unsold" (no pack of this size and colour in stock)
    per_model: Dict[str, int] = field(default_factory=dict)


@dataclass
class Order:
    lines: List[Line]
    missing: List[Missing]
    models: List[Tuple[str, str, int]]       # (name, unit, parts)
    catalogue_date: str = ""

    @property
    def parts_cost(self) -> float:
        return round(sum(x.cost for x in self.lines), 2)

    @property
    def shipping(self) -> float:
        return 0.0 if self.parts_cost >= FREE_FROM else SHIPPING

    @property
    def packs(self) -> int:
        return sum(x.packs for x in self.lines)

    def search_cost(self) -> float:
        """The parts' cost plus NO_PACK for the parts no pack covers (what cheapest_sizes minimises): any order the
        shop can fill beats one it cannot."""
        return round(self.parts_cost + sum(NO_PACK[0] + NO_PACK[1] * m.need for m in self.missing)
                     + SUBSTITUTE[0] * len({(x.unit, x.part, x.colour) for x in self.lines
                                            if x.instead_of or x.plates_per_brick > 1}), 2)

    def to_dict(self) -> Dict:
        return {"catalogue_date": self.catalogue_date, "models": self.models, "parts_cost": self.parts_cost,
                "shipping": self.shipping, "packs": self.packs,
                "lines": [dict(asdict(x), cost=x.cost) for x in self.lines], "missing": [asdict(x) for x in self.missing]}


def price(models: Dict[str, Needs], catalogue: Catalogue) -> Order:
    """One order for the builds in `models` ({name: needs}): packs shared, the cheapest mix per part."""
    total: Needs = defaultdict(int)
    for nd in models.values():
        for key, n in nd.items():
            total[key] += n
    lines, missing = [], []
    for (unit, part, colour), n in sorted(total.items()):
        per = {name: nd[(unit, part, colour)] for name, nd in models.items() if (unit, part, colour) in nd}
        if not re.fullmatch(r"\d+x\d+", part):
            missing.append(Missing(unit, part, colour, n, "shaped", per))
            continue
        shop_colour, packs = catalogue.find(unit, part, colour)
        instead, buy, buy_unit, each = "", colour, unit, 1
        if not packs and unit == "brick":                               # three plates of its size: one brick high
            shop_colour, packs = catalogue.find("plate", part, colour)
            buy_unit, each = ("plate", 3) if packs else (unit, 1)
        if not packs:                                                   # the nearest colour the shop has in this part
            buy, shop_colour, packs = catalogue.nearest(unit, part, colour)
            instead = colour
        choice, _cost = cheapest(packs, n * each)
        if not choice:
            missing.append(Missing(unit, part, colour, n, "unsold", per))
            continue
        lines += [Line(buy_unit, part, buy, shop_colour, n * each, p, k, {m: c * each for m, c in per.items()},
                       instead, each) for p, k in choice]
    return Order(lines, missing, [(name, next(iter(nd))[0] if nd else "", sum(nd.values()))
                                  for name, nd in models.items()], catalogue.date)


# ------------------------------------------------------------------------------------------------ the cheapest catalogue

def cheapest_sizes(legolizer, settings, catalogue: Catalogue, progress: Optional[Callable[[str], None]] = None,
                   max_parts: Optional[int] = None):
    """(settings with the cheapest brick sizes per colour, its LegoResult, its Order, the trace [(sizes, cost,
    bricks)]). Same grid, same cells; the voxelization is cached by the Legolizer: every trial is a new layout only.

    Per colour (settings.colour_sizes - the layout uses a size in a colour only where allowed): it starts with the
    sizes the shop sells in that colour (an order the shop can fill), then greedily drops a size from a colour, or all
    sizes a colour does not use at once, while that lowers the order's price. A global catalogue is not enough: a size
    used in six colours is six packs, and a single-size drop stalls (without 1x8 the layout turned to 1x3s the shop
    lacked). No step may make the build hold together worse (groups, floating parts) than the first, nor take it past
    `max_parts` bricks (fewer sizes mean more, smaller bricks). Without source colours (one colour mode for all) the
    same search runs on the global catalogue."""
    say = progress or (lambda _t: None)
    trace: List[Tuple[str, float, int]] = []
    tried: Dict[Tuple, Tuple] = {}
    unit = settings.unit
    every = tuple((min(a, b), max(a, b)) for a, b in settings.sizes)

    def key_of(cs):
        return tuple(sorted((c, tuple(sorted(set(v) | {(1, 1)}))) for c, v in cs.items()))

    def name(cs):
        return "; ".join(f"{c}: " + ",".join(f"{a}x{b}" for a, b in v) for c, v in key_of(cs))

    def trial(cs):
        k = key_of(cs)
        if k not in tried:
            if per_colour:
                union = tuple(sorted({x for _c, v in k for x in v}, key=lambda t: (-t[0] * t[1], t)))
                st = replace(settings, sizes=union, colour_sizes=k)
            else:
                st = replace(settings, sizes=k[0][1])
            r = legolizer.run(st)
            o = price({"model": needs(r)}, catalogue)
            trace.append((name(cs), o.search_cost(), r.stats["bricks"]))
            tried[k] = (st, r, o, (r.stats["groups"], r.stats["floating"]), dict(k))
        return tried[k]

    def fits(c):
        return c[3][0] <= limit[0] and c[3][1] <= limit[1] and (max_parts is None or c[1].stats["bricks"] <= max_parts)

    def used(c, colour):
        return {(min(b.w, b.d), max(b.w, b.d)) for b, n in zip(c[1].model.bricks, needs_colours(c[1]))
                if b.is_brick and n == colour}

    first = legolizer.run(replace(settings, colour_sizes=()))
    colours = sorted({c for (_u, _p, c) in needs(first)})
    per_colour = first.source_colours is not None and len(colours) > 1
    if per_colour:
        sold = {c: {x for x in every if catalogue.find(unit, f"{x[0]}x{x[1]}", c)[1]} for c in colours}
        best = trial(sold)
    else:
        best = trial({"*": set(every)})
    limit = (max(best[3][0], first.stats["groups"]), max(best[3][1], first.stats["floating"]))
    say(f"start: {best[2].search_cost():.2f} EUR, {best[1].stats['bricks']} bricks")
    while True:
        cs = best[4]
        moves = []
        for c, v in cs.items():
            moves += [dict(cs, **{c: tuple(x for x in v if x != drop)}) for drop in v if drop != (1, 1)]
            if per_colour:
                only = tuple(x for x in v if x in used(best, c) or x == (1, 1))
                if len(only) < len(v):
                    moves.append(dict(cs, **{c: only}))
        ok = [t for t in map(trial, moves) if fits(t)]
        t = min(ok, key=lambda t: (t[2].search_cost(), t[1].stats["bricks"]), default=None)
        if t is None or t[2].search_cost() >= best[2].search_cost() - 1e-9:
            break
        best = t
        say(f"-> {best[2].search_cost():.2f} EUR, {best[1].stats['bricks']} bricks ({name(best[4])})")
    return best[0], best[1], best[2], trace


def needs_colours(result) -> List[str]:
    """The palette colour of every brick of a result (as needs() counts them)."""
    from .palette import NAMES, snap_index
    return [NAMES[k] for k in snap_index(np.asarray(result.colours(), float))]


# ------------------------------------------------------------------------------------------------ command line

def main(argv=None) -> int:
    """python3 -m lego_engine.shop crawl | order NAME=result.json|manual.tex ... [--lang de] [--out DIR]"""
    import argparse
    import sys
    ap = argparse.ArgumentParser(prog="legolize-it shop", description="BlueBrixx part packs for Legolize-it builds")
    sub = ap.add_subparsers(dest="what", required=True)
    c = sub.add_parser("crawl", help="read the shop's standard brick and plate packs (about 10-20 minutes)")
    c.add_argument("--catalogue", default=str(CACHE))
    c.add_argument("--delay", type=float, default=1.0, help="seconds between requests (default 1)")
    c.add_argument("--refresh", action="store_true", help="read every product again (prices, stock)")
    o = sub.add_parser("order", help="one order list for builds: NAME=result.json or NAME=manual.tex")
    o.add_argument("models", nargs="+")
    o.add_argument("--catalogue", default=str(CACHE))
    o.add_argument("--lang", choices=("de", "en"), default="de")
    o.add_argument("--out", default="order")
    o.add_argument("--title", default=None)
    o.add_argument("--no-pdf", action="store_true")
    args = ap.parse_args(argv)
    say = lambda t: print(t, file=sys.stderr)  # noqa: E731
    if args.what == "crawl":
        print(crawl(args.catalogue, args.delay, say, args.refresh))
        return 0
    if not Path(args.catalogue).exists():
        print(f"no catalogue at {args.catalogue}: run `python3 -m lego_engine.shop crawl` first", file=sys.stderr)
        return 2
    from .export_order import write_order
    models = {}
    for m in args.models:
        name, _, path = m.rpartition("=") if "=" in m else (Path(m).parent.name, "", m)
        models[name] = needs_from_file(path)
    order = price(models, Catalogue.load(args.catalogue))
    res = write_order(order, args.out, args.lang, args.title, pdf=not args.no_pdf, progress=say)
    print(json.dumps({**res, "parts_cost": order.parts_cost, "shipping": order.shipping, "packs": order.packs,
                      "missing": len(order.missing)}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
