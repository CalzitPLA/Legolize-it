"""Export to FreeCAD: a .FCStd built by `freecadcmd` (lego_engine/freecad_build.py), with the colours inside.

Colours and transparency are view properties, and a console FreeCAD has no view objects - so they are written into the
saved file afterwards (`write_view_colours`): a GuiDocument.xml with, per part, its ShapeAppearance (FreeCAD 1.x: a
binary material record per part, the format FreeCAD itself writes - checked byte for byte against freecadcmd's own
output, tests/test_freecad_colours.py), ShapeColor (FreeCAD 0.2x) and Transparency, plus an isometric camera. The
document opens coloured, the input translucent. `<name>_colours.FCMacro` next to it sets the same colours from inside
FreeCAD (a fallback; run it from the Macro dialog).

The bricks and pieces are sized for stacking from one stud play (lego_engine.system.BrickSystem.fit): hollow bricks
with walls, tubes and posts, shaped and special pieces with sockets. The document's spreadsheet "Dimensions" lists
every size and gap, and the result of a stacking check made in FreeCAD on pairs of parts that sit on each other.
"""
from __future__ import annotations

import json
import math
import os
import re
import shutil
import struct
import subprocess
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

from .mesh_io import STEP_SUFFIXES, clean_env, find_freecadcmd, tessellate_step

BUILD_SCRIPT = Path(__file__).with_name("freecad_build.py")
GUI_ENV = "LEGOLIZE_FREECAD"          # the FreeCAD *window* for "Open in FreeCAD" (default: `freecad` on PATH)
INPUT_RGB, INPUT_TRANSPARENCY = (0.75, 0.75, 0.72), 80

MACRO = '''# Legolize it: colours for {fcstd_name} (the document has them already; this sets them again).
import os

import FreeCAD as App
import FreeCADGui as Gui

PATH = {path!r}
INPUT_TRANSPARENCY = {transparency}
COLOURS = {colours}   # brick colour index per object, see PALETTE
PALETTE = {palette}

doc = None
for d in App.listDocuments().values():          # already open (the document given with the macro)?
    if d.FileName and os.path.normpath(d.FileName) == os.path.normpath(PATH):
        doc = d
if doc is None:
    doc = App.openDocument(PATH)
App.setActiveDocument(doc.Name)


def paint(obj, rgb, transparency=0):
    vo = getattr(obj, "ViewObject", None)
    if vo is None:
        return
    rgb = tuple(float(x) for x in rgb)
    try:                                             # FreeCAD 1.x
        mat = App.Material()
        mat.DiffuseColor = rgb
        mat.Transparency = transparency / 100.0
        vo.ShapeAppearance = (mat,)
    except Exception:                                # FreeCAD 0.2x
        vo.ShapeColor = rgb
    vo.Transparency = transparency


for name, index in COLOURS.items():
    obj = doc.getObject(name)
    if obj is not None:
        paint(obj, PALETTE[index])
inp = doc.getObject("Input")
if inp is not None:
    paint(inp, (0.75, 0.75, 0.72), INPUT_TRANSPARENCY)
try:
    view = Gui.getDocument(doc.Name).activeView()
    view.viewIsometric()
    view.fitAll()
except Exception:
    pass
'''


# ---------------------------------------------------------------------------------------------- colours in the file


def packed(rgb, alpha: float = 0.0) -> int:
    """FreeCAD's packed colour (Base::Color::getPackedValue): 0xRRGGBBAA, each channel x * 255 + 0.5 truncated."""
    r, g, b = (min(255, max(0, int(float(x) * 255.0 + 0.5))) for x in rgb[:3])
    return (r << 24) | (g << 16) | (b << 8) | min(255, max(0, int(alpha * 255.0 + 0.5)))


def material_list_blob(materials: List[Tuple[tuple, float]]) -> bytes:
    """The binary file of an App::PropertyMaterialList (format version 3, FreeCAD 1.x): the count, per material the
    packed ambient, diffuse, specular and emissive colours, shininess and transparency (float32), then per material
    three empty strings (image, image path, uuid). Ambient, specular and shininess are FreeCAD's default material."""
    out = struct.pack("<I", len(materials))
    for rgb, transparency in materials:
        out += struct.pack("<IIIIff", packed((0.3333, 0.3333, 0.3333)), packed(rgb), packed((0.5333, 0.5333, 0.5333)),
                           0, 0.9, float(transparency))
    out += struct.pack("<I", 0) * (3 * len(materials))
    return out


