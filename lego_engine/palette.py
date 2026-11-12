"""Brick colours: a palette of plastic-brick colours and the colouring modes shared by the window, the FreeCAD export
and the logo.

Modes:
    auto    "source" when the input has colours, else "size" (the default)
    size    every brick size its own colour (the parts list reads at a glance)
    layer   the colours cycle layer by layer (the stack and its crossing directions show)
    mixed   a colour per brick from its index (a colourful build, neighbours differ)
    source  the colour of the input's surface nearest to the brick, snapped to the palette - for coloured inputs
            (PLY/OBJ/GLB with vertex or face colours); without colours it falls back to "size". The layout then also
            keeps every visible brick to one colour (lego_engine.pipeline)

    random  a random brick colour per brick (fixed seed: the same model, the same colours)
    rainbow the rainbow from the bottom layer to the top, in brick colours
    fade    a gradient from the bottom to the top through the stops of a preset (FADES), every brick snapped to the
            nearest brick colour - a build can only use colours that exist
    single  one colour

Colours are matched in CIELAB (distance ~ how different two colours look). The first version weighted R, G, B by their
share of brightness, which almost ignores blue and hue: a navy pram came out black and chrome lavender (2026-11-11).
"""
from __future__ import annotations

from typing import Dict, Optional

import numpy as np

from .system import name_sort_key

# sRGB, 0..1 - approximations of common brick colours
PALETTE: Dict[str, tuple] = {
    "red": (0.79, 0.10, 0.12), "blue": (0.05, 0.34, 0.69), "yellow": (0.98, 0.79, 0.12),
    "green": (0.14, 0.53, 0.27), "white": (0.95, 0.95, 0.93), "black": (0.13, 0.13, 0.14),
    "orange": (0.95, 0.47, 0.10), "light_grey": (0.64, 0.65, 0.66), "dark_grey": (0.39, 0.40, 0.42),
    "azure": (0.36, 0.72, 0.86), "lime": (0.65, 0.80, 0.15), "tan": (0.89, 0.80, 0.60),
    "dark_tan": (0.58, 0.49, 0.35), "reddish_brown": (0.35, 0.16, 0.09), "medium_nougat": (0.67, 0.49, 0.33),
    "bright_light_orange": (0.97, 0.73, 0.24), "light_yellow": (0.99, 0.91, 0.59), "cream": (0.96, 0.92, 0.80),
    "dark_orange": (0.66, 0.33, 0.12), "light_bluish_grey": (0.63, 0.65, 0.64), "lavender": (0.80, 0.71, 0.85),
    "dark_purple": (0.25, 0.12, 0.33), "dark_blue": (0.04, 0.20, 0.39), "medium_blue": (0.35, 0.58, 0.86),
    "sand_blue": (0.38, 0.45, 0.53), "dark_red": (0.45, 0.05, 0.06), "dark_green": (0.09, 0.27, 0.18),
    "light_nougat": (0.96, 0.84, 0.70), "nougat": (0.82, 0.57, 0.41), "pearl_gold": (0.67, 0.50, 0.18),
    "flat_silver": (0.54, 0.53, 0.53), "dark_bluish_grey": (0.42, 0.43, 0.41),
}
NAMES = list(PALETTE)
CYCLE = ["red", "blue", "yellow", "green", "orange", "azure", "lime", "white", "dark_grey", "tan"]
MODES = ("auto", "source", "size", "layer", "mixed", "random", "rainbow", "fade", "single")
RAINBOW = ["dark_red", "red", "orange", "bright_light_orange", "yellow", "lime", "green", "azure", "medium_blue",
           "blue", "dark_purple"]
RANDOM = ["red", "blue", "yellow", "green", "orange", "azure", "lime", "white", "black", "tan", "dark_blue",
          "medium_blue", "dark_red", "lavender", "light_bluish_grey", "dark_bluish_grey", "bright_light_orange",
          "reddish_brown", "dark_green", "cream"]
FADES: Dict[str, tuple] = {
    "sunset": ("dark_red", "red", "orange", "yellow", "light_yellow"),
    "ocean": ("dark_blue", "blue", "medium_blue", "azure", "white"),
    "forest": ("dark_green", "green", "lime", "light_yellow"),
    "greys": ("black", "dark_bluish_grey", "light_bluish_grey", "white"),
    "candy": ("dark_purple", "lavender", "white"),
    "earth": ("reddish_brown", "dark_orange", "medium_nougat", "tan", "cream"),
}


def rgb(name: str) -> np.ndarray:
    return np.asarray(PALETTE[name], float)


