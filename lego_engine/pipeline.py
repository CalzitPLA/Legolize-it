"""From an input file to bricks: load -> place -> fill fraction -> occupancy -> layout -> colours and numbers.

`Legolizer` keeps the expensive steps cached, so the window can re-run the cheap ones at once: a new threshold, shell,
catalogue or colour only redoes the layout (milliseconds to a second); a new size, up axis, unit or sampling redoes the
voxelization; a new input or deflection reloads the part.

Shaped and special pieces (`shapes`, `special`; lego_engine.shapes): the cells near the surface are measured finely
(cached with the voxelization), the pieces are chosen where they fit better than bricks, and the bricks are laid around
them.
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Callable, Dict, Optional, Tuple

import numpy as np

from .analysis import stats as model_stats
from .layout import BrickModel, check_cover, layout_connected
from .mesh_io import load_part
from .palette import NAMES, PALETTE, brick_colours, snap_index, surface_colours
from .shapes import FAMILIES, Fit, deviation, fine_samples, fit_pieces, parse_families, special_cells, special_pieces
from .system import DEFAULT_SIZES, BrickSystem, normalise_sizes
from .voxelize import THIN_MODES, Grid, Placement, fill_fraction, hollow, occupancy, place, surface_cells, thin_cells

RESULTS = Path.home() / "legolize_results"


@dataclass(frozen=True)
class LegoSettings:
    input: str = ""
    up: str = "z"                          # the input's up axis: z, y, x, -z, -y, -x
    size_mode: str = "studs"               # "studs": studs along the longest side; "scale": a factor (1 = mm)
    size: float = 16.0
    unit: str = "brick"                    # layer height: "brick" (9.6) or "plate" (3.2)
    threshold: float = 0.5                 # share of material a cell needs to become a brick
    samples: int = 3                       # sub-samples per cell and axis (3 -> 27 points per cell)
    shell: int = 0                         # 0 = solid; n = keep a shell n cells thick
    thin_walls: str = "auto"               # "auto": surface cells where the material is too thin for any cell (sheet
                                           # metal, profiles); "all": every cell the surface passes; "off"
    sizes: Tuple[Tuple[int, int], ...] = DEFAULT_SIZES
    max_length: int = 8                    # longest brick in studs (0 = the catalogue's)
    bridge_weight: float = 1.5             # layout: reward for bricks spanning bricks below
    shapes: Tuple[str, ...] = ()           # shaped pieces: families of lego_engine.shapes.FAMILIES (slope, curve, round,
                                           # dome, inverted); () = bricks only
    special: bool = False                  # special pieces (the input's exact geometry) where nothing else fits
    special_tolerance: float = 0.3         # ... where the best brick or piece leaves more than this share of a cell wrong
    colour_mode: str = "auto"              # lego_engine.palette.MODES ("auto": the input's colours if it has any)
    single_colour: str = "red"             # mode "single": a palette name
    fade: str = "sunset"                   # mode "fade": a preset of lego_engine.palette.FADES
    snap_colours: bool = True              # mode "source": snap the surface colours to the brick palette
    deflection: float = 0.05               # STEP tessellation, mm
    system: BrickSystem = field(default_factory=BrickSystem)

    def to_dict(self) -> Dict:
        d = asdict(self)
        d["sizes"] = [list(s) for s in self.sizes]
        return d

    @classmethod
    def from_dict(cls, d: Dict) -> "LegoSettings":
        d = dict(d)
        d["system"] = BrickSystem.from_dict(d.get("system", {}))
        if "sizes" in d:
            d["sizes"] = tuple(tuple(s) for s in d["sizes"])
        if "shapes" in d:
            d["shapes"] = parse_families(d["shapes"])
        if isinstance(d.get("thin_walls"), bool):                     # results of 2026-11-11 stored a bool
            d["thin_walls"] = "all" if d["thin_walls"] else "off"
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in d.items() if k in known})


@dataclass
class LegoResult:
    settings: LegoSettings
    mesh: object                    # trimesh.Trimesh, the input in its own units
    placed_vertices: np.ndarray     # the input's vertices in model space (mm, on the grid)
    placement: Placement
    grid: Grid
    fraction: np.ndarray            # (nx, ny, nz) share of material per cell
    occupancy: np.ndarray           # (nx, ny, nz) bool: cells that became bricks
    model: BrickModel
    stats: Dict
    source_colours: Optional[np.ndarray] = None     # per brick, from a coloured input
    timings: Dict[str, float] = field(default_factory=dict)

    @property
    def system(self) -> BrickSystem:
        return self.settings.system

    @property
    def faces(self) -> np.ndarray:
        return np.asarray(self.mesh.faces)

    def colours(self, mode: Optional[str] = None, fade: Optional[str] = None, single: Optional[str] = None) -> np.ndarray:
        s = self.settings
        return brick_colours(self.model, mode or s.colour_mode, self.source_colours, single or s.single_colour,
                             fade or s.fade)

    def colour_mode(self, mode: Optional[str] = None) -> str:
        """The mode drawn for `mode` (default the settings'): "auto" becomes "source" or "size"."""
        from .palette import resolve
        return resolve(mode or self.settings.colour_mode, self.source_colours)

    def to_dict(self) -> Dict:
        from .analysis import connectivity
        return {"legolize": 1, "settings": self.settings.to_dict(), "placement": self.placement.to_dict(),
                "grid": self.grid.to_dict(), "stats": self.stats, "timings": self.timings,
                "bricks": [b.to_list() for b in self.model.bricks],          # layer, i, j, w (x), d (y)
                "kinds": [b.kind for b in self.model.bricks],                # "" brick, a shaped piece's key, "special"
                "turns": [b.rot for b in self.model.bricks],                 # shaped pieces: x 90 degrees about z
                "colours": np.round(self.colours(), 4).tolist(),
                "floating_bricks": connectivity(self.model)["floating"]}

    def save_json(self, path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=1))
        return path


