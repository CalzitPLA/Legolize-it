"""From an input file to bricks: load -> place -> fill fraction -> occupancy -> layout -> colours and numbers.

`Legolizer` keeps the expensive steps cached, so the window can re-run the cheap ones at once: a new threshold, shell,
catalogue or colour only redoes the layout (milliseconds to a second); a new size, up axis, unit or sampling redoes the
voxelization; a new input or deflection reloads the part.
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
from .palette import brick_colours, snap, surface_colours
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
    colour_mode: str = "size"              # lego_engine.palette.MODES
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

    def colours(self, mode: Optional[str] = None) -> np.ndarray:
        return brick_colours(self.model, mode or self.settings.colour_mode, self.source_colours)

    def to_dict(self) -> Dict:
        from .analysis import connectivity
        return {"legolize": 1, "settings": self.settings.to_dict(), "placement": self.placement.to_dict(),
                "grid": self.grid.to_dict(), "stats": self.stats, "timings": self.timings,
                "bricks": [b.to_list() for b in self.model.bricks],          # layer, i, j, w (x), d (y)
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
        if settings.thin_walls != "off":
            touched = self.touched(settings)
            extra = (touched & ~occ) if settings.thin_walls == "all" else thin_cells(occ, touched)
            thin = int(extra.sum())
            occ = occ | extra
        occ = hollow(occ, settings.shell)
        sizes = normalise_sizes(settings.sizes, settings.max_length)
        model = layout_connected(occ, sizes, 0, settings.bridge_weight)
        problems = check_cover(model, occ)
        if problems:
            raise RuntimeError("the brick layout is not exact: " + "; ".join(problems[:5]))
        st = model_stats(model, settings.system, settings.unit)
        st["max_fill"] = round(float(frac.max()), 3) if frac.size else 0.0
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
        source = self._source_colours(mesh, placed, model, grid, settings)
        self.say(f"{st['bricks']} bricks in {st['layers']} layers ({st['size_mm'][0]} x {st['size_mm'][1]} x "
                 f"{st['size_mm'][2]} mm)" + ("" if st["stable"] else f"; {st['groups']} groups, {st['floating']} floating"))
        return LegoResult(settings, mesh, placed, placement, grid, frac, occ, model, st, source,
                          {"load_s": round(t_load, 3), "voxelize_s": round(t_vox, 3), "layout_s": round(t_layout, 3),
                           "total_s": round(time.time() - t0, 3)})

    @staticmethod
    def _source_colours(mesh, placed, model: BrickModel, grid: Grid, settings: LegoSettings) -> Optional[np.ndarray]:
        """Per brick: the mean colour of the input surface nearest to its cells (None for an input without colours)."""
        if not model.bricks:
            return None
        cells, owner = [], []
        for n, b in enumerate(model.bricks):
            for a in range(b.i, b.i + b.w):
                for q in range(b.j, b.j + b.d):
                    cells.append((a, q, b.layer))
                    owner.append(n)
        centres = grid.origin + (np.asarray(cells, float) + 0.5) * grid.cell
        col = surface_colours(mesh, centres, placed)
        if col is None:
            return None
        owner = np.asarray(owner)
        out = np.zeros((len(model.bricks), 3))
        np.add.at(out, owner, col)
        out /= np.bincount(owner, minlength=len(model.bricks))[:, None]
        return snap(out) if settings.snap_colours else out


def run(settings: LegoSettings, progress=None) -> LegoResult:
    return Legolizer(progress).run(settings)
