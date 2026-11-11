"""Brick colours: a palette of plastic-brick colours and the colouring modes shared by the window, the FreeCAD export
and the logo.

Modes:
    size    every brick size its own colour (the parts list reads at a glance)
    layer   the colours cycle layer by layer (the stack and its crossing directions show)
    mixed   a colour per brick from its index (a colourful build, neighbours differ)
    source  the colour of the input's surface nearest to the brick, snapped to the palette - for coloured inputs
            (PLY/OBJ/GLB with vertex or face colours); without colours it falls back to "size"
    single  one colour
"""
from __future__ import annotations

from typing import Dict, Optional

import numpy as np

# sRGB, 0..1 - approximations of common brick colours
PALETTE: Dict[str, tuple] = {
    "red": (0.79, 0.10, 0.12), "blue": (0.05, 0.34, 0.69), "yellow": (0.98, 0.79, 0.12),
    "green": (0.14, 0.53, 0.27), "white": (0.95, 0.95, 0.93), "black": (0.13, 0.13, 0.14),
    "orange": (0.95, 0.47, 0.10), "light_grey": (0.64, 0.65, 0.66), "dark_grey": (0.39, 0.40, 0.42),
    "azure": (0.36, 0.72, 0.86), "lime": (0.65, 0.80, 0.15), "tan": (0.89, 0.80, 0.60),
    "dark_tan": (0.58, 0.49, 0.35), "reddish_brown": (0.35, 0.16, 0.09), "medium_nougat": (0.67, 0.49, 0.33),
    "bright_light_orange": (0.97, 0.73, 0.24), "light_yellow": (0.99, 0.91, 0.59), "cream": (0.96, 0.92, 0.80),
    "dark_orange": (0.66, 0.33, 0.12), "light_bluish_grey": (0.63, 0.65, 0.64), "lavender": (0.80, 0.71, 0.85),
    "dark_purple": (0.25, 0.12, 0.33),
}
CYCLE = ["red", "blue", "yellow", "green", "orange", "azure", "lime", "white", "dark_grey", "tan"]
MODES = ("size", "layer", "mixed", "source", "single")


def rgb(name: str) -> np.ndarray:
    return np.asarray(PALETTE[name], float)


def snap(colours: np.ndarray, names=None) -> np.ndarray:
    """Every colour replaced by the nearest palette colour (in a simple perceptual space: linear RGB weighted)."""
    names = list(names or PALETTE)
    pal = np.array([PALETTE[n] for n in names])
    w = np.array([0.30, 0.59, 0.11]) ** 0.5
    d = (((colours[:, None, :] - pal[None, :, :]) * w) ** 2).sum(-1)
    return pal[d.argmin(axis=1)]


def surface_colours(mesh, points: np.ndarray, vertices: Optional[np.ndarray] = None) -> Optional[np.ndarray]:
    """(len(points), 3) colours of the mesh surface nearest to each point, or None when the mesh has no colours.
    `vertices`: the mesh's vertices in the space of `points` (default: the mesh's own)."""
    visual = getattr(mesh, "visual", None)
    if visual is None or getattr(visual, "kind", None) not in ("vertex", "face", "texture"):
        return None
    try:
        if visual.kind == "texture":
            visual = visual.to_color()
        face = np.asarray(visual.face_colors, float)[:, :3] / 255.0
    except Exception:
        return None
    if len(face) != len(mesh.faces) or np.ptp(face, axis=0).max() < 1e-6 and np.allclose(face[0], 102 / 255, atol=0.02):
        return None                                           # trimesh's default grey: no colours
    import igl
    verts = mesh.vertices if vertices is None else vertices
    _, tri, _ = igl.point_mesh_squared_distance(np.ascontiguousarray(points, float),
                                                np.ascontiguousarray(verts, float),
                                                np.ascontiguousarray(mesh.faces, np.int64))
    return face[np.asarray(tri, int)]


def brick_colours(model, mode: str = "size", source: Optional[np.ndarray] = None,
                  single: str = "red") -> np.ndarray:
    """(len(model.bricks), 3) colours. `source`: per-brick colours for mode "source" (LegoResult.source_colours)."""
    n = len(model.bricks)
    if mode == "source" and source is not None and len(source) == n:
        return np.asarray(source, float)
    if mode == "layer":
        return np.array([PALETTE[CYCLE[b.layer % len(CYCLE)]] for b in model.bricks]).reshape(n, 3)
    if mode == "mixed":
        return np.array([PALETTE[CYCLE[(m * 7 + b.layer * 3) % len(CYCLE)]] for m, b in enumerate(model.bricks)]).reshape(n, 3)
    if mode == "single":
        return np.tile(rgb(single), (n, 1))
    names = sorted({b.name for b in model.bricks}, key=lambda s: (-int(s.split("x")[0]) * int(s.split("x")[1]), s))
    colour_of = {s: PALETTE[CYCLE[q % len(CYCLE)]] for q, s in enumerate(names)}
    return np.array([colour_of[b.name] for b in model.bricks]).reshape(n, 3)


def size_colours(model) -> Dict[str, tuple]:
    """{size name: colour} as used by mode "size" (the parts list's swatches)."""
    names = sorted({b.name for b in model.bricks}, key=lambda s: (-int(s.split("x")[0]) * int(s.split("x")[1]), s))
    return {s: PALETTE[CYCLE[q % len(CYCLE)]] for q, s in enumerate(names)}
