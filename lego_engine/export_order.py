"""The order list for BlueBrixx part packs (lego_engine.shop.Order): LaTeX and its PDF, Markdown, CSV; English or German.

    order/order.tex, order.pdf    one line per pack to buy, sorted by colour: a box to tick, the item number (what the
                                  shop's search and the store staff look up), part, colour (swatch), pieces per pack,
                                  packs, how many pieces each model needs, price; the total and shipping; then the
                                  parts no standard pack covers (shaped pieces, colours or sizes not sold)
    order/order.md, order.csv     the same as text (the CSV with ; and decimal commas when German)

Packs the shop marks "available exclusively online" are flagged - in 2026-11 all in-stock brick and plate packs.
"""
from __future__ import annotations

import csv
import re
import shutil
import subprocess
from pathlib import Path
from typing import Callable, Dict, Optional

from .export_manual import _tex_colour_id, tex_escape
from .palette import PALETTE
from .shop import FREE_FROM, Order

TEXT: Dict[str, Dict[str, str]] = {
    "en": {"title": "BlueBrixx order list", "models": "Models", "bricks": "bricks", "plates": "plates",
           "parts": "parts", "stand": "Prices and stock: the BlueBrixx online shop, {date}.",
           "online_note": "Packs marked \\textbf{online} are sold online only, not in the stores.",
           "item": "Item no.", "part": "Part", "colour": "Colour", "per_pack": "Pieces/pack", "packs": "Packs",
           "need": "Needed (per model)", "price": "Price", "sum": "Sum", "online": "online",
           "total": "Parts {cost} EUR, {packs} packs; online shipping {ship} EUR (free from {free} EUR).",
           "spare": "Packs often hold more than needed - the rest is stock for the next model.",
           "missing": "Not available as a standard pack", "count": "Count", "why": "Why",
           "shaped": "shaped piece: not a standard pack", "unsold": "no pack of this size and colour in stock",
           "brick": "Brick", "plate": "Plate", "yes": "yes", "instead": "instead of",
           "as_plates": "3 per brick {part}",
           "substitutes": "Colours marked \\emph{instead of} are the nearest colour the shop sells in that part."},
    "de": {"title": "BlueBrixx Einkaufsliste", "models": "Modelle", "bricks": "Steine", "plates": "Platten",
           "parts": "Teile", "stand": "Preise und Verfügbarkeit: BlueBrixx-Onlineshop, Stand {date}.",
           "online_note": "Mit \\textbf{online} markierte Packs gibt es laut Shop nur online, nicht in den Stores.",
           "item": "Art.-Nr.", "part": "Teil", "colour": "Farbe", "per_pack": "Stück/Pack", "packs": "Packs",
           "need": "benötigt (je Modell)", "price": "Preis", "sum": "Summe", "online": "online",
           "total": "Teile {cost} EUR, {packs} Packs; online zzgl. Versand {ship} EUR (versandkostenfrei ab {free} EUR).",
           "spare": "Packs enthalten oft mehr als nötig - der Rest ist Vorrat für das nächste Modell.",
           "missing": "Nicht als Standard-Pack erhältlich", "count": "Anzahl", "why": "Grund",
           "shaped": "Formteil: kein Standard-Pack", "unsold": "kein Pack dieser Größe und Farbe vorrätig",
           "brick": "Stein", "plate": "Platte", "yes": "ja", "instead": "statt",
           "as_plates": "3 je Stein {part}",
           "substitutes": "Mit \\emph{statt} markierte Farben: die nächste Farbe, die der Shop in diesem Teil hat."},
}
COLOUR_DE = {
    "white": "weiß", "black": "schwarz", "red": "rot", "dark_red": "dunkelrot", "blue": "blau", "dark_blue": "dunkelblau",
    "medium_blue": "mittelblau", "azure": "azurblau", "sand_blue": "sandblau", "yellow": "gelb",
    "light_yellow": "hellgelb", "orange": "orange", "dark_orange": "dunkelorange", "bright_light_orange": "hellorange",
    "green": "grün", "dark_green": "dunkelgrün", "lime": "limettengrün", "tan": "beige", "dark_tan": "dunkelbeige",
    "reddish_brown": "rotbraun", "light_grey": "hellgrau", "dark_grey": "dunkelgrau",
    "light_bluish_grey": "hellgrau (blaugrau)", "dark_bluish_grey": "dunkelgrau (blaugrau)", "medium_nougat": "nougat",
    "nougat": "nougat hell", "light_nougat": "hautfarben hell", "lavender": "lavendel", "dark_purple": "dunkellila",
    "cream": "creme", "pearl_gold": "perlgold", "flat_silver": "silber",
}