def iso_camera(lo, hi) -> str:
    """An orthographic camera looking at the box lo..hi like FreeCAD's isometric view (from +x, -y, +z)."""
    lo, hi = np.asarray(lo, float), np.asarray(hi, float)
    centre, diag = (lo + hi) / 2, max(1.0, float(np.linalg.norm(hi - lo)))
    pos = centre + 2.0 * diag * np.array([1.0, -1.0, 1.0]) / math.sqrt(3)
    return ("OrthographicCamera {\n  viewportMapping ADJUST_CAMERA\n  position %.6f %.6f %.6f\n"
            "  orientation 0.74290597 0.30772205 0.59447276  1.2171159\n  nearDistance %.4f\n  farDistance %.4f\n"
            "  aspectRatio 1\n  focalDistance %.4f\n  height %.4f\n\n}\n"
            % (pos[0], pos[1], pos[2], 0.5 * diag, 3.5 * diag, 2.0 * diag, 1.15 * diag))


def write_view_colours(fcstd, colours: Dict[str, Tuple[tuple, int]], camera: Optional[str] = None) -> int:
    """Write view colours into a saved .FCStd that has none (a console FreeCAD's): {object name: (rgb 0..1,
    transparency 0..100)}. Adds GuiDocument.xml and one ShapeAppearance record per object after FreeCAD's own
    entries - FreeCAD reads the files of a document in the order they were announced. Returns the objects coloured."""
    from xml.sax.saxutils import quoteattr
    fcstd = Path(fcstd)
    with zipfile.ZipFile(fcstd) as z:
        if "GuiDocument.xml" in z.namelist():
            raise ValueError(f"{fcstd.name} has view properties already")
    lines = ["<?xml version='1.0' encoding='utf-8'?>", "<!--",
             " FreeCAD Document, see https://www.freecad.org for more information...", "-->",
             '<Document SchemaVersion="1">', f'    <ViewProviderData Count="{len(colours)}">']
    files = []
    for n, (name, (rgb, transparency)) in enumerate(colours.items()):
        fname = "ShapeAppearance" + (str(n) if n else "")
        files.append((fname, material_list_blob([(rgb, transparency / 100.0)])))
        lines += [f'        <ViewProvider name={quoteattr(name)} expanded="0">',
                  '            <Properties Count="3" TransientCount="0">',
                  '                <Property name="ShapeColor" type="App::PropertyColor">',
                  f'                    <PropertyColor value="{packed(rgb)}"/>',
                  '                </Property>',
                  '                <Property name="ShapeAppearance" type="App::PropertyMaterialList">',
                  f'                    <MaterialList file="{fname}" version="3"/>',
                  '                </Property>',
                  '                <Property name="Transparency" type="App::PropertyPercent">',
                  f'                    <Integer value="{int(transparency)}"/>',
                  '                </Property>',
                  '            </Properties>',
                  '        </ViewProvider>']
    lines += ["    </ViewProviderData>", f"    <Camera settings={quoteattr(camera or '')}/>", "</Document>", ""]
    with zipfile.ZipFile(fcstd, "a", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr("GuiDocument.xml", "\n".join(lines))
        for fname, blob in files:
            z.writestr(fname, blob)
    return len(colours)


# ---------------------------------------------------------------------------------------------- the document


def _job(result, path: Path, name: str, include_input: bool, hollow: bool, freecad: str, tmp: Path) -> Dict:
    from .geometry import manifold_mesh, special_print_solid, to_trimesh
    from .shapes import TYPES, outline, stud_cells_local
    model, grid, system = result.model, result.grid, result.system
    p, h, c = float(grid.cell[0]), float(grid.cell[2]), system.clearance
    job = {"out": str(path), "name": name, "system": system.to_dict(), "fit": system.fit(), "layer_height": h,
           "origin": [float(x) for x in grid.origin], "hollow": bool(hollow),
           "bricks": [b.to_list() for b in model.bricks], "kinds": [b.kind for b in model.bricks],
           "turns": [b.rot for b in model.bricks], "input": None, "model_diagonal": float(np.linalg.norm(grid.size)),
           "types": {}, "specials": {}, "stud_cells": {}, "contacts": []}
    src = Path(str(result.settings.input))
    step = src.suffix.lower() in STEP_SUFFIXES
    if include_input and src.is_file():
        job["input"] = {"path": str(src.resolve()), "kind": "step" if step else "mesh",
                        "matrix": result.placement.matrix.tolist()}
        if step:                                             # the fallback: the tessellation (cached, mesh_io)
            try:
                job["input"]["mesh"] = str(tessellate_step(src, result.settings.deflection, freecad))
            except Exception:
                pass
    if step and job["input"] is None:                        # special pieces are cut from the exact input
        job["input"] = {"path": str(src.resolve()), "kind": "step", "matrix": result.placement.matrix.tolist(),
                        "hidden": True}
    studs, occ = model.studs, model.occupancy
    nz = model.shape[2]
    for n, b in enumerate(model.bricks):
        fp = (slice(b.i, b.i + b.w), slice(b.j, b.j + b.d))
        cells = [[int(b.i + a), int(b.j + q)] for a, q in np.argwhere(studs[fp + (b.layer,)])]
        if b.kind and not b.is_special:
            t = TYPES[b.kind]
            if b.kind not in job["types"]:
                job["types"][b.kind] = {"outline": outline(b.kind, p, h, c), "studs": stud_cells_local(b.kind),
                                        "cells": [[a, q] for a in range(t.L) for q in range(t.W)],
                                        "L": t.L, "W": t.W, "label": t.label}
            job["stud_cells"][str(n)] = cells
        elif b.is_special:
            k = b.layer
            covered = occ[fp + (k + 1,)] if k + 1 < nz else np.zeros((b.w, b.d), bool)
            top = np.argwhere(studs[fp + (k,)] & covered)
            below = np.argwhere(studs[fp + (k - 1,)]) if k > 0 else np.zeros((0, 2), int)
            corner = grid.origin + np.array([b.i * p, b.j * p, k * h])
            spec = {"stud_cells": [[int(b.i + a), int(b.j + q)] for a, q in top],
                    "studs": [[float(corner[0] + (a + 0.5) * p), float(corner[1] + (q + 0.5) * p), float(corner[2] + h)]
                              for a, q in top],
                    "sockets": [[float(corner[0] + (a + 0.5) * p), float(corner[1] + (q + 0.5) * p), float(corner[2])]
                                for a, q in below]}
            if step:
                spec["box"] = [[float(corner[0] + c), float(corner[1] + c), float(corner[2])],
                               [b.w * p - 2 * c, b.d * p - 2 * c, h]]
            if n in model.special_meshes:                    # the mesh (the fallback for a STEP input)
                mesh = to_trimesh(special_print_solid(model, n, grid, system))
                mpath = tmp / f"special_{n:05d}.stl"
                mesh.export(mpath)
                spec["mesh"] = str(mpath)
            job["specials"][str(n)] = spec
        if b.layer > 0:                                      # who sits on whose studs (the stacking check)
            under = model.ids[fp + (b.layer - 1,)]
            st = studs[fp + (b.layer - 1,)]
            for m in sorted(set(under[(under >= 0) & st].tolist())):
                sel = np.argwhere((under == m) & st)
                job["contacts"].append([int(m), n, [[int(b.i + a), int(b.j + q)] for a, q in sel]])
    return job


def write_fcstd(result, path, include_input: bool = True, hollow: bool = True, colours: Optional[np.ndarray] = None,
                freecad: Optional[str] = None, timeout: int = 1800, progress=None) -> Dict:
    """Build `path` (.FCStd) from `result` (lego_engine.pipeline.LegoResult); returns {"fcstd", "macro", "seconds",
    "objects", "bricks", "checks", "stacking", "fit", "coloured", "warnings"}. `colours`: per brick (default: the
    result's colour mode). `hollow`: bricks with their open underside (walls, tubes, posts) - else solid with sockets."""
    say = progress or (lambda _t: None)
    if not result.model.bricks:
        raise ValueError("nothing to export: the model has no bricks")
    freecad = freecad or find_freecadcmd()
    if freecad is None:
        raise RuntimeError("freecadcmd was not found (install FreeCAD or set LEGOLIZE_FREECADCMD)")
    path = Path(path).with_suffix(".FCStd").resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    name = re.sub(r"\W", "_", path.stem) or "legolize"
    tmp = Path(tempfile.mkdtemp(prefix="legolize_fc_"))
    job = _job(result, path, name, include_input, hollow, freecad, tmp)
    (tmp / "job.json").write_text(json.dumps(job))
    env = clean_env()
    env["LEGOLIZE_JOB"] = str(tmp / "job.json")
    say(f"FreeCAD builds {len(job['bricks'])} parts ...")
    t = time.time()
    proc = subprocess.run([freecad, str(BUILD_SCRIPT)], capture_output=True, text=True, timeout=timeout, env=env)
    seconds = time.time() - t
    done = [ln for ln in proc.stdout.splitlines() if ln.startswith("LEGOLIZE_DONE ")]
    if not done or not path.is_file():
        raise RuntimeError(f"the FreeCAD build failed: {(proc.stderr or proc.stdout)[-800:]}")
    shutil.rmtree(tmp, ignore_errors=True)
    info = json.loads(done[-1].split(" ", 1)[1])

    cols = np.asarray(result.colours() if colours is None else colours, float)
    present = set(info.get("names", []))
    view = {"Brick%05d" % n: (tuple(float(x) for x in cols[n]), 0) for n in range(len(cols))
            if "Brick%05d" % n in present}
    if "Input" in present:
        view["Input"] = (INPUT_RGB, INPUT_TRANSPARENCY)
    grid = result.grid
    lo = np.minimum(grid.origin, result.placed_vertices.min(axis=0))
    hi = np.maximum(grid.origin + grid.size, result.placed_vertices.max(axis=0))
    coloured = write_view_colours(path, view, iso_camera(lo, hi))
    macro = _write_macro(path, cols)
    checks = info.get("checks", [])
    stacking = None
    if checks:
        gaps = [x["stud_gap_mm"] for x in checks if x["stud_gap_mm"] is not None]
        stacking = {"pairs": len(checks), "max_overlap_mm3": max(x["overlap_mm3"] for x in checks),
                    "min_stud_gap_mm": min(gaps) if gaps else None, "max_stud_gap_mm": max(gaps) if gaps else None}
        say(f"stacking check in FreeCAD: {stacking['pairs']} pairs, overlap {stacking['max_overlap_mm3']:g} mm3, "
            f"stud gap {stacking['min_stud_gap_mm']} .. {stacking['max_stud_gap_mm']} mm "
            f"(stud play {result.system.stud_play} mm)")
    for w in info.get("warnings", []):
        say(f"FreeCAD: {w}")
    say(f"FreeCAD document: {path} ({seconds:.1f} s), {coloured} parts coloured")
    return {"fcstd": str(path), "macro": str(macro), "seconds": round(seconds, 2), "objects": info["objects"],
            "bricks": info["bricks"], "warnings": info.get("warnings", []), "checks": checks, "stacking": stacking,
            "fit": result.system.fit(), "coloured": coloured, "special_exact": info.get("special_exact", 0)}


def _write_macro(path: Path, cols: np.ndarray) -> Path:
    palette, index, names = [], {}, {}
    for n, c in enumerate(np.round(cols, 4)):
        key = tuple(float(x) for x in c)
        if key not in index:
            index[key] = len(palette)
            palette.append(key)
        names["Brick%05d" % n] = index[key]
    macro = path.with_name(path.stem + "_colours.FCMacro")
    macro.write_text(MACRO.format(fcstd_name=path.name, path=str(path), transparency=INPUT_TRANSPARENCY,
                                  colours=json.dumps(names), palette=json.dumps(palette)))
    return macro


def freecad_gui() -> Optional[str]:
    """The FreeCAD window to open a document in: $LEGOLIZE_FREECAD, else `freecad` / `FreeCAD` on PATH."""
    chosen = os.environ.get(GUI_ENV, "").strip()
    if chosen:
        return shutil.which(os.path.expanduser(chosen))
    return shutil.which("freecad") or shutil.which("FreeCAD")


def open_in_freecad(fcstd, macro=None) -> subprocess.Popen:
    """Start the FreeCAD window with the document (its colours are inside; `macro` optional) - the user's action,
    from the window."""
    exe = freecad_gui()
    if exe is None:
        raise RuntimeError(f"no FreeCAD window found (set {GUI_ENV} to FreeCAD or its AppImage)")
    args = [exe, str(fcstd)] + ([str(macro)] if macro else [])
    return subprocess.Popen(args, env=clean_env(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            start_new_session=True)
