"""The building manual: a LaTeX document (and its PDF, when pdflatex is there) that builds the model layer by layer.

    manual/manual.tex         title page with the finished model and its numbers, the parts list (part, colour, count),
                              then one step per layer: a picture of the model so far (this layer bright, the ones below
                              pale), the plan of the layer seen from above (TikZ: the layer below grey, every part in
                              its colour with its studs; shaped pieces show which way they fall, special pieces are
                              hatched and numbered) and the parts of this step
    manual/img/step_NNN.png   the pictures (VTK offscreen; without VTK the manual has the plans only)
    manual/manual.pdf         pdflatex, twice (for the page references)

Special pieces are custom parts: they are printed from the STL stack's bricks/ folder (special_NNNNN.stl) - the
manual numbers them the same way.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np

from .catalogues import NOTICE
from .palette import NAMES, PALETTE, snap_index
from .system import name_sort_key


def tex_escape(text: str) -> str:
    rep = {"\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#", "_": r"\_", "{": r"\{",
           "}": r"\}", "~": r"\textasciitilde{}", "^": r"\textasciicircum{}",
           "\u00ae": r"\textregistered{}", "\u2122": r"\texttrademark{}"}
    return "".join(rep.get(ch, ch) for ch in str(text))


def colour_names(colours: np.ndarray) -> List[str]:
    """The brick colour's name per colour: the palette name when it is one, else the nearest with a '~'."""
    out = []
    idx = snap_index(colours) if len(colours) else []
    for c, i in zip(colours, idx):
        name = NAMES[int(i)]
        exact = np.allclose(np.asarray(PALETTE[name]), c, atol=2e-3)
        out.append(name.replace("_", " ") if exact else "~" + name.replace("_", " "))
    return out


def _tex_colour_id(rgb) -> str:
    r, g, b = (int(round(float(x) * 255)) for x in rgb[:3])
    return f"c{r:02x}{g:02x}{b:02x}"


def _fall_arrow(b) -> Optional[tuple]:
    """For a slope or curve: (x0, y0, x1, y1) in cells, from its high side to its low side."""
    from .shapes import TYPES
    t = TYPES.get(b.kind)
    if t is None or t.form not in ("slope", "curve"):
        return None
    cx, cy = b.i + b.w / 2, b.j + b.d / 2
    dx, dy = {0: (1, 0), 1: (0, 1), 2: (-1, 0), 3: (0, -1)}[b.rot % 4]
    half = 0.32 * (b.w if dx else b.d)
    if t.inverted:                                     # drawn dashed: the fall is underneath
        pass
    return cx - dx * half, cy - dy * half, cx + dx * half, cy + dy * half