def to_lab(colours: np.ndarray) -> np.ndarray:
    """sRGB (0..1) -> CIELAB (D65)."""
    c = np.asarray(colours, float)
    lin = np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)
    xyz = lin @ np.array([[0.4124, 0.3576, 0.1805], [0.2126, 0.7152, 0.0722], [0.0193, 0.1192, 0.9505]]).T
    xyz = xyz / np.array([0.95047, 1.0, 1.08883])
    f = np.where(xyz > (6 / 29) ** 3, np.cbrt(xyz), xyz / (3 * (6 / 29) ** 2) + 4 / 29)
    return np.stack([116 * f[..., 1] - 16, 500 * (f[..., 0] - f[..., 1]), 200 * (f[..., 1] - f[..., 2])], axis=-1)


def snap_index(colours: np.ndarray, names=None) -> np.ndarray:
    """For every colour the index (into `names`, default NAMES) of the nearest palette colour in CIELAB."""
    names = list(names or NAMES)
    pal = to_lab(np.array([PALETTE[n] for n in names]))
    lab = to_lab(np.atleast_2d(colours))
    return ((lab[:, None, :] - pal[None, :, :]) ** 2).sum(-1).argmin(axis=1)


def snap(colours: np.ndarray, names=None) -> np.ndarray:
    """Every colour replaced by the nearest palette colour (CIELAB)."""
    names = list(names or NAMES)
    pal = np.array([PALETTE[n] for n in names])
    return pal[snap_index(colours, names)]


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


def resolve(mode: str, source: Optional[np.ndarray] = None) -> str:
    """The mode actually drawn: "auto" is "source" with the input's colours, else "size"; "source" without colours
    falls back to "size"."""
    if mode in ("auto", "source"):
        return "source" if source is not None else "size"
    return mode


def fade_colours(t: np.ndarray, stops) -> np.ndarray:
    """Colours at t (0..1) along the stops (palette names), blended in CIELAB and snapped to the palette."""
    t = np.clip(np.asarray(t, float), 0, 1)
    lab = to_lab(np.array([PALETTE[s] for s in stops]))
    x = t * (len(stops) - 1)
    a = np.minimum(np.floor(x).astype(int), len(stops) - 2)
    f = (x - a)[:, None]
    mix = lab[a] * (1 - f) + lab[a + 1] * f
    pal = to_lab(np.array([PALETTE[s] for s in NAMES]))
    near = ((mix[:, None, :] - pal[None, :, :]) ** 2).sum(-1).argmin(axis=1)
    return np.array([PALETTE[NAMES[i]] for i in near]).reshape(-1, 3)


def brick_colours(model, mode: str = "size", source: Optional[np.ndarray] = None,
                  single: str = "red", fade: str = "sunset", seed: int = 7) -> np.ndarray:
    """(len(model.bricks), 3) colours. `source`: per-brick colours for mode "source" (LegoResult.source_colours);
    `single`: the colour of mode "single"; `fade`: the preset of mode "fade" (FADES)."""
    n = len(model.bricks)
    mode = resolve(mode, source if source is not None and len(source) == n else None)
    if mode == "source":
        return np.asarray(source, float)
    if mode in ("rainbow", "fade") and n:
        layers = np.array([b.layer for b in model.bricks], float)
        lo, hi = layers.min(), layers.max()
        t = (layers - lo) / max(1.0, hi - lo)
        if mode == "fade":
            return fade_colours(t, FADES.get(fade, FADES["sunset"]))
        return np.array([PALETTE[RAINBOW[min(len(RAINBOW) - 1, int(x * len(RAINBOW)))]] for x in t]).reshape(n, 3)
    if mode == "random":
        rng = np.random.default_rng(seed)
        return np.array([PALETTE[RANDOM[q]] for q in rng.integers(0, len(RANDOM), n)]).reshape(n, 3)
    if mode == "layer":
        return np.array([PALETTE[CYCLE[b.layer % len(CYCLE)]] for b in model.bricks]).reshape(n, 3)
    if mode == "mixed":
        return np.array([PALETTE[CYCLE[(m * 7 + b.layer * 3) % len(CYCLE)]] for m, b in enumerate(model.bricks)]).reshape(n, 3)
    if mode == "single":
        return np.tile(rgb(single if single in PALETTE else "red"), (n, 1))
    names = sorted({b.name for b in model.bricks}, key=name_sort_key)
    colour_of = {s: PALETTE[CYCLE[q % len(CYCLE)]] for q, s in enumerate(names)}
    return np.array([colour_of[b.name] for b in model.bricks]).reshape(n, 3)


def size_colours(model) -> Dict[str, tuple]:
    """{size name: colour} as used by mode "size" (the parts list's swatches)."""
    names = sorted({b.name for b in model.bricks}, key=name_sort_key)
    return {s: PALETTE[CYCLE[q % len(CYCLE)]] for q, s in enumerate(names)}