def colour_name(colour: str, lang: str) -> str:
    return COLOUR_DE.get(colour, colour.replace("_", " ")) if lang == "de" else colour.replace("_", " ")


def day(iso: str, lang: str) -> str:
    """2026-11-24 -> 24.11.2026 in German."""
    parts = iso.split("-")
    return ".".join(reversed(parts)) if lang == "de" and len(parts) == 3 else iso


def money(x: float, lang: str) -> str:
    s = f"{x:.2f}"
    return s.replace(".", ",") if lang == "de" else s


def write_order(order: Order, out_dir, lang: str = "de", title: Optional[str] = None, pdf: bool = True,
                progress: Optional[Callable[[str], None]] = None) -> Dict[str, Optional[str]]:
    """Write order.tex (and .pdf), order.md, order.csv into out_dir. Returns their paths ("pdf": None without
    pdflatex)."""
    say = progress or (lambda _t: None)
    t = TEXT[lang]
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    title = title or t["title"] + ": " + ", ".join(m[0] for m in order.models)
    lines = sorted(order.lines, key=lambda x: (colour_name(x.colour, lang), x.unit, x.part))

    def part(unit, p, x=None):
        name = f"{t[unit]} {p}"
        if x is not None and x.plates_per_brick > 1:                    # a brick the shop lacks, bought as 3 plates
            name += " (" + t["as_plates"].format(part=p) + ")"
        return name

    def per(d):
        return ", ".join(f"{k} {v}" for k, v in d.items())

    def cname(x):                                                       # the colour bought (and the model's, if other)
        c = colour_name(x.colour, lang)
        return c + (f" ({t['instead']} {colour_name(x.instead_of, lang)})" if x.instead_of else "")
    online = any(x.pack.online_only for x in lines)
    substitutes = any(x.instead_of for x in lines)

    with open(out / "order.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, delimiter=";" if lang == "de" else ",")
        w.writerow([t["item"], "product", t["part"], t["colour"], "shop colour", t["per_pack"], t["packs"], t["need"],
                    t["price"], t["sum"], t["online"], "url"])
        for x in lines:
            w.writerow([x.pack.item, x.pack.name, part(x.unit, x.part, x), cname(x), x.shop_colour,
                        x.pack.pieces, x.packs, f"{x.need} ({per(x.per_model)})", money(x.pack.price, lang),
                        money(x.cost, lang), t["yes"] if x.pack.online_only else "", x.pack.url])

    total = t["total"].format(cost=money(order.parts_cost, lang), packs=order.packs, ship=money(order.shipping, lang),
                              free=f"{FREE_FROM:.0f}")
    md = [f"# {title}", "", t["stand"].format(date=day(order.catalogue_date, lang)), "",
          f"| | {t['item']} | {t['part']} | {t['colour']} | {t['per_pack']} | {t['packs']} | {t['need']} | "
          f"{t['price']} | {t['sum']} | |", "|---|---|---|---|---:|---:|---|---:|---:|---|"]
    md += [f"| ☐ | [{x.pack.item}]({x.pack.url}) | {part(x.unit, x.part, x)} | {cname(x)} | "
           f"{x.pack.pieces} | {x.packs} | {x.need} ({per(x.per_model)}) | {money(x.pack.price, lang)} | "
           f"{money(x.cost, lang)} | {t['online'] if x.pack.online_only else ''} |" for x in lines]
    md += ["", f"**{total}**"]
    if substitutes:
        md += ["", re.sub(r"\\emph\{([^}]*)\}", r"*\1*", t["substitutes"])]
    if order.missing:
        md += ["", f"## {t['missing']}", "", f"| {t['part']} | {t['colour']} | {t['count']} | {t['why']} |",
               "|---|---|---:|---|"]
        md += [f"| {part(m.unit, m.part)} | {colour_name(m.colour, lang)} | {m.need} ({per(m.per_model)}) | "
               f"{t[m.why]} |" for m in order.missing]
    (out / "order.md").write_text("\n".join(md) + "\n", encoding="utf-8")

    cols = {}
    for x in [*lines, *order.missing]:
        if x.colour in PALETTE:
            cols[x.colour] = _tex_colour_id(PALETTE[x.colour])

    def sw(c):
        return rf"\swatch{{{cols[c]}}}\ " if c in cols else ""
    doc = [r"\documentclass[a4paper,10pt]{article}", r"\usepackage[margin=14mm,landscape]{geometry}",
           r"\usepackage[T1]{fontenc}", r"\usepackage[utf8]{inputenc}", r"\usepackage{lmodern}",
           r"\usepackage{booktabs}", r"\usepackage{longtable}", r"\usepackage{array}", r"\usepackage{amssymb}",
           r"\usepackage{tikz}", r"\usepackage[hidelinks]{hyperref}", r"\setlength{\parindent}{0pt}",
           r"\pagestyle{empty}", r"\renewcommand{\arraystretch}{1.3}",
           r"\newcommand{\swatch}[1]{\tikz[baseline=-0.6ex]\filldraw[fill=#1,draw=black!60] (0,-0.12) rectangle (0.5,0.18);}"]
    doc += [rf"\definecolor{{{cid}}}{{rgb}}{{{PALETTE[c][0]:.4f},{PALETTE[c][1]:.4f},{PALETTE[c][2]:.4f}}}"
            for c, cid in cols.items()]
    models = "; ".join(f"{n} ({t['plates'] if u == 'plate' else t['bricks']}, {k} {t['parts']})"
                       for n, u, k in order.models)
    doc += [r"\begin{document}", rf"{{\LARGE\bfseries {tex_escape(title)}}}\par\medskip",
            rf"{t['models']}: {tex_escape(models)}.\par",
            tex_escape(t["stand"].format(date=day(order.catalogue_date, lang))) + (" " + t["online_note"] if online else "")
            + r"\par\medskip",
            r"\begin{longtable}{@{}c >{\bfseries}l l l r r p{6.6cm} r r c@{}}", r"\toprule",
            rf" & {t['item']} & {t['part']} & {t['colour']} & {t['per_pack']} & {t['packs']} & "
            rf"{tex_escape(t['need'])} & {t['price']} & {t['sum']} & \\", r"\midrule", r"\endhead"]
    bold_online = r"\textbf{" + t["online"] + "}"                     # (no backslashes inside f-strings: Python 3.10)
    for x in lines:
        flag = bold_online if x.pack.online_only else ""
        doc.append(rf"$\square$ & \href{{{x.pack.url}}}{{{x.pack.item}}} & {tex_escape(part(x.unit, x.part, x))} & "
                   rf"{sw(x.colour)}{tex_escape(cname(x))} & {x.pack.pieces} & {x.packs} & "
                   rf"{x.need} ({tex_escape(per(x.per_model))}) & {money(x.pack.price, lang)} & "
                   rf"{money(x.cost, lang)} & {flag} \\")
    doc += [r"\midrule", rf" & & & & & {order.packs} & & & \textbf{{{money(order.parts_cost, lang)}}} & \\",
            r"\bottomrule", r"\end{longtable}", tex_escape(total) + r"\par", tex_escape(t["spare"]) + r"\par"]
    if substitutes:
        doc.append(t["substitutes"] + r"\par")
    if order.missing:
        doc += [rf"\bigskip{{\large\bfseries {tex_escape(t['missing'])}}}\par\smallskip",
                r"\begin{longtable}{@{}l l r l@{}}", r"\toprule",
                rf"{t['part']} & {t['colour']} & {t['count']} & {t['why']} \\", r"\midrule"]
        doc += [rf"{tex_escape(part(m.unit, m.part))} & {sw(m.colour)}{tex_escape(colour_name(m.colour, lang))} & "
                rf"{m.need} ({tex_escape(per(m.per_model))}) & {tex_escape(t[m.why])} \\" for m in order.missing]
        doc += [r"\bottomrule", r"\end{longtable}"]
    doc.append(r"\end{document}")
    tex = out / "order.tex"
    tex.write_text("\n".join(doc) + "\n", encoding="utf-8")
    res: Dict[str, Optional[str]] = {"tex": str(tex), "md": str(out / "order.md"), "csv": str(out / "order.csv"),
                                     "pdf": None}
    if pdf:
        exe = shutil.which("pdflatex")
        if not exe:
            say("no pdflatex: the order list stays LaTeX (compile order.tex yourself)")
            return res
        for _ in range(2):
            proc = subprocess.run([exe, "-interaction=nonstopmode", "-halt-on-error", tex.name], cwd=out,
                                  capture_output=True, text=True, timeout=300)
        if proc.returncode == 0 and (out / "order.pdf").exists():
            res["pdf"] = str(out / "order.pdf")
        else:
            say("pdflatex failed: " + proc.stdout[-300:])
    return res