def _plan(model, k: int, colours: np.ndarray, unit_cm: float, special_no: Dict[int, int], mounted=()) -> str:
    """The TikZ plan of layer k: the layer below grey, this layer's parts in their colours."""
    from .geometry import _rectangles
    from .shapes import TYPES
    occ = model.occupancy
    nx, ny, _nz = occ.shape
    lines = [rf"\begin{{tikzpicture}}[x={unit_cm:.3f}cm,y={unit_cm:.3f}cm]",
             rf"\draw[black!12, line width=0.2pt] (0,0) grid ({nx},{ny});"]
    if k > 0:
        for i, j, w, d in _rectangles(occ[:, :, k - 1]):
            lines.append(rf"\fill[black!10] ({i},{j}) rectangle ({i + w},{j + d});")
    studs = model.studs
    for n in model.layer_bricks(k):
        b = model.bricks[n]
        col = _tex_colour_id(colours[n])
        dark = float(np.dot(colours[n][:3], (0.299, 0.587, 0.114))) < 0.4       # marks in white on dark bricks
        ink, stud_ink = ("white", "white!70!black") if dark else ("black!80", "black!55")
        x0, y0, x1, y1 = b.i + 0.06, b.j + 0.06, b.i + b.w - 0.06, b.j + b.d - 0.06
        t = TYPES.get(b.kind)
        if t is not None and t.form in ("quarter", "dome"):           # a quarter disk around its corner
            r = t.L - 0.12
            cx, cy = {0: (x0, y0), 1: (x1, y0), 2: (x1, y1), 3: (x0, y1)}[b.rot % 4]
            a0 = 90 * (b.rot % 4)
            lines.append(rf"\filldraw[fill={col}, draw=black, line width=0.5pt] ({cx:.2f},{cy:.2f}) -- "
                         rf"++({a0}:{r:.2f}) arc[start angle={a0}, end angle={a0 + 90}, radius={r:.2f}] -- cycle;")
        elif t is not None and t.form == "round":
            lines.append(rf"\filldraw[fill={col}, draw=black, line width=0.5pt] ({b.i + b.w / 2:.2f},"
                         rf"{b.j + b.d / 2:.2f}) circle ({b.w / 2 - 0.06:.2f});")
        elif t is not None and t.form == "bar":                       # two clip plates, the bar between them
            (ax, ay), (ex, ey) = (b.i, b.j), (b.i + b.w - 1, b.j + b.d - 1)
            lines.append(rf"\draw[black, line width=3.2pt] ({ax + 0.5},{ay + 0.5}) -- ({ex + 0.5},{ey + 0.5});")
            lines.append(rf"\draw[{col}, line width=2.2pt] ({ax + 0.5},{ay + 0.5}) -- ({ex + 0.5},{ey + 0.5});")
            for cx, cy in ((ax, ay), (ex, ey)):
                lines.append(rf"\filldraw[fill={col}, draw=black, line width=0.5pt] ({cx + 0.06:.2f},{cy + 0.06:.2f})"
                             rf" rectangle ({cx + 0.94:.2f},{cy + 0.94:.2f});")
        else:
            style = "pattern=north east lines, pattern color=black!45, " if b.is_special else ""
            lines.append(rf"\filldraw[fill={col}, draw=black, line width=0.5pt] ({x0:.2f},{y0:.2f}) rectangle "
                         rf"({x1:.2f},{y1:.2f});")
            if style:
                lines.append(rf"\fill[{style}] ({x0:.2f},{y0:.2f}) rectangle ({x1:.2f},{y1:.2f});")
            if t is not None and t.form == "tile":                     # a tile: smooth, an inner edge
                lines.append(rf"\draw[{stud_ink}, line width=0.3pt] ({x0 + 0.12:.2f},{y0 + 0.12:.2f}) rectangle "
                             rf"({x1 - 0.12:.2f},{y1 - 0.12:.2f});")
        if b.is_brick or (t is not None and t.form != "round") or b.is_special:
            for a in range(b.i, b.i + b.w):
                for q in range(b.j, b.j + b.d):
                    if studs[a, q, k]:
                        lines.append(rf"\draw[{stud_ink}, line width=0.4pt] ({a + 0.5},{q + 0.5}) circle (0.3);")
        elif t is not None and studs[b.i:b.i + b.w, b.j:b.j + b.d, k].any():
            lines.append(rf"\draw[{stud_ink}, line width=0.4pt] ({b.i + b.w / 2},{b.j + b.d / 2}) circle (0.3);")
        arrow = _fall_arrow(b)
        if arrow:
            dash = ", dashed" if t.inverted else ""
            lines.append(rf"\draw[-{{Stealth[length=4pt]}}, line width=0.9pt, {ink}{dash}] ({arrow[0]:.2f},"
                         rf"{arrow[1]:.2f}) -- ({arrow[2]:.2f},{arrow[3]:.2f});")
        if b.is_special:
            lines.append(rf"\node[font=\tiny\bfseries, fill=white, inner sep=1pt] at ({b.i + b.w / 2:.2f},"
                         rf"{b.j + b.d / 2:.2f}) {{S{special_no[n]}}};")
    for q, a in enumerate(getattr(model, "assemblies", None) or ()):
        if q not in mounted:
            continue
        if a.supports:
            cells = [(i, j) for i, j, kk in a.supports]
        else:
            cells = sorted({(i, j) for i, j, kk in a.cells if kk == k})
        for i, j in cells:
            lines.append(rf"\filldraw[fill=white, draw=black, line width=0.6pt] ({i + 0.06:.2f},{j + 0.06:.2f}) rectangle "
                         rf"({i + 0.94:.2f},{j + 0.94:.2f});")
            lines.append(rf"\fill[pattern=crosshatch, pattern color=black!35] ({i + 0.06:.2f},{j + 0.06:.2f}) rectangle "
                         rf"({i + 0.94:.2f},{j + 0.94:.2f});")
        if a.supports and len(cells) >= 2:
            (i0, j0), (i1, j1) = cells[0], cells[-1]
            lines.append(rf"\draw[black, line width=2pt] ({i0 + 0.5},{j0 + 0.5}) -- ({i1 + 0.5},{j1 + 0.5});")
        if cells:
            ci = sum(c[0] for c in cells) / len(cells) + 0.5
            cj = sum(c[1] for c in cells) / len(cells) + 0.5
            lines.append(rf"\node[font=\tiny\bfseries, fill=white, inner sep=1pt] at ({ci:.2f},{cj:.2f}) {{A{q + 1}}};")
    lines.append(r"\end{tikzpicture}")
    return "\n".join(lines)


