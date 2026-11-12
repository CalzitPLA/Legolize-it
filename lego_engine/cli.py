"""Legolize it from the command line: an STL/STEP in, bricks out.

    python3 -m lego_engine part.step --studs 24 --unit plate --shapes all --special --out DIR --stl layers --fcstd \
        --manual

writes DIR/result.json (settings, placement, grid, every brick and piece, colours, numbers) and, if asked, the STL
stack (DIR/stl/, lego_engine.export_stl), the FreeCAD document (DIR/<part>.FCStd, coloured, with its sheet of
dimensions and the stacking check) and the building manual (DIR/manual/manual.tex and .pdf). Prints a summary.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path
from typing import List

from .export_stl import MODES as STL_MODES
from .mesh_io import UP_AXES
from .palette import FADES, MODES as COLOUR_MODES, NAMES as COLOUR_NAMES
from .pipeline import LegoSettings, Legolizer, default_out_dir
from .shapes import FAMILIES, parse_families
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
    ap.add_argument("--interlock", type=float, default=1.5, help="layout: reward for bricks spanning bricks below")
    ap.add_argument("--deflection", type=float, default=0.05, help="STEP tessellation, mm")
    ap.add_argument("--no-snap", action="store_true", help="colours from the input as they are (not brick colours)")
    ap.add_argument("--system-json", default=None, help="brick system sizes as JSON (lego_engine.system.BrickSystem)")
    ap.add_argument("--shapes", default="none",
                    help=f"shaped pieces where they fit better than bricks: a list of {', '.join(FAMILIES)}, or all, "
                         "or none (default)")
    ap.add_argument("--special", nargs="?", type=float, const=0.3, default=None, metavar="TOL",
                    help="special pieces (the input's exact shape) where the best brick or piece leaves more than TOL "
                         "of a cell wrong (the bare flag: 0.3)")
    ap.add_argument("--colours", choices=COLOUR_MODES, default="auto",
                    help="brick colours (default auto: the input's colours if it has any, else by size)")
    ap.add_argument("--fade", choices=list(FADES), default="sunset", help="--colours fade: the gradient")
    ap.add_argument("--single", choices=COLOUR_NAMES, default="red", help="--colours single: the colour")
    ap.add_argument("--pitch", type=float, default=None, help="stud pitch in mm (default 8.0)")
    ap.add_argument("--stud-play", "--socket-clearance", dest="socket_clearance", type=float, default=None,
                    help="radial play of a stud in what receives it - sockets, walls, tubes, posts (mm, 0.15)")
    ap.add_argument("--out", default=None, help="output directory (default ~/legolize_results/<part>/<time>)")
    ap.add_argument("--stl", choices=STL_MODES + ("none",), default="none", help="write the STL stack")
    ap.add_argument("--hollow-bricks", action=argparse.BooleanOptionalAction, default=True,
                    help="--stl bricks: hollow underside with tubes (default on)")
    ap.add_argument("--fcstd", action="store_true", help="write the FreeCAD document (needs freecadcmd)")
    ap.add_argument("--no-input", action="store_true", help="--fcstd: leave the input out of the document")
    ap.add_argument("--solid-fc", action="store_true", help="--fcstd: solid bricks with sockets, not hollow ones")
    ap.add_argument("--manual", action="store_true", help="write the building manual (LaTeX, and its PDF)")
    ap.add_argument("--no-pdf", action="store_true", help="--manual: LaTeX only, do not run pdflatex")
    ap.add_argument("--png", default=None, help="render a picture of the result (needs VTK; offscreen)")
    ap.add_argument("--server", default=None, metavar="HOST",
                    help="run on a server over ssh (legolize-it server add HOST PATH); the results come back to --out")
    ap.add_argument("--quiet", action="store_true")
    return ap


def settings_from_args(args) -> LegoSettings:
    system = BrickSystem.from_dict(json.loads(args.system_json)) if args.system_json else BrickSystem()
    if args.pitch:
        system = replace(system, pitch=args.pitch)
    if args.socket_clearance is not None:
        system = replace(system, socket_clearance=args.socket_clearance)
    mode, size = ("scale", args.scale) if args.scale is not None else ("studs", args.studs or 16.0)
    return LegoSettings(input=str(Path(args.input).resolve()), up=args.up, size_mode=mode, size=size, unit=args.unit,
                        threshold=args.threshold, samples=args.samples, shell=args.shell, thin_walls=args.thin_walls,
                        sizes=parse_sizes(args.sizes), max_length=args.max_length, colour_mode=args.colours,
                        fade=args.fade, single_colour=args.single, shapes=parse_families(args.shapes),
                        special=args.special is not None,
                        special_tolerance=0.3 if args.special is None else args.special,
                        bridge_weight=args.interlock, deflection=args.deflection, snap_colours=not args.no_snap,
                        system=system)


def settings_args(s: LegoSettings) -> List[str]:
    """The command-line arguments (without the input) that give the settings `s` - for a run on a server."""
    out = ["--studs" if s.size_mode == "studs" else "--scale", repr(float(s.size)), "--up", s.up, "--unit", s.unit,
           "--threshold", repr(float(s.threshold)), "--samples", str(s.samples), "--shell", str(s.shell),
           "--thin-walls", s.thin_walls, "--sizes", ",".join(f"{a}x{b}" for a, b in s.sizes),
           "--max-length", str(s.max_length), "--interlock", repr(float(s.bridge_weight)),
           "--deflection", repr(float(s.deflection)), "--colours", s.colour_mode, "--fade", s.fade,
           "--single", s.single_colour, "--shapes", ",".join(s.shapes) or "none",
           "--system-json", json.dumps(s.system.to_dict(), separators=(",", ":"))]
    if s.special:
        out += ["--special", repr(float(s.special_tolerance))]
    if not s.snap_colours:
        out.append("--no-snap")
    return out


def _without(argv: List[str], option: str, value: bool = True) -> List[str]:
    out, skip = [], False
    for a in argv:
        if skip:
            skip = False
            continue
        if a == option:
            skip = value
            continue
        if a.startswith(option + "="):
            continue
        out.append(a)
    return out


def main_remote(args, argv: List[str], say) -> int:
    """The same run on a server (lego_engine.remote): the input goes there, the results come back to --out."""
    from .remote import run_cli
    out = Path(args.out) if args.out else default_out_dir(settings_from_args(args))
    rest = _without(_without(_without(argv, "--server"), "--out"), "--png")
    rest.remove(args.input)
    summary = run_cli(args.server, args.input, rest, out, progress=say)
    if args.png:                                   # the picture is drawn here (the same settings, laid again here)
        from lego_gui.scene import render_png
        summary["png"] = str(render_png(Legolizer(say).run(settings_from_args(args)), args.png))
    print(json.dumps(summary, indent=1))
    return 0 if "error" not in summary else 2


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    args = build_parser().parse_args(argv)
    say = (lambda _t: None) if args.quiet else (lambda t: print(t, file=sys.stderr))
    if args.server:
        return main_remote(args, argv, say)
    settings = settings_from_args(args)
    result = Legolizer(say).run(settings)
    out = Path(args.out) if args.out else default_out_dir(settings)
    result.save_json(out / "result.json")
    summary = {"result": str(out / "result.json"), **{k: result.stats[k] for k in
               ("bricks", "shaped", "special", "layers", "grid", "size_mm", "counts", "groups", "floating", "stable")},
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
                         hollow=not args.solid_fc, progress=say)
        summary["fcstd"], summary["macro"] = fc["fcstd"], fc["macro"]
        summary["fit"], summary["stacking"] = fc["fit"], fc["stacking"]
        if fc.get("warnings"):
            summary["fcstd_warnings"] = fc["warnings"]
    if args.manual:
        from .export_manual import write_manual
        man = write_manual(result, out / "manual", pdf=not args.no_pdf, progress=say)
        summary["manual"] = man["pdf"] or man["tex"]
    if args.png:
        from lego_gui.scene import render_png
        summary["png"] = str(render_png(result, args.png))
    print(json.dumps(summary, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
