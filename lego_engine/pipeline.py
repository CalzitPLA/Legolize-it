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

STYLES = ("exact", "artsy")

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
    colour_sizes: Tuple[Tuple[str, Tuple[Tuple[int, int], ...]], ...] = ()   # per palette colour the brick sizes it
                                           # may use (those a shop sells in it - lego_engine.shop); others: `sizes`
    recolour: Tuple[Tuple[str, str], ...] = ()   # source colours: palette colour -> the one to build it in (after
                                           # snapping; e.g. a model in two colours, to share packs with others)
    details: Tuple[str, ...] = ()          # lego_engine.details.DETAILS: tiles (smooth tops), rods (round 1x1
                                           # columns), bars (on clips, for free-standing runs); () = none
    style: str = "exact"                   # "exact": as close to the part as the cells allow; "artsy": built with
                                           # craft (lego_engine.artsy - axle stacks, printed details, symmetry)
    artsy_symmetry: str = "auto"           # artsy: mirror planes where the part nearly has them ("auto") or "off"
    artsy_axles: bool = True               # artsy: round bodies as parts threaded on an axle
    artsy_printed: bool = True             # artsy: small detailed parts as printed pieces
    artsy_base: str = "auto"               # artsy: a display base when the model stands on parts apart ("auto"),
                                           # always ("on") or never ("off")
    technic_colours: Tuple[Tuple[str, Tuple[str, ...]], ...] = ()   # artsy: per Technic part (lego_engine.technic) the
                                           # colours a shop sells it in - axle stacks use only those; () = any
    parts_range: str = "standard"          # the parts' range (lego_engine.catalogues): standard, lego, bluebrixx -
                                           # a record of the choice; `sizes` and `parts_off` carry what it means
    parts_off: Tuple[str, ...] = ()        # single parts left out (ids of lego_engine.catalogues: "tile_4x2", "bar_5",
                                           # "round_1", "curve_2x1", "gear_24", "axle_32", "support_1x1", "2x4" ...)

    def to_dict(self) -> Dict:
        d = asdict(self)
        d["sizes"] = [list(s) for s in self.sizes]
        d["colour_sizes"] = {c: [list(s) for s in sz] for c, sz in self.colour_sizes}
        d["recolour"] = dict(self.recolour)
        d["technic_colours"] = {k: list(v) for k, v in self.technic_colours}
        return d

    @classmethod
    def from_dict(cls, d: Dict) -> "LegoSettings":
        d = dict(d)
        d["system"] = BrickSystem.from_dict(d.get("system", {}))
        if "sizes" in d:
            d["sizes"] = tuple(tuple(s) for s in d["sizes"])
        if "colour_sizes" in d:
            cs = d["colour_sizes"]
            d["colour_sizes"] = tuple((c, tuple(tuple(s) for s in sz)) for c, sz in (cs.items() if isinstance(cs, dict)
                                                                                     else cs))
        if "recolour" in d:
            rc = d["recolour"]
            d["recolour"] = tuple(sorted((rc.items() if isinstance(rc, dict) else (tuple(x) for x in rc))))
        if "shapes" in d:
            d["shapes"] = parse_families(d["shapes"])
        if "details" in d:
            from .details import parse_details
            d["details"] = parse_details(d["details"])
        if "parts_off" in d:
            d["parts_off"] = tuple(d["parts_off"] or ())
        if "technic_colours" in d:
            tc = d["technic_colours"]
            d["technic_colours"] = tuple((k, tuple(v)) for k, v in (tc.items() if isinstance(tc, dict) else tc))
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
                "floating_bricks": connectivity(self.model)["floating"],
                "assemblies": [a.to_dict() for a in getattr(self.model, "assemblies", None) or ()]}

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
        from .artsy import base_layers
        pad = base_layers(settings)                      # artsy: room for a display base under the model
        key = (self._mesh_key, settings.up, settings.size_mode, float(settings.size), settings.unit, int(settings.samples),
               tuple(cell), pad)
        if key != self._vox_key:
            placement, grid = place(mesh.vertices, cell, settings.size_mode, settings.size, settings.up)
            if pad:
                placement = Placement(placement.up, placement.scale, placement.offset + np.array([0.0, 0.0, pad * cell[2]]))
                grid = Grid(grid.origin, grid.cell, (grid.shape[0], grid.shape[1], grid.shape[2] + pad))
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
        if settings.thin_walls not in THIN_MODES:
            raise ValueError(f"thin_walls must be one of {THIN_MODES}, not {settings.thin_walls!r}")
        if settings.style not in STYLES:
            raise ValueError(f"style must be one of {STYLES}, not {settings.style!r}")
        thin = 0
        art = None
        if settings.style == "artsy":                # the part read as a builder would (lego_engine.artsy)
            from .artsy import prepare
            touched = self.touched(settings) if settings.thin_walls != "off" else None
            art = prepare(mesh, placed, grid, frac, touched, settings, settings.unit, settings.threshold, self.say)
            occ = art.occupancy
            extra = np.zeros_like(occ)
        else:
            occ = occupancy(frac, settings.threshold)
            extra = np.zeros_like(occ)
            if settings.thin_walls != "off":
                touched = self.touched(settings)
                extra = (touched & ~occ) if settings.thin_walls == "all" else thin_cells(occ, touched)
                thin = int(extra.sum())
                occ = occ | extra
        solid_occ = occ
        occ = hollow(occ, settings.shell)
        base_occ = occ
        off = set(settings.parts_off)
        sizes = normalise_sizes(tuple(s for s in settings.sizes if s in ((1, 1),) or f"{min(s)}x{max(s)}" not in off),
                                settings.max_length)
        families = parse_families(settings.shapes)
        if art is not None and not families and settings.unit == "brick":
            families = ("curve", "round")                # artsy: round and curved pieces where they fit (brick layers:
                                                         # in plate layers they are not parts one can buy)
        pieces, fit = [], None
        if families or settings.special or (art is not None and art.printed is not None and art.printed.any()):
            fine = self.fine(settings)
            allowed = ~extra & ~(solid_occ & ~occ)           # not thin-wall cells, not the inside of a hollow model
            if art is not None and art.contacts is not None:
                allowed &= ~art.contacts                      # artsy: studded bricks where an assembly holds
            sysd = settings.system
            seat_min = round(min(0.95, (sysd.socket_depth + 0.6) / float(grid.cell[2])), 3)   # room for a socket
            fit = fit_pieces(fine, occ, allowed, families, seat_min=seat_min, off=off) if families else \
                Fit([], np.zeros_like(occ), np.zeros_like(occ), [])
            pieces = list(fit.pieces)
            occ = occ | fit.taken
            if settings.special:
                cells = special_cells(fine, occ, allowed, fit.taken, fit.forbid, settings.special_tolerance,
                                      thin=extra)
                pieces += special_pieces(cells, fine, sizes)
                occ = occ | cells
        if art is not None and art.printed is not None and art.printed.any():   # artsy: printed detail pieces
            laid = np.zeros_like(occ)
            for b, _st, _se in pieces:
                laid[b.i:b.i + b.w, b.j:b.j + b.d, b.layer] = True
            pieces += special_pieces(art.printed & ~laid, self.fine(settings), sizes)
            occ = occ | art.printed
        from .details import apply as apply_details, bar_pieces, parse_details
        details = parse_details(settings.details)
        if art is not None and not details:
            details = ("tiles", "rods", "bars")         # artsy: the details on
        if "bars" in details:
            laid = np.zeros_like(occ)
            for b, _st, _se in pieces:
                laid[b.i:b.i + b.w, b.j:b.j + b.d, b.layer] = True
            from .shapes import BAR_LENGTHS
            lengths = [L for L in BAR_LENGTHS if f"bar_{L}" not in off]
            if lengths:
                pieces += bar_pieces(occ, ~laid, lengths)
        cell_colours = self._artsy_colours(self._cell_colours(mesh, placed, occ, grid, settings), art)
        labels = None
        if settings.colour_mode in ("source", "auto") and cell_colours is not None and cell_colours[1] is not None:
            labels = cell_colours[1]                     # visible cells: one colour per brick (a mosaic's rule)
            if art is not None:                          # artsy: no colour specks
                from .artsy import block_colours
                labels = block_colours(labels, occ)
        colour_sizes = colour_size_map(settings, sizes) if labels is not None else None
        model = layout_connected(occ, sizes, 0, settings.bridge_weight, labels=labels, pieces=pieces,
                                 colour_sizes=colour_sizes)
        if pieces:
            model, occ, dropped = self._drop_floating_pieces(model, occ, base_occ, pieces, sizes, settings, labels,
                                                             colour_sizes=colour_sizes)
            if dropped:
                self.say(f"{dropped} pieces that would not hold were replaced by bricks")
        if art is not None:
            model.assemblies = list(art.assemblies)
            model, occ, cell_colours, pieces = self._artsy_repair(model, occ, cell_colours, pieces, sizes, settings,
                                                                  colour_sizes, art, mesh, placed, grid)
        detail_counts = {}
        if details:
            model, detail_counts = apply_details(model, details, settings.unit, off)
            detail_counts["bars"] = sum(1 for b in model.bricks if b.kind.startswith("bar_"))
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
        if details:
            st["details"] = detail_counts
        t_layout = time.time() - t1
        if model.empty:
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
        if art is not None:
            from .artsy import craft
            st["craft"] = craft(model, source, art, st)
            if art.assemblies:
                self.say(f"artsy: {len(art.assemblies)} axle stack(s), {st['craft']['technic_parts']} Technic parts")
        extra_text = "".join(f", {st[k]} {k}" for k in ("shaped", "special") if st.get(k))
        self.say(f"{st['bricks']} bricks{extra_text} in {st['layers']} layers ({st['size_mm'][0]} x {st['size_mm'][1]} x "
                 f"{st['size_mm'][2]} mm)" + ("" if st["stable"] else f"; {st['groups']} groups, {st['floating']} floating"))
        return LegoResult(settings, mesh, placed, placement, grid, frac, occ, model, st, source,
                          {"load_s": round(t_load, 3), "voxelize_s": round(t_vox, 3), "layout_s": round(t_layout, 3),
                           "total_s": round(time.time() - t0, 3)})

    @staticmethod
    def _artsy_colours(cell_colours, art):
        """Artsy: the display base in its own colour (lego_engine.artsy.BASE_COLOUR), whatever surface is nearest."""
        if art is None or art.base is None or cell_colours is None or cell_colours[1] is None:
            return cell_colours
        from .artsy import BASE_COLOUR
        colours, labels, snapped = (np.array(x, copy=True) for x in cell_colours)
        k = NAMES.index(BASE_COLOUR)
        base = art.base
        colours[base] = PALETTE[BASE_COLOUR]
        labels[base & (labels >= 0)] = k
        snapped[base] = k
        return colours, labels, snapped

    def _artsy_repair(self, model, occ, cell_colours, pieces, sizes, settings, colour_sizes, art, mesh, placed, grid,
                      rounds: int = 8):
        """Artsy: a model that stands. While parts float: groups under 4 cells go, larger ones get two posts under
        their lowest bricks down to the next part or the floor; then the bricks are laid again (colours of the new
        cells from the input, as everywhere)."""
        from .analysis import connectivity
        from .artsy import base_layers, block_colours
        from .assembly import cells_mask
        floor = 0 if art.base is not None else base_layers(settings)
        taken = cells_mask(model.assemblies, occ.shape)
        for _ in range(rounds):
            con = connectivity(model)
            floating = set(con["floating"])
            if not floating:
                break
            groups: Dict[int, list] = {}
            for n in floating:
                groups.setdefault(con["group_of"][n], []).append(n)
            occ = occ.copy()
            for members in groups.values():
                bricks = [model.bricks[n] for n in members]
                mine = set(members)
                cells = sum(b.w * b.d for b in bricks)
                if cells < 4:
                    for b in bricks:
                        occ[b.i:b.i + b.w, b.j:b.j + b.d, b.layer] = False
                    continue
                # posts only where the way down ends on a brick or the floor - not on an axle sub-assembly
                done = []
                for b in sorted(bricks, key=lambda b: (b.layer, -b.w * b.d)):
                    for ci in range(b.i, b.i + b.w):
                        for cj in range(b.j, b.j + b.d):
                            if b.layer <= floor or any(abs(ci - a) + abs(cj - c) < 3 for a, c in done):
                                continue
                            kk = b.layer - 1
                            while kk >= floor and not occ[ci, cj, kk] and not taken[ci, cj, kk]:
                                kk -= 1
                            if kk >= floor and (taken[ci, cj, kk] or int(model.ids[ci, cj, kk]) in mine):
                                continue                                   # on an assembly, or on itself
                            occ[ci, cj, kk + 1:b.layer] = True
                            done.append((ci, cj))
                    if len(done) >= 2:
                        break
                if not done and cells < 200:                         # nothing to stand on: a small bit goes
                    for b in bricks:
                        occ[b.i:b.i + b.w, b.j:b.j + b.d, b.layer] = False
            pieces = [pc for pc in pieces if occ[pc[0].i:pc[0].i + pc[0].w, pc[0].j:pc[0].j + pc[0].d, pc[0].layer].all()]
            cell_colours = self._artsy_colours(self._cell_colours(mesh, placed, occ, grid, settings), art)
            labels = None
            if settings.colour_mode in ("source", "auto") and cell_colours is not None and cell_colours[1] is not None:
                labels = block_colours(cell_colours[1], occ)
            assemblies = model.assemblies
            model = layout_connected(occ, sizes, 0, settings.bridge_weight, labels=labels, pieces=pieces,
                                     colour_sizes=colour_sizes)
            model.assemblies = assemblies
        return model, occ, cell_colours, pieces

    @staticmethod
    def _drop_floating_pieces(model, occ, base_occ, pieces, sizes, settings, labels, rounds: int = 2,
                              colour_sizes=None):
        """Pieces that do not hold go: those in floating groups, and those in groups without a single brick (they hold
        only on each other, or stand alone on the ground - 2026-11-12: the special pieces along a cylinder's round
        bottom edge, too little material at their faces for a stud, were 8 "groups" of one piece each). Their cells
        become bricks again (the cells a piece added become empty) and the bricks are laid again. Returns (model, occ,
        pieces dropped)."""
        from .analysis import connectivity
        dropped = 0
        for _ in range(rounds):
            con = connectivity(model)
            floating = set(con["floating"])
            with_bricks = {g for g, b in zip(con["group_of"], model.bricks) if b.is_brick}
            gone = {(b.layer, b.i, b.j, b.kind) for n, b in enumerate(model.bricks)
                    if b.kind and (n in floating or (with_bricks and con["group_of"][n] not in with_bricks))}
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
            model = layout_connected(occ, sizes, 0, settings.bridge_weight, labels=labels, pieces=pieces,
                                     colour_sizes=colour_sizes)
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
        idx = snap_index(col)
        if settings.recolour:                            # the colours to build in
            to = np.arange(len(NAMES))
            for src, dst in settings.recolour:
                if src in NAMES and dst in NAMES:
                    to[NAMES.index(src)] = NAMES.index(dst)
            idx = to[idx]
        labels[tuple(cells.T)] = idx
        snapped = labels.copy()
        labels[inside] = -1
        return colours, labels, snapped

    @staticmethod
    def _source_colours(model: BrickModel, cell_colours, settings: LegoSettings) -> Optional[np.ndarray]:
        """Per brick: with snapping the palette colour most of its visible cells have (all its cells if none is
        visible) - a majority, not a mean, so a brick at a border does not turn into a mix of both sides; without
        snapping the mean colour of its cells. None for an input without colours. With `colour_sizes` a brick nobody
        sees takes the most common colour of its cells that comes in its size, else the model's most common such."""
        if not model.bricks or cell_colours is None:
            return None
        colours = cell_colours[0]
        cs = colour_size_map(settings, model.sizes) if cell_colours[1] is not None else None
        common = None
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
                if cs and b.is_brick:
                    key = (min(b.w, b.d), max(b.w, b.d))
                    if common is None:
                        seen = np.bincount(cell_colours[2][cell_colours[2] >= 0], minlength=len(NAMES))
                        common = [c for c in np.argsort(-seen) if seen[c]]          # the model's colours only
                    counts = np.bincount(pick, minlength=len(NAMES))
                    ok = [c for c in np.argsort(-counts) if counts[c] and key in cs.get(int(c), (key,))]
                    ok = ok or [c for c in common if key in cs.get(int(c), (key,))]
                    if ok:
                        out[n] = PALETTE[NAMES[int(ok[0])]]
                        continue
            out[n] = PALETTE[NAMES[int(np.bincount(pick).argmax())]]
        return out


def colour_size_map(settings: LegoSettings, sizes) -> Optional[Dict[int, frozenset]]:
    """settings.colour_sizes as {palette index: frozenset of (short, long)} within `sizes` (1x1 always), or None."""
    if not settings.colour_sizes:
        return None
    allowed = {(min(a, b), max(a, b)) for a, b in sizes}
    out = {}
    for name, sz in settings.colour_sizes:
        if name in NAMES:
            out[NAMES.index(name)] = frozenset({(min(a, b), max(a, b)) for a, b in sz} & allowed | {(1, 1)})
    return out


def run(settings: LegoSettings, progress=None) -> LegoResult:
    return Legolizer(progress).run(settings)