def _parts_table(rows: List[tuple], with_total: bool = True) -> str:
    """rows: (part name, colour id, colour name, count)."""
    out = [r"\begin{tabular}{@{}l l r@{}}", r"\toprule", r"Part & Colour & Count \\", r"\midrule"]
    for name, cid, cname, count in rows:
        out.append(rf"{tex_escape(name)} & \swatch{{{cid}}}\ {tex_escape(cname)} & {count} \\")
    if with_total:
        out += [r"\midrule", rf"\multicolumn{{2}}{{@{{}}l}}{{all}} & {sum(r[3] for r in rows)} \\"]
    out.append(r"\bottomrule")
    out.append(r"\end{tabular}")
    return "\n".join(out)


def _group(model, indices, colours, names) -> List[tuple]:
    count: Dict[tuple, int] = {}
    for n in indices:
        b = model.bricks[n]
        key = (b.name, _tex_colour_id(colours[n]), names[n])
        count[key] = count.get(key, 0) + 1
    return sorted(((a, b, c, v) for (a, b, c), v in count.items()), key=lambda r: (name_sort_key(r[0]), r[2]))


def _technic_rows(assemblies) -> List[tuple]:
    """Parts-table rows (name, colour id, colour name, count) for the axle sub-assemblies' parts."""
    from .technic import PARTS
    count: Dict[tuple, int] = {}
    for a in assemblies:
        for (key, colour), n in a.part_counts().items():
            k = (PARTS[key].name, _tex_colour_id(PALETTE.get(colour, (0.6, 0.6, 0.6))), colour.replace("_", " "))
            count[k] = count.get(k, 0) + n
    return sorted(((a, b, c, v) for (a, b, c), v in count.items()), key=lambda r: (r[0], r[2]))


