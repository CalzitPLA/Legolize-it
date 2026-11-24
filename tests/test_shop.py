"""BlueBrixx part packs (lego_engine.shop, export_order): parsing the shop's pages, the cheapest packs, the order, the
cheapest brick catalogue - all on a made-up catalogue (no network)."""
import json
from dataclasses import replace

import pytest

from lego_engine.export_order import write_order
from lego_engine.pipeline import LegoSettings, Legolizer
from lego_engine.shop import (Catalogue, Pack, cheapest, cheapest_sizes, crawl, needs, needs_from_json,
                              needs_from_manual, parse_listing, parse_product, price)
from lego_engine.system import DEFAULT_SIZES


def pack(size, colour, pieces, price_, unit="brick", item=None, stock=True):
    return Pack(item or f"{unit}-{size}-{colour}-{pieces}", f"{unit.upper()} {size.upper()} X {pieces}", unit, size,
                colour, pieces, price_, "https://www.bluebrixx.com/en/prod/1/x/", stock, True)


def test_cheapest_mixes_packs():
    small, xl = pack("1x2", "red", 50, 4.95), pack("1x2", "red", 300, 19.95)
    assert cheapest([small, xl], 51) == ([(small, 2)], 9.9)
    assert cheapest([small, xl], 250) == ([(xl, 1)], 19.95)
    assert cheapest([small, xl], 301) == ([(small, 1), (xl, 1)], 24.9)
    assert cheapest([], 3)[1] == float("inf")


def test_pages_are_read():
    card = ('<div class="card product-box box-standard" data-product-information="{&quot;name&quot;:&quot;BRICK 2X4 X'
            ' 50&quot;,&quot;price&quot;:4.95}"><span class="esbb-product-box-pieces-txt"> 50 </span>'
            '<a href="https://www.bluebrixx.com/en/prod/600123/brick-2x4-x-50/" class="btn-light">Details</a></div>')
    (c,) = parse_listing("<html>" + card + '<a href="?p=3">3</a></html>')
    assert (c["name"], c["price"], c["item"], c["pieces"]) == ("BRICK 2X4 X 50", 4.95, "600123", "50")
    head = ('<meta itemprop="mpn"\n      content="BPP3001-light_bluish_gray">'
            '<link itemprop="availability" href="https://schema.org/InStock">'
            '<div class="esbb-pdp-online-exclusive"><svg><path d="' + "M1 2" * 200 + '"></path></svg>\n'
            ' Available exclusively online\n</div>')                    # the note comes after a long icon
    assert parse_product(head) == {"mpn": "BPP3001-light_bluish_gray", "availability": "InStock",
                                   "online_only": "Available exclusively online"}


def test_crawl_reads_and_resumes(tmp_path):
    listing = ('<div class="card product-box" data-product-information="{&quot;name&quot;:&quot;{n}&quot;,'
               '&quot;price&quot;:4.95}"><a href="https://www.bluebrixx.com/en/prod/{i}/x/">D</a></div>')
    pages = {"/en/partpacks/bricks/": listing.replace("{n}", "BRICK 1X2 X 50").replace("{i}", "11")
             + listing.replace("{n}", "BRICK 1X10 X 20").replace("{i}", "12"),        # 1x10: not a size we use
             "/en/partpacks/plates/": listing.replace("{n}", "PLATE 2X2 X 100").replace("{i}", "13")}
    seen = []

    def fetch(url, limit=None):
        seen.append(url)
        if "/prod/" in url:
            return '<meta itemprop="mpn" content="BPP3004-white"><link itemprop="availability" ' \
                   'href="https://schema.org/InStock">'
        return pages[url.split("bluebrixx.com")[1]]
    path = crawl(tmp_path / "cat.json", delay=0, fetch=fetch)
    cat = Catalogue.load(path)
    assert sorted((p.unit, p.size, p.colour) for p in cat.packs) == [("brick", "1x2", "white"), ("plate", "2x2", "white")]
    n = len(seen)
    crawl(path, delay=0, fetch=fetch)                                   # again: the products are not read again
    assert not any("/prod/" in u for u in seen[n:])


@pytest.fixture(scope="module")
def two_result(parts):
    return Legolizer().run(LegoSettings(input=str(parts["two"]), size_mode="scale", size=1.0))


def test_order_of_a_build(two_result, tmp_path):
    nd = needs(two_result)
    assert {c for (_u, _p, c) in nd} == {"red", "blue"} and all(u == "brick" for (u, _p, _c) in nd)
    two_result.save_json(tmp_path / "result.json")
    assert needs_from_json(tmp_path / "result.json") == nd
    sold = [pack(p, c, 50, 4.95) for (_u, p, c) in nd]
    order = price({"two": nd}, Catalogue(sold[1:], "2026-11-24"))   # the first part is not sold
    assert len(order.missing) == 1 and order.missing[0].why == "unsold"
    assert order.packs == len(nd) - 1 and order.parts_cost == round(4.95 * (len(nd) - 1), 2)
    res = write_order(order, tmp_path / "order", "de", pdf=False)
    tex = open(res["tex"], encoding="utf-8").read()
    assert "Einkaufsliste" in tex and "Art.-Nr." in tex and sold[1].item in tex and r"\textbf{online}" in tex
    assert "Nicht als Standard-Pack" in tex
    assert open(res["csv"], encoding="utf-8").read().count("\n") == len(order.lines) + 1


