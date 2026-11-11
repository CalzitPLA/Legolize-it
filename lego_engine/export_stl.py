"""The STL stack for 3D printing, written into one directory:

    layers/layer_000.stl ...  mode "layers": one fused slab per layer (lego_engine.geometry.layer_slab), lying on the
                              print bed (z = 0) at its place in x/y; the slabs plug into each other in the order of
                              their numbers - studs of layer n into the sockets of layer n + 1
    bricks/brick_2x4.stl ...  mode "bricks": every brick size once (hollow underside by default), standing on the bed;
                              how many of each is in manifest.json and parts.csv
    assembly.stl              all layer slabs in place (the whole model, for a look in the slicer)
    parts.csv                 every brick: layer, x, y, size, orientation (the building plan)
    manifest.json             the settings, the system, the files and the counts
    README.txt                the printing and building order in words
"""
from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Dict

import numpy as np

from .geometry import brick_solid, layer_slab, to_trimesh
from .system import size_name

MODES = ("layers", "bricks", "both")


def write_stl_stack(result, out_dir, mode: str = "layers", hollow_bricks: bool = True, assembly: bool = True,
                    progress=None) -> Dict:
    """Write the stack of `result` (lego_engine.pipeline.LegoResult) into `out_dir`; returns the manifest."""
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}, not {mode!r}")
    say = progress or (lambda _t: None)
    if not result.model.bricks:
        raise ValueError("nothing to export: the model has no bricks")
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    model, grid, system = result.model, result.grid, result.system
    h = float(grid.cell[2])
    occ = model.occupancy
    manifest: Dict = {"input": str(result.settings.input), "unit": result.settings.unit, "layer_height_mm": h,
                      "system": system.to_dict(), "grid": grid.to_dict(), "placement": result.placement.to_dict(),
                      "stats": {k: v for k, v in result.stats.items() if k != "per_layer"}, "files": {}}

    if mode in ("layers", "both"):
        (out / "layers").mkdir(exist_ok=True)
        layers, pieces = [], []
        for k in range(occ.shape[2]):
            cells = occ[:, :, k]
            if not cells.any():
                continue
            below = occ[:, :, k - 1] if k > 0 else None
            slab = layer_slab(cells, below, h, system)
            mesh = to_trimesh(slab)
            name = f"layers/layer_{k:03d}.stl"
            mesh.export(out / name)                              # bottom at z = 0: ready for the bed
            layers.append({"layer": k, "file": name, "z_mm": round(k * h, 3), "cells": int(cells.sum()),
                           "watertight": bool(mesh.is_watertight), "volume_mm3": round(float(slab.volume()), 1)})
            if assembly:
                placed = mesh.copy()
                placed.apply_translation([grid.origin[0], grid.origin[1], grid.origin[2] + k * h])
                pieces.append(placed)
            say(f"layer {k}: {name}")
        manifest["files"]["layers"] = layers
        if assembly and pieces:
            import trimesh
            trimesh.util.concatenate(pieces).export(out / "assembly.stl")
            manifest["files"]["assembly"] = "assembly.stl"

    if mode in ("bricks", "both"):
        (out / "bricks").mkdir(exist_ok=True)
        types = {}
        for b in model.bricks:
            types.setdefault(size_name(b.w, b.d), [0, sorted((b.w, b.d))])[0] += 1
        bricks = []
        for name, (count, (a, b)) in sorted(types.items(), key=lambda kv: -kv[1][0]):
            mesh = to_trimesh(brick_solid(b, a, h, system, hollow=hollow_bricks))   # long side along x
            fname = f"bricks/brick_{name}.stl"
            mesh.export(out / fname)
            bricks.append({"size": name, "count": count, "file": fname, "watertight": bool(mesh.is_watertight)})
            say(f"brick {name} x {count}: {fname}")
        manifest["files"]["bricks"] = bricks

    with open(out / "parts.csv", "w", newline="") as fh:
        wr = csv.writer(fh)
        wr.writerow(["brick", "layer", "z_mm", "x_mm", "y_mm", "size", "along", "i", "j", "w", "d"])
        for n, b in enumerate(model.bricks):
            x, y = grid.origin[0] + b.i * grid.cell[0], grid.origin[1] + b.j * grid.cell[1]
            wr.writerow([n, b.layer, round(b.layer * h, 3), round(x, 3), round(y, 3), b.name,
                         "x" if b.along_x else "y", b.i, b.j, b.w, b.d])
    manifest["files"]["parts"] = "parts.csv"
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2))
    (out / "README.txt").write_text(_readme(manifest, result))
    say(f"STL stack written to {out}")
    return manifest


def _readme(manifest: Dict, result) -> str:
    s = result.stats
    lines = [f"Legolize it - STL stack of {Path(str(result.settings.input)).name}",
             f"{s['bricks']} bricks in {s['layers']} layers, {s['size_mm'][0]} x {s['size_mm'][1]} x {s['size_mm'][2]} mm, "
             f"layer height {manifest['layer_height_mm']} mm.", ""]
    if "layers" in manifest["files"]:
        lines += ["layers/: print every layer_NNN.stl as it lies (studs up). Build from layer_000 upwards: each slab's",
                  "sockets go onto the studs of the slab below. A layer with several separate pieces prints them all.",
                  f"Socket play: {result.system.socket_clearance} mm per side - print layer_000 and layer_001 first and",
                  "adjust socket_clearance if the fit is too tight or too loose.", ""]
    if "bricks" in manifest["files"]:
        lines += ["bricks/: one STL per brick size; print the number given below. parts.csv is the building plan",
                  "(layer, position, size and direction of every brick)."]
        lines += [f"  {b['size']:>5} x {b['count']}" for b in manifest["files"]["bricks"]]
        lines.append("")
    if s.get("floating"):
        lines.append(f"Warning: {s['floating']} bricks are not connected to the bottom layer (they would fall off).")
    return "\n".join(lines) + "\n"