def _assembly_pages(assemblies, result, out: Path, pictures: bool, say, layers) -> List[str]:
    """A page per axle sub-assembly: its picture on its own, what goes onto the axle in which order, its parts, and
    the step it is mounted in."""
    from .technic import PARTS
    doc = [r"\clearpage", r"\section*{Sub-assemblies}",
           r"Parts with a cross hole threaded onto an axle: build each first, then mount it in its step. A lying axle "
           r"is held at both ends by a brick with an axle hole; an upright stack sits on studs like any part."]
    step_of = {k: n for n, k in enumerate(layers, start=1)}
    for q, a in enumerate(assemblies):
        name = f"A{q + 1}"
        doc.append(r"\clearpage" if q else r"\par\vspace{4mm}")
        doc.append(rf"\subsection*{{Sub-assembly {name}}}")
        pic = None
        if pictures:
            try:
                from lego_gui.scene import render_assembly
                pic = render_assembly(a, result.grid, result.system, out / "img" / f"assembly_{name}.png")
            except Exception as exc:                                          # no VTK / OpenGL
                say(f"no picture of {name} ({str(exc).splitlines()[0][:100]})")
        if pic is not None:
            doc.append(rf"\begin{{center}}\includegraphics[width=0.62\textwidth]{{img/{Path(pic).name}}}\end{{center}}")
        along = "upwards" if a.axis == 2 else ("from left to right (along x)" if a.axis == 0 else "front to back (along y)")
        axles = ", ".join(PARTS[k].name for k in a.axles) or "no axle (the studs hold it)"
        doc.append(rf"\textbf{{Axle}}: {tex_escape(axles)}.\par")
        if a.supports:
            doc.append(r"\textbf{Ends}: through a brick 1x1 with axle hole at each end.\par")
        seq = [rf"{tex_escape(PARTS[k].name)} ({tex_escape(c.replace('_', ' '))})" for k, _o, c in a.parts]
        runs, prev, n = [], None, 0
        for item in seq + [None]:
            if item == prev:
                n += 1
                continue
            if prev is not None:
                runs.append(prev + (rf" $\times$ {n}" if n > 1 else ""))
            prev, n = item, 1
        doc.append(rf"\textbf{{Thread on}}, {along}: " + "; ".join(runs) + r".\par")
        if layers:
            k = next((x for x in layers if x >= a.mount_layer), layers[-1])
            doc.append(rf"\textbf{{Mounted in}}: step {step_of[k]} (layer {k}).\par")
        else:                                        # artsy: a round part can be one stack, no bricks at all
            doc.append(r"\textbf{Done}: this sub-assembly is the whole model - it stands on its own.\par")
        doc.append(_parts_table(_technic_rows([a])))
    return doc


