"""Robustness check: run many parts through Legolize it and report what breaks.

    python3 scripts/check_parts.py <folder or files...> [--sample 40] [--seed 11] [--studs 16] [--unit brick]
                                   [--stl] [--fcstd] [--csv out.csv]

Every part (STL/STEP/...; a folder is searched recursively) is legolized with the given settings; with --stl the layer
slabs are built and checked for watertightness, with --fcstd the FreeCAD document is built. Prints one line per part
and a summary (ok / no bricks / errors, the slow ones); exit code 1 if any part raised an error.

Measured 2026-11-11 on the ABC dataset (Koch et al., the 500-part chunk 0000): 8 picked parts (a 0.1 mm sheet, a
1.2 m bar, a 48-solid assembly, a 6 mm part, an invalid solid, a file with unbounded surfaces, ...) with both exports
and 40 random parts (--seed 11) with the slabs: all succeeded, none empty, every slab watertight, 0.5 - 3.8 s each.
"""
from __future__ import annotations

import argparse
import csv
import random
import sys
import tempfile
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lego_engine import LegoSettings, Legolizer  # noqa: E402
from lego_engine.mesh_io import MESH_SUFFIXES, STEP_SUFFIXES  # noqa: E402

SUFFIXES = STEP_SUFFIXES + MESH_SUFFIXES


def collect(paths) -> list:
    out = []
    for p in map(Path, paths):
        if p.is_dir():
            out += sorted(q for q in p.rglob("*") if q.suffix.lower() in SUFFIXES)
        elif p.suffix.lower() in SUFFIXES:
            out.append(p)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--sample", type=int, default=0, help="this many parts at random (0 = all)")
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--studs", type=float, default=16.0)
    ap.add_argument("--unit", choices=("brick", "plate"), default="brick")
    ap.add_argument("--stl", action="store_true", help="also build the layer slabs and check them")
    ap.add_argument("--fcstd", action="store_true", help="also build the FreeCAD document")
    ap.add_argument("--csv", default=None, help="write the per-part numbers here")
    args = ap.parse_args(argv)
    parts = collect(args.paths)
    if args.sample and args.sample < len(parts):
        random.seed(args.seed)
        parts = random.sample(parts, args.sample)
    lz = Legolizer()
    rows, errors, empty, slow = [], 0, 0, []
    for path in parts:
        t = time.time()
        row = {"part": path.name, "status": "ok"}
        try:
            res = lz.run(LegoSettings(input=str(path), size=args.studs, unit=args.unit))
            s = res.stats
            row.update(bricks=s["bricks"], layers=s["layers"], groups=s["groups"], floating=s["floating"],
                       thin=s["thin_cells"])
            if s["bricks"] == 0:
                row["status"], empty = "no bricks", empty + 1
            else:
                with tempfile.TemporaryDirectory() as d:
                    if args.stl:
                        from lego_engine.export_stl import write_stl_stack
                        m = write_stl_stack(res, d, "layers", assembly=False)
                        if not all(x["watertight"] for x in m["files"]["layers"]):
                            row["status"] = "slab not watertight"
                    if args.fcstd:
                        from lego_engine.export_freecad import write_fcstd
                        info = write_fcstd(res, Path(d) / "part.FCStd")
                        if info["warnings"]:
                            row["fcstd"] = "; ".join(info["warnings"])
        except Exception as exc:
            errors += 1
            row["status"] = f"error: {type(exc).__name__}: {str(exc).splitlines()[0][:160]}"
            traceback.print_exc(file=sys.stderr)
        row["seconds"] = round(time.time() - t, 2)
        if row["seconds"] > 5:
            slow.append(path.name)
        rows.append(row)
        print(f"{path.name[:40]:40s} {row['status'][:40]:12s} bricks {row.get('bricks', '-'):>5} layers "
              f"{row.get('layers', '-'):>3} groups {row.get('groups', '-'):>3} floating {row.get('floating', '-'):>3} "
              f"thin {row.get('thin', '-'):>5} {row['seconds']:5.1f} s" + (f"  [{row['fcstd']}]" if "fcstd" in row else ""))
    print(f"\n{len(parts)} parts: {len(parts) - errors - empty} with bricks, {empty} without, {errors} errors; "
          f"slower than 5 s: {slow or 'none'}")
    if args.csv:
        keys = sorted({k for r in rows for k in r}, key=lambda k: list(rows[0]).index(k) if k in rows[0] else 99)
        with open(args.csv, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=keys)
            w.writeheader()
            w.writerows(rows)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
