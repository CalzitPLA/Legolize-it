"""Legolize it from the command line: an STL/STEP in, bricks out.

    python3 -m lego_engine part.step --studs 24 --unit plate --out DIR --stl layers --fcstd

writes DIR/result.json (settings, placement, grid, every brick, colours, numbers) and, if asked, the STL stack
(DIR/stl/, lego_engine.export_stl) and the FreeCAD document (DIR/<part>.FCStd + its colour macro). Prints a summary.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path

from .export_stl import MODES as STL_MODES
from .mesh_io import UP_AXES
from .palette import MODES as COLOUR_MODES
from .pipeline import LegoSettings, Legolizer, default_out_dir
from .system import DEFAULT_SIZES, UNITS, BrickSystem, parse_sizes
from .voxelize import THIN_MODES


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="legolize-it", description=__doc__.split("\n\n")[0])
    ap.add_argument("input", help="the part: .stl or .step/.stp (also .obj/.ply/.off/.3mf/.glb)")
    size = ap.add_mutually_exclusive_group()
    size.add_argument("--studs", type=float, default=None, help="studs along the longest side (default 16)")
    size.add_argument("--scale", type=float, default=None, help="scale factor instead (1 = the part's mm at real size)")
    ap.add_argument("--up", choices=UP_AXES, default="z", help="the part's up axis (default z)")
    ap.add_argument("--unit", choices=UNITS, default="brick", help="layer height: brick (9.6 mm) or plate (3.2 mm)")
    ap.add_argument("--threshold", type=float, default=0.5, help="share of material a cell needs (default 0.5)")
    ap.add_argument("--samples", type=int, default=3, help="sub-samples per cell and axis (default 3)")
    ap.add_argument("--shell", type=int, default=0, help="hollow: keep a shell this many cells thick (0 = solid)")
    ap.add_argument("--thin-walls", nargs="?", choices=THIN_MODES, default="auto", const="all",
                    help="auto (default): surface cells where the material is too thin to fill any cell (sheet metal, "
                         "profiles); all (or the bare flag): every cell the surface passes; off")
    ap.add_argument("--sizes", default=",".join(f"{a}x{b}" for a, b in DEFAULT_SIZES), help="brick catalogue")
    ap.add_argument("--max-length", type=int, default=8, help="longest brick in studs (0 = no limit)")
    ap.add_argument("--colours", choices=COLOUR_MODES, default="size", help="brick colours (default size)")
    ap.add_argument("--pitch", type=float, default=None, help="stud pitch in mm (default 8.0)")
    ap.add_argument("--socket-clearance", type=float, default=None, help="printed sockets: radial play in mm (0.15)")
    ap.add_argument("--out", default=None, help="output directory (default ~/legolize_results/<part>/<time>)")
    ap.add_argument("--stl", choices=STL_MODES + ("none",), default="none", help="write the STL stack")
    ap.add_argument("--hollow-bricks", action=argparse.BooleanOptionalAction, default=True,
                    help="--stl bricks: hollow underside with tubes (default on)")
    ap.add_argument("--fcstd", action="store_true", help="write the FreeCAD document (needs freecadcmd)")
    ap.add_argument("--no-input", action="store_true", help="--fcstd: leave the input out of the document")
    ap.add_argument("--png", default=None, help="render a picture of the result (needs VTK; offscreen)")
    ap.add_argument("--quiet", action="store_true")
    return ap


def settings_from_args(args) -> LegoSettings:
    system = BrickSystem()
    if args.pitch:
        system = replace(system, pitch=args.pitch)
    if args.socket_clearance is not None:
        system = replace(system, socket_clearance=args.socket_clearance)
    mode, size = ("scale", args.scale) if args.scale is not None else ("studs", args.studs or 16.0)
    return LegoSettings(input=str(Path(args.input).resolve()), up=args.up, size_mode=mode, size=size, unit=args.unit,
                        threshold=args.threshold, samples=args.samples, shell=args.shell, thin_walls=args.thin_walls,
                        sizes=parse_sizes(args.sizes), max_length=args.max_length, colour_mode=args.colours,
                        system=system)


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    say = (lambda _t: None) if args.quiet else (lambda t: print(t, file=sys.stderr))
    settings = settings_from_args(args)
    result = Legolizer(say).run(settings)
    out = Path(args.out) if args.out else default_out_dir(settings)
    result.save_json(out / "result.json")
    summary = {"result": str(out / "result.json"), **{k: result.stats[k] for k in
               ("bricks", "layers", "grid", "size_mm", "counts", "groups", "floating", "stable")},
               "timings": result.timings}
    if not result.model.bricks:
        summary["error"] = "no bricks - nothing exported (see the message above)"
        print(json.dumps(summary, indent=1))
        return 2
    if args.stl != "none":
        from .export_stl import write_stl_stack
        write_stl_stack(result, out / "stl", args.stl, hollow_bricks=args.hollow_bricks, progress=say)
        summary["stl"] = str(out / "stl")
    if args.fcstd:
        from .export_freecad import write_fcstd
        fc = write_fcstd(result, out / f"{Path(settings.input).stem}.FCStd", include_input=not args.no_input,
                         progress=say)
        summary["fcstd"], summary["macro"] = fc["fcstd"], fc["macro"]
        if fc.get("warnings"):
            summary["fcstd_warnings"] = fc["warnings"]
    if args.png:
        from lego_gui.scene import render_png
        summary["png"] = str(render_png(result, args.png))
    print(json.dumps(summary, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