def write_manual(result, out_dir, colours: Optional[np.ndarray] = None, title: Optional[str] = None,
                 pictures: bool = True, pdf: bool = True, progress=None, images: Optional[Dict] = None) -> Dict:
    """Write the building manual of `result` into `out_dir`; returns {"tex", "pdf" (or None), "steps", "pictures",
    "log"}. `colours`: per brick (default: the result's colour mode). `images`: the pictures made already
    (lego_gui.scene.render_steps into out_dir/img - the window renders them in its own thread)."""
    say = progress or (lambda _t: None)
    model, grid, system = result.model, result.grid, result.system
    if model.empty:
        raise ValueError("nothing to build: the model has no bricks")
    out = Path(out_dir)
    (out / "img").mkdir(parents=True, exist_ok=True)
    cols = np.asarray(result.colours() if colours is None else colours, float)
    names = colour_names(cols)
    stem = Path(str(result.settings.input)).stem or "part"
    title = title or stem
    layers = sorted({b.layer for b in model.bricks})
    special_no = {n: q + 1 for q, n in enumerate(n for n, b in enumerate(model.bricks) if b.is_special)}
    images = dict(images or {})
    if pictures and not images:
        try:
            from lego_gui.scene import render_steps
            say("pictures of the steps ...")
            images = render_steps(result, out / "img", colours=cols)
        except Exception as exc:                                  # no VTK / no OpenGL: plans only
            say(f"no pictures ({str(exc).splitlines()[0][:120]}): the manual has the plans only")
            images = {}
    nx, ny = grid.shape[0], grid.shape[1]
    unit = min(0.6, 8.4 / max(nx, ny))             # the plan beside the picture: at most 8.4 cm
    st = result.stats
    fit = system.fit()
    asm = list(getattr(model, "assemblies", None) or ())
    asm_rgb = [np.asarray(PALETTE[c], float) for a in asm for (_k, c) in a.part_counts() if c in PALETTE]
    used = sorted({_tex_colour_id(c): c for c in list(cols) + asm_rgb}.items())
    defs = "\n".join(rf"\definecolor{{{cid}}}{{rgb}}{{{c[0]:.4f},{c[1]:.4f},{c[2]:.4f}}}" for cid, c in used)
    doc = [r"\documentclass[a4paper,11pt]{article}",
           r"\usepackage[margin=16mm]{geometry}",
           r"\usepackage[T1]{fontenc}", r"\usepackage{lmodern}", r"\usepackage{graphicx}", r"\usepackage{booktabs}",
           r"\usepackage{longtable}", r"\usepackage{tikz}", r"\usetikzlibrary{arrows.meta,patterns}",
           r"\usepackage[hidelinks]{hyperref}", r"\setlength{\parindent}{0pt}", r"\setlength{\parskip}{4pt}",
           r"\setlength{\LTleft}{0pt}",
           defs,
           r"\newcommand{\swatch}[1]{\tikz[baseline=-0.6ex]\filldraw[fill=#1,draw=black!60] (0,-0.12) rectangle (0.5,0.18);}",
           r"\begin{document}",
           r"\begin{titlepage}\centering",
           rf"{{\Huge\bfseries {tex_escape(title)}}}\par\vspace{{4mm}}",
           r"{\Large Building manual -- Legolize it}\par\vspace{8mm}"]
    if images.get("final"):
        doc.append(rf"\includegraphics[width=0.82\textwidth]{{img/{Path(images['final']).name}}}\par\vspace{{6mm}}")
    extra = "".join(f", {st[k]} {k}" for k in ("shaped", "special") if st.get(k))
    tp = int(st.get("technic_parts") or 0)                 # artsy: the parts threaded on axles count too
    extra += f", {tp} Technic" if tp else ""
    grid_text = r" $\times$ ".join(str(x) for x in st["grid"])
    doc += [r"\begin{tabular}{@{}ll@{}}",
            rf"Parts & {st['bricks'] + tp}{tex_escape(extra)} \\",
            rf"Layers & {st['layers']} ({tex_escape(result.settings.unit)}s, {grid.cell[2]:g}\,mm) \\",
            rf"Size & {st['size_mm'][0]:g} $\times$ {st['size_mm'][1]:g} $\times$ {st['size_mm'][2]:g}\,mm \\",
            rf"Grid & {grid_text} cells, pitch {system.pitch:g}\,mm \\",
            rf"Stud play & {fit['stud_play']:g}\,mm (wall {fit['wall']:g}, tube {fit['tube_outer']:g}, socket "
            rf"{fit['socket_diameter']:g}\,mm) \\",
            rf"Holds together & {'yes' if st['stable'] else 'no: ' + str(st['groups']) + ' groups, ' + str(st['floating']) + ' floating'} \\",
            r"\end{tabular}", r"\vfill",
            rf"{{\footnotesize {tex_escape(NOTICE)}\par}}", r"\end{titlepage}"]
    doc += [r"\section*{Parts}", _parts_table(_group(model, range(len(model.bricks)), cols, names)
                                                + _technic_rows(asm)).replace(
        r"\begin{tabular}", r"\begin{longtable}").replace(r"\end{tabular}", r"\end{longtable}")]
    doc += [r"\subsection*{How to read the steps}",
            r"Each step is one layer. The picture shows the model so far, the new layer bright. The plan shows the "
            r"layer from above: the layer below in grey, every part in its colour, circles for studs. An arrow "
            r"points down the fall of a slope or curved slope (dashed: an inverted one, falling underneath). "
            r"Quarter circles are round or dome corners, full circles round bricks. Hatched parts marked "
            r"\textbf{S}$n$ are special pieces: custom parts with the input's exact shape, printed from the STL "
            r"stack (\texttt{bricks/special\_NNNNN.stl})."]
    if asm:
        doc += _assembly_pages(asm, result, out, pictures, say, layers)
    mount = {}                                       # the step an assembly goes in: its layer, or the next with parts
    for q, a in enumerate(asm if layers else ()):
        k = next((x for x in layers if x >= a.mount_layer), layers[-1])
        mount.setdefault(k, []).append(q)
    for s_no, k in enumerate(layers, start=1):
        idx = model.layer_bricks(k)
        doc.append(r"\clearpage")
        doc.append(rf"\section*{{Step {s_no} \normalfont\large(layer {k}, z = {k * grid.cell[2]:g}\,mm, "
                   rf"{len(idx)} parts)}}")
        # the picture and the plan side by side, the parts below in up to three columns (2026-11-12: with the parts
        # beside the picture, a step of 58 parts pushed its plan onto the next page)
        doc.append(r"\begin{minipage}[c]{0.5\textwidth}")
        if images.get(k):
            doc.append(rf"\includegraphics[width=\linewidth]{{img/{Path(images[k]).name}}}")
        doc.append(r"\end{minipage}\hfill\begin{minipage}[c]{0.48\textwidth}\centering")
        doc.append(_plan(model, k, cols, unit, special_no, mount.get(k, ())))
        doc.append(r"\end{minipage}\par\vspace{5mm}")
        for q in mount.get(k, ()):
            a = asm[q]
            what = ("its two bricks with axle hole go on the cells marked A%d" % (q + 1) if a.supports else
                    "its lowest part goes on the cells marked A%d" % (q + 1))
            doc.append(rf"\textbf{{Mount sub-assembly A{q + 1}}}: {what}.\par\vspace{{2mm}}")
        rows = _group(model, idx, cols, names)
        ncol = 1 if len(rows) <= 14 else 2 if len(rows) <= 40 else 3
        per = -(-len(rows) // ncol)
        chunks = [rows[q:q + per] for q in range(0, len(rows), per)]
        width = {1: 0.49, 2: 0.49, 3: 0.32}[ncol]
        doc.append(r"{\scriptsize\noindent" if ncol == 3 else r"{\small\noindent")
        doc.append(r"\hfill".join(rf"\begin{{minipage}}[t]{{{width}\textwidth}}" + _parts_table(c, with_total=False) +
                                   r"\end{minipage}" for c in chunks))
        doc.append(r"\par}")
    if special_no:
        doc += [r"\clearpage", r"\section*{Special pieces}",
                r"Print each from \texttt{bricks/} of the STL stack; the studs and sockets sit where it joins the "
                r"bricks.\par", r"\begin{longtable}{@{}l l l l@{}}", r"\toprule",
                r"Piece & File & Layer & Cells \\", r"\midrule"]
        for n, q in special_no.items():
            b = model.bricks[n]
            doc.append(rf"S{q} & \texttt{{special\_{n:05d}.stl}} & {b.layer} & {b.w} $\times$ {b.d} at "
                       rf"({b.i}, {b.j}) \\")
        doc += [r"\bottomrule", r"\end{longtable}"]
    doc.append(r"\end{document}")
    tex = out / "manual.tex"
    tex.write_text("\n".join(doc) + "\n")
    say(f"building manual: {tex} ({len(layers)} steps)")
    info = {"tex": str(tex), "pdf": None, "steps": len(layers), "pictures": len([k for k in images if k != "final"]),
            "log": ""}
    if pdf:
        exe = shutil.which("pdflatex")
        if exe is None:
            say("no pdflatex: the manual stays LaTeX (compile manual.tex yourself)")
        else:
            say("pdflatex ...")
            for _ in range(2):
                proc = subprocess.run([exe, "-interaction=nonstopmode", "-halt-on-error", tex.name], cwd=out,
                                      capture_output=True, text=True, timeout=600)
            info["log"] = proc.stdout[-3000:]
            if proc.returncode == 0 and (out / "manual.pdf").is_file():
                info["pdf"] = str(out / "manual.pdf")
                say(f"building manual: {info['pdf']}")
            else:
                err = re.findall(r"^!.*$", proc.stdout, re.M)
                say("pdflatex failed: " + (err[0] if err else proc.stdout[-300:]))
    return info
