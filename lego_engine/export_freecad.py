"""Export to FreeCAD: a .FCStd built by `freecadcmd` (lego_engine/freecad_build.py) and, next to it, a macro
`<name>_colours.FCMacro` that gives the bricks their colours and makes the input translucent.

Why a macro: colours and transparency are view properties; a console FreeCAD has no view objects, and FreeCAD 1.x
stores them in a binary ShapeAppearance record that is not written by hand here. Open the document with the macro -
`freecad part.FCStd part_colours.FCMacro`, or the window's "Open in FreeCAD" - or run the macro in FreeCAD's Macro
dialog; save the document afterwards and the colours stay.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Dict, Optional

import numpy as np

from .mesh_io import STEP_SUFFIXES, clean_env, find_freecadcmd

BUILD_SCRIPT = Path(__file__).with_name("freecad_build.py")
GUI_ENV = "LEGOLIZE_FREECAD"          # the FreeCAD *window* for "Open in FreeCAD" (default: `freecad` on PATH)

MACRO = '''# Legolize it: colours for {fcstd_name} - opened with the document, or run from FreeCAD's Macro dialog.
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


def write_fcstd(result, path, include_input: bool = True, hollow: bool = False, colours: Optional[np.ndarray] = None,
                freecad: Optional[str] = None, timeout: int = 1800, progress=None) -> Dict:
    """Build `path` (.FCStd) from `result` (lego_engine.pipeline.LegoResult); returns {"fcstd", "macro", "seconds",
    "objects", "bricks"}. `colours`: per brick (default: the result's colour mode)."""
    say = progress or (lambda _t: None)
    if not result.model.bricks:
        raise ValueError("nothing to export: the model has no bricks")
    freecad = freecad or find_freecadcmd()
    if freecad is None:
        raise RuntimeError("freecadcmd was not found (install FreeCAD or set LEGOLIZE_FREECADCMD)")
    path = Path(path).with_suffix(".FCStd").resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    name = re.sub(r"\W", "_", path.stem) or "legolize"
    grid = result.grid
    job = {"out": str(path), "name": name, "system": result.system.to_dict(), "layer_height": float(grid.cell[2]),
           "origin": [float(x) for x in grid.origin], "hollow": bool(hollow),
           "bricks": [b.to_list() for b in result.model.bricks], "input": None}
    src = Path(str(result.settings.input))
    if include_input and src.is_file():
        job["input"] = {"path": str(src.resolve()), "kind": "step" if src.suffix.lower() in STEP_SUFFIXES else "mesh",
                        "matrix": result.placement.matrix.tolist()}
    tmp = Path(tempfile.mkdtemp(prefix="legolize_fc_"))
    (tmp / "job.json").write_text(json.dumps(job))
    env = clean_env()
    env["LEGOLIZE_JOB"] = str(tmp / "job.json")
    say(f"FreeCAD builds {len(job['bricks'])} bricks ...")
    t = time.time()
    proc = subprocess.run([freecad, str(BUILD_SCRIPT)], capture_output=True, text=True, timeout=timeout, env=env)
    seconds = time.time() - t
    done = [ln for ln in proc.stdout.splitlines() if ln.startswith("LEGOLIZE_DONE ")]
    if not done or not path.is_file():
        raise RuntimeError(f"the FreeCAD build failed: {(proc.stderr or proc.stdout)[-800:]}")
    shutil.rmtree(tmp, ignore_errors=True)
    info = json.loads(done[-1].split(" ", 1)[1])

    cols = np.asarray(result.colours() if colours is None else colours, float)
    palette, index = [], {}
    names = {}
    for n, c in enumerate(np.round(cols, 4)):
        key = tuple(float(x) for x in c)
        if key not in index:
            index[key] = len(palette)
            palette.append(key)
        names["Brick%05d" % n] = index[key]
    macro = path.with_name(path.stem + "_colours.FCMacro")
    macro.write_text(MACRO.format(fcstd_name=path.name, path=str(path), transparency=80,
                                  colours=json.dumps(names), palette=json.dumps(palette)))
    say(f"FreeCAD document: {path} ({seconds:.1f} s); colours: {macro.name}")
    return {"fcstd": str(path), "macro": str(macro), "seconds": round(seconds, 2), "objects": info["objects"],
            "bricks": info["bricks"]}


def freecad_gui() -> Optional[str]:
    """The FreeCAD window to open a document in: $LEGOLIZE_FREECAD, else `freecad` / `FreeCAD` on PATH."""
    chosen = os.environ.get(GUI_ENV, "").strip()
    if chosen:
        return shutil.which(os.path.expanduser(chosen))
    return shutil.which("freecad") or shutil.which("FreeCAD")


def open_in_freecad(fcstd, macro=None) -> subprocess.Popen:
    """Start the FreeCAD window with the document (and the colour macro) - the user's action, from the window."""
    exe = freecad_gui()
    if exe is None:
        raise RuntimeError(f"no FreeCAD window found (set {GUI_ENV} to FreeCAD or its AppImage)")
    args = [exe, str(fcstd)] + ([str(macro)] if macro else [])
    return subprocess.Popen(args, env=clean_env(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            start_new_session=True)
