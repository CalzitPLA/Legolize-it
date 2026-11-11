"""Loading a part: STL directly (trimesh), STEP/STP through FreeCAD's tessellator (`freecadcmd`, run as a child
process), and the up-axis rotation that makes the part's "up" the brick stack's +z.

Where freecadcmd is (one rule, as in SHaPe ReCo's dataset_gen/src/freecad_path.py):
    1. $LEGOLIZE_FREECADCMD (a path or a command name; if it names no executable, no other FreeCAD is used),
    2. `freecadcmd` on PATH,
    3. /usr/bin/freecadcmd.
Tessellated STEP files are cached in ~/.cache/legolize/step/ by the file's content and the deflection.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tempfile
import warnings
from pathlib import Path
from typing import Optional

import numpy as np

ENV = "LEGOLIZE_FREECADCMD"
FALLBACK = "/usr/bin/freecadcmd"
CACHE = Path(os.environ.get("LEGOLIZE_CACHE", Path.home() / ".cache" / "legolize"))
STEP_SUFFIXES = (".step", ".stp")
MESH_SUFFIXES = (".stl", ".obj", ".ply", ".off", ".glb", ".gltf", ".3mf")
UP_AXES = ("z", "y", "x", "-z", "-y", "-x")

TESSELLATE_SCRIPT = '''
import os, Part, MeshPart
shape = Part.read(os.environ["STEP_IN"])
mesh = MeshPart.meshFromShape(Shape=shape, LinearDeflection=float(os.environ["DEFLECTION"]),
                              AngularDeflection=0.1, Relative=False)
mesh.write(os.environ["STL_OUT"])
'''


def find_freecadcmd() -> Optional[str]:
    chosen = os.environ.get(ENV, "").strip()
    if chosen:
        found = shutil.which(os.path.expanduser(chosen))
        if found is None:
            warnings.warn(f"{ENV}={chosen!r} is not an executable: no FreeCAD is used in its place", stacklevel=2)
        return found
    return shutil.which("freecadcmd") or (FALLBACK if os.path.isfile(FALLBACK) else None)


def clean_env() -> dict:
    """The environment for a FreeCAD child: without our PYTHONPATH/PYTHONHOME, which would mix two Pythons."""
    return {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "PYTHONHOME")}


def tessellate_step(path, deflection: float = 0.05, freecad: Optional[str] = None, cache: bool = True) -> Path:
    """STEP/STP -> STL via FreeCAD; `deflection` is the largest distance of the triangles from the surface (mm)."""
    path = Path(path).resolve()
    key = hashlib.sha1(path.read_bytes() + f"|{deflection:.6g}".encode()).hexdigest()[:16]
    cached = CACHE / "step" / f"{path.stem}_{key}.stl"
    if cache and cached.is_file():
        return cached
    freecad = freecad or find_freecadcmd()
    if freecad is None:
        raise RuntimeError("a STEP input needs FreeCAD: freecadcmd was not found "
                           f"(install FreeCAD or set {ENV})")
    tmp = Path(tempfile.mkdtemp(prefix="legolize_step_"))
    script, out = tmp / "tessellate.py", tmp / (path.stem + ".stl")
    script.write_text(TESSELLATE_SCRIPT)
    env = clean_env()
    env.update(STEP_IN=str(path), STL_OUT=str(out), DEFLECTION=str(deflection))
    proc = subprocess.run([freecad, str(script)], capture_output=True, text=True, timeout=600, env=env)
    if not out.is_file() or out.stat().st_size == 0:
        raise RuntimeError(f"FreeCAD could not tessellate {path.name}: {(proc.stderr or proc.stdout)[-600:]}")
    if not cache:
        return out
    cached.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(out), cached)
    shutil.rmtree(tmp, ignore_errors=True)
    return cached


def load_part(path, deflection: float = 0.05):
    """The part as one trimesh.Trimesh in its own units (taken as mm)."""
    import trimesh
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(p)
    suffix = p.suffix.lower()
    if suffix in STEP_SUFFIXES:
        p = tessellate_step(p, deflection)
    elif suffix not in MESH_SUFFIXES:
        raise ValueError(f"unsupported input {p.name}: STL or STEP (also OBJ/PLY/OFF/3MF/glTF)")
    mesh = trimesh.load(str(p), force="mesh", process=True)
    if len(mesh.faces) == 0:
        raise ValueError(f"{Path(path).name} contains no triangles")
    return mesh


def up_matrix(up: str) -> np.ndarray:
    """3x3 rotation that turns the axis `up` ('z', 'y', 'x', or negative) into +z."""
    if up not in UP_AXES:
        raise ValueError(f"up must be one of {UP_AXES}, not {up!r}")
    r = {
        "z": [[1, 0, 0], [0, 1, 0], [0, 0, 1]],
        "-z": [[1, 0, 0], [0, -1, 0], [0, 0, -1]],     # half turn about x
        "y": [[1, 0, 0], [0, 0, -1], [0, 1, 0]],       # +90 deg about x: y -> z
        "-y": [[1, 0, 0], [0, 0, 1], [0, -1, 0]],      # -90 deg about x: -y -> z
        "x": [[0, 0, -1], [0, 1, 0], [1, 0, 0]],       # -90 deg about y: x -> z
        "-x": [[0, 0, 1], [0, 1, 0], [-1, 0, 0]],      # +90 deg about y: -x -> z
    }[up]
    return np.array(r, dtype=float)