def test_needs_from_a_manual(tmp_path):
    tex = tmp_path / "manual.tex"
    tex.write_text("Layers & 5 (plates, 3.2\\,mm) \\\\\n\\section*{Parts}\n\\begin{longtable}{@{}l l r@{}}\n"
                   "2x4 & \\swatch{cf2f2ed}\\ white & 20 \\\\\nslope 2x2 & \\swatch{c0a3363}\\ dark blue & 3 \\\\\n"
                   "\\end{longtable}\n")
    assert needs_from_manual(tex) == {("plate", "2x4", "white"): 20, ("plate", "slope 2x2", "dark_blue"): 3}


def test_cheapest_catalogue_keeps_the_grid(parts):
    """A shop selling only 1x1, 1x2 and 1x4 bricks: the search drops the sizes it lacks - same cells, still one piece
    (the wall: one stud thick, running bond holds with short bricks too)."""
    lz = Legolizer()
    s = LegoSettings(input=str(parts["wall"]), size_mode="scale", size=1.0, colour_mode="single", single_colour="red")
    base = lz.run(s)
    sold = {"1x1", "1x2", "1x4"}
    cat = Catalogue([pack(p, "red", 100, 4.95) for p in sold])
    best_s, best, order, trace = cheapest_sizes(lz, s, cat)
    assert {p for (_u, p, _c) in needs(base)} - sold                    # with every size it uses others
    assert {p for (_u, p, _c) in needs(best)} <= sold and not order.missing
    assert len(trace) > 1 and order.search_cost() <= price({"L": needs(base)}, cat).search_cost()
    assert best.occupancy.sum() == base.occupancy.sum()
    assert best.stats["groups"] <= base.stats["groups"] and best.stats["floating"] <= base.stats["floating"]


def test_cheapest_catalogue_respects_a_parts_limit(parts):
    lz = Legolizer()
    s = LegoSettings(input=str(parts["wall"]), size_mode="scale", size=1.0, colour_mode="single", single_colour="red")
    cat = Catalogue([pack(p, "red", 100, 4.95) for p in ("1x1", "1x2", "1x4", "1x8")])
    free = cheapest_sizes(lz, s, cat)[1].stats["bricks"]
    capped = cheapest_sizes(lz, s, cat, max_parts=lz.run(s).stats["bricks"])[1].stats["bricks"]
    assert capped <= lz.run(s).stats["bricks"] <= free


def test_cheapest_sizes_per_colour(parts, two_result):
    """Red sold as 1x1, 1x2, 2x2, blue only as 1x1 and 1x4: each colour keeps to its own sizes, nothing is missing,
    the settings carry the sizes per colour (result.json too)."""
    sold = {"red": ("1x1", "1x2", "2x2"), "blue": ("1x1", "1x4")}
    cat = Catalogue([pack(p, c, 100, 4.95) for c, ps in sold.items() for p in ps])
    lz = Legolizer()
    s = LegoSettings(input=str(parts["two"]), size_mode="scale", size=1.0)
    best_s, best, order, _trace = cheapest_sizes(lz, s, cat)
    assert not order.missing
    for (_u, p, c) in needs(best):
        assert p in sold[c], (p, c)
    assert dict(best_s.colour_sizes) and best.occupancy.sum() == two_result.occupancy.sum()
    assert LegoSettings.from_dict(best_s.to_dict()).colour_sizes == best_s.colour_sizes


def test_a_part_the_shop_lacks_is_bought_in_the_nearest_colour(tmp_path):
    """1x1 silver bricks are not sold, light bluish grey ones are: bought instead and marked so."""
    cat = Catalogue([pack("1x1", "light_bluish_gray", 100, 4.95), pack("1x1", "red", 100, 4.95)])
    order = price({"m": {("brick", "1x1", "flat_silver"): 12}}, cat)
    (line,) = order.lines
    assert (line.colour, line.instead_of, line.shop_colour) == ("light_bluish_grey", "flat_silver", "light_bluish_gray")
    assert not order.missing and order.search_cost() > order.parts_cost
    tex = open(write_order(order, tmp_path, "de", pdf=False)["tex"], encoding="utf-8").read()
    assert "statt silber" in tex and r"\emph{statt}" in tex


def test_a_brick_the_shop_lacks_is_bought_as_three_plates(tmp_path):
    cat = Catalogue([pack("1x1", "yellow", 200, 4.95, unit="plate"), pack("1x1", "orange", 100, 4.95)])
    order = price({"m": {("brick", "1x1", "yellow"): 7}}, cat)
    (line,) = order.lines
    assert (line.unit, line.colour, line.need, line.plates_per_brick, line.instead_of) == ("plate", "yellow", 21, 3, "")
    assert "3 je Stein 1x1" in open(write_order(order, tmp_path, "de", pdf=False)["md"], encoding="utf-8").read()