def default_out_dir(settings: LegoSettings) -> Path:
    stem = Path(settings.input).stem or "part"
    return RESULTS / stem / time.strftime("%Y%m%d-%H%M%S")


class Legolizer:
    """The pipeline with caches (see the module docstring). `run(settings)` -> LegoResult."""

    def __init__(self, progress: Optional[Callable[[str], None]] = None):
        self.say = progress or (lambda _t: None)
        self._mesh_key = self._mesh = None
        self._vox_key = self._vox = None
        self._surf_key = self._surf = None
        self._fine_key = self._fine = None
        self._solid_key = self._solid = None

    def mesh(self, settings: LegoSettings):
        key = (str(Path(settings.input).resolve()), Path(settings.input).stat().st_mtime, settings.deflection)
        if key != self._mesh_key:
            self.say(f"loading {Path(settings.input).name} ...")
            self._mesh, self._mesh_key = load_part(settings.input, settings.deflection), key
            self._vox_key = None
        return self._mesh

    def voxelize(self, settings: LegoSettings):
        mesh = self.mesh(settings)
        sysd = settings.system
        cell = np.array([sysd.pitch, sysd.pitch, sysd.layer_height(settings.unit)])
        key = (self._mesh_key, settings.up, settings.size_mode, float(settings.size), settings.unit, int(settings.samples),
               tuple(cell))
        if key != self._vox_key:
            placement, grid = place(mesh.vertices, cell, settings.size_mode, settings.size, settings.up)
            placed = placement.apply(mesh.vertices)
            nx, ny, nz = grid.shape
            self.say(f"voxelizing: grid {nx} x {ny} x {nz} cells, {settings.samples ** 3} samples per cell ...")
            t = time.time()
            frac = fill_fraction(placed, np.asarray(mesh.faces), grid, settings.samples)
            self._vox = (placement, grid, placed, frac, time.time() - t)
            self._vox_key = key
        return self._vox

    def touched(self, settings: LegoSettings) -> np.ndarray:
        """The cells the surface passes through (cached with the voxelization)."""
        _placement, grid, placed, _frac, _t = self.voxelize(settings)
        if self._surf_key != self._vox_key:
            self._surf = surface_cells(placed, np.asarray(self._mesh.faces), grid)
            self._surf_key = self._vox_key
        return self._surf

    def release(self):
        """Drop the caches (the input's manifold solid among them - nanobind reports it as leaked if it outlives the
        interpreter's shutdown)."""
        self._mesh_key = self._vox_key = self._surf_key = self._fine_key = self._solid_key = None
        self._mesh = self._vox = self._surf = self._fine = self._solid = None

    def fine(self, settings: LegoSettings):
        """The cells near the surface measured finely (lego_engine.shapes.fine_samples; cached)."""
        _placement, grid, placed, frac, _t = self.voxelize(settings)
        if self._fine_key != self._vox_key:
            t = time.time()
            self._fine = fine_samples(placed, np.asarray(self._mesh.faces), grid, frac)
            self.say(f"surface cells measured finely: {len(self._fine.inside)} cells ({time.time() - t:.2f} s)")
            self._fine_key = self._vox_key
        return self._fine

    def solid(self, settings: LegoSettings):
        """The input as a closed solid for cutting special pieces (None when it is not closed; cached)."""
        _placement, _grid, placed, _frac, _t = self.voxelize(settings)
        if self._solid_key != self._vox_key:
            from .geometry import input_solid
            self._solid = input_solid(placed, np.asarray(self._mesh.faces))
            if self._solid is None:
                self.say("the input is not a closed solid: special pieces are built from the fine samples (blocky)")
            self._solid_key = self._vox_key
        return self._solid

    def run(self, settings: LegoSettings) -> LegoResult:
        t0 = time.time()
        mesh = self.mesh(settings)
        t_load = time.time() - t0
        placement, grid, placed, frac, t_vox = self.voxelize(settings)
        t1 = time.time()
        occ = occupancy(frac, settings.threshold)
        if settings.thin_walls not in THIN_MODES:
            raise ValueError(f"thin_walls must be one of {THIN_MODES}, not {settings.thin_walls!r}")
        thin = 0
        extra = np.zeros_like(occ)
        if settings.thin_walls != "off":
            touched = self.touched(settings)
            extra = (touched & ~occ) if settings.thin_walls == "all" else thin_cells(occ, touched)
            thin = int(extra.sum())
            occ = occ | extra
        solid_occ = occ
        occ = hollow(occ, settings.shell)
        base_occ = occ
        sizes = normalise_sizes(settings.sizes, settings.max_length)
        families = parse_families(settings.shapes)
        pieces, fit = [], None
        if families or settings.special:
            fine = self.fine(settings)
            allowed = ~extra & ~(solid_occ & ~occ)           # not thin-wall cells, not the inside of a hollow model
            sysd = settings.system
            seat_min = round(min(0.95, (sysd.socket_depth + 0.6) / float(grid.cell[2])), 3)   # room for a socket
            fit = fit_pieces(fine, occ, allowed, families, seat_min=seat_min) if families else \
                Fit([], np.zeros_like(occ), np.zeros_like(occ), [])
            pieces = list(fit.pieces)
            occ = occ | fit.taken
            if settings.special:
                cells = special_cells(fine, occ, allowed, fit.taken, fit.forbid, settings.special_tolerance,
                                      thin=extra)
                pieces += special_pieces(cells, fine, sizes)
                occ = occ | cells
        cell_colours = self._cell_colours(mesh, placed, occ, grid, settings)
        labels = None
        if settings.colour_mode in ("source", "auto") and cell_colours is not None and cell_colours[1] is not None:
            labels = cell_colours[1]                     # visible cells: one colour per brick (a mosaic's rule)
        model = layout_connected(occ, sizes, 0, settings.bridge_weight, labels=labels, pieces=pieces)
        if pieces:
            model, occ, dropped = self._drop_floating_pieces(model, occ, base_occ, pieces, sizes, settings, labels)
            if dropped:
                self.say(f"{dropped} pieces that would not hold were replaced by bricks")
        if any(b.is_special for b in model.bricks):
            from .geometry import manifold_mesh, special_solid
            solid = self.solid(settings)
            fine = self.fine(settings)
            for n, b in enumerate(model.bricks):
                if b.is_special:
                    model.special_meshes[n] = manifold_mesh(special_solid(b, grid, solid, fine))
        problems = check_cover(model, occ)
        if problems:
            raise RuntimeError("the brick layout is not exact: " + "; ".join(problems[:5]))
        st = model_stats(model, settings.system, settings.unit)
        st["max_fill"] = round(float(frac.max()), 3) if frac.size else 0.0
        if model.bricks:                       # how far from the part (on the fine samples near the surface)
            st["deviation"] = round(deviation(model, self.fine(settings), float(frac.sum()), solid_occ & ~base_occ), 4)
        st["thin_cells"] = thin
        t_layout = time.time() - t1
        if not model.bricks:
            self.say(f"no bricks: no cell is filled to the threshold {settings.threshold:g} (the fullest is "
                     f"{st['max_fill']:.0%})" + ("" if settings.thin_walls == "all" else
                     " - try thin walls 'all', a lower threshold, or a bigger size"))
        else:
            if thin:
                self.say(f"thin walls: {thin} cells where the material is too thin to fill a cell")
            if st["layers"] == 1 and st["bricks"] > 1:
                self.say("one layer only: bricks side by side do not hold together - plates (3.2 mm) or a bigger size "
                         "give more layers")
        source = self._source_colours(model, cell_colours, settings)
        extra_text = "".join(f", {st[k]} {k}" for k in ("shaped", "special") if st.get(k))
        self.say(f"{st['bricks']} bricks{extra_text} in {st['layers']} layers ({st['size_mm'][0]} x {st['size_mm'][1]} x "
                 f"{st['size_mm'][2]} mm)" + ("" if st["stable"] else f"; {st['groups']} groups, {st['floating']} floating"))
        return LegoResult(settings, mesh, placed, placement, grid, frac, occ, model, st, source,
                          {"load_s": round(t_load, 3), "voxelize_s": round(t_vox, 3), "layout_s": round(t_layout, 3),
                           "total_s": round(time.time() - t0, 3)})

    @staticmethod
    def _drop_floating_pieces(model, occ, base_occ, pieces, sizes, settings, labels, rounds: int = 2):
        """Pieces in floating groups (they hold only on each other, or on nothing) go: their cells become bricks again
        (the cells a piece added become empty) and the bricks are laid again. Returns (model, occ, pieces dropped)."""
        from .analysis import connectivity
        dropped = 0
        for _ in range(rounds):
            floating = set(connectivity(model)["floating"])
            gone = {(b.layer, b.i, b.j, b.kind) for n, b in enumerate(model.bricks) if b.kind and n in floating}
            if not gone:
                break
            keep = []
            for pc in pieces:
                b = pc[0]
                if (b.layer, b.i, b.j, b.kind) in gone:
                    sl = (slice(b.i, b.i + b.w), slice(b.j, b.j + b.d), b.layer)
                    occ = occ.copy()
                    occ[sl] = base_occ[sl]
                    dropped += 1
                else:
                    keep.append(pc)
            pieces = keep
            if labels is not None:
                labels = np.where(occ, labels, -2)
            model = layout_connected(occ, sizes, 0, settings.bridge_weight, labels=labels, pieces=pieces)
        return model, occ, dropped

    @staticmethod
    def _cell_colours(mesh, placed, occ: np.ndarray, grid: Grid, settings: LegoSettings):
        """(colours, labels) of the occupied cells, or None for an input without colours: colours (nx, ny, nz, 3) the
        input surface's colour nearest to each cell's centre; labels (nx, ny, nz) int - with snapping, the index into
        palette.NAMES for the cells one can see (an empty neighbour), -1 for the cells inside (any colour will do),
        -2 for empty cells; without snapping None."""
        cells = np.argwhere(occ)
        if not len(cells):
            return None
        col = surface_colours(mesh, grid.origin + (cells + 0.5) * grid.cell, placed)
        if col is None:
            return None
        colours = np.zeros(occ.shape + (3,))
        colours[tuple(cells.T)] = col
        if not settings.snap_colours:
            return colours, None
        from scipy import ndimage
        inside = ndimage.binary_erosion(occ, structure=ndimage.generate_binary_structure(3, 1), border_value=0)
        labels = np.full(occ.shape, -2, np.int32)
        labels[tuple(cells.T)] = snap_index(col)
        snapped = labels.copy()
        labels[inside] = -1
        return colours, labels, snapped

    @staticmethod
    def _source_colours(model: BrickModel, cell_colours, settings: LegoSettings) -> Optional[np.ndarray]:
        """Per brick: with snapping the palette colour most of its visible cells have (all its cells if none is
        visible) - a majority, not a mean, so a brick at a border does not turn into a mix of both sides; without
        snapping the mean colour of its cells. None for an input without colours."""
        if not model.bricks or cell_colours is None:
            return None
        colours = cell_colours[0]
        out = np.zeros((len(model.bricks), 3))
        for n, b in enumerate(model.bricks):
            sl = (slice(b.i, b.i + b.w), slice(b.j, b.j + b.d), b.layer)
            if cell_colours[1] is None:
                out[n] = colours[sl].reshape(-1, 3).mean(axis=0)
                continue
            vis = cell_colours[1][sl].ravel()
            pick = vis[vis >= 0]
            if not pick.size:
                pick = cell_colours[2][sl].ravel()
                pick = pick[pick >= 0]
            out[n] = PALETTE[NAMES[int(np.bincount(pick).argmax())]]
        return out


def run(settings: LegoSettings, progress=None) -> LegoResult:
    return Legolizer(progress).run(settings)
