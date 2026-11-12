<p align="center"><img src="packaging/icons/legolize-it_256.png" width="200" alt="Legolize it: a Golden Teacher mushroom of bricks, not fully built"></p>

# Legolize it

Turns an STL or STEP part into a model of stud-and-tube bricks (the common 8 mm system): it voxelizes the part on the
brick grid, lays bricks so the layers interlock - with **shaped pieces** (slopes, curved slopes, round corners, round
bricks, dome corners, inverted ones) where the surface curves and **special pieces** (custom parts with the part's
exact shape) where nothing else fits - shows the result with the input translucent over the opaque bricks, and exports
it to **FreeCAD** (a coloured document, a solid per part, every stud fit sized from one tolerance and checked), as an
**STL stack for 3D printing** (one slab per layer that plugs into the next, or single parts with a parts list) and as
a **building manual** (LaTeX and PDF, one step per layer). It runs here or **on a server over ssh**.

The layout follows SHaPe ReCo's: an engine package (`lego_engine/`, like `reco_engine/`), a PyQt5 + VTK window
(`lego_gui/`, like `reco_gui/`), `install.sh` with a command and a desktop entry, `tests/`, and the logo pipeline in
`packaging/logo/`.

## Install

```bash
./install.sh            # environment ~/.local/share/legolize-it/venv (with the system's packages), the command
                        # legolize-it, the desktop entry "Legolize it" and its icon
legolize-it check       # packages, FreeCAD, one part into bricks with both exports
./install.sh --uninstall
```

Needs Python >= 3.10 with numpy, scipy, trimesh, libigl, manifold3d (and PyQt5, vtk for the window) -
`requirements*.txt`. FreeCAD's `freecadcmd` is used for STEP input and the FreeCAD export
(`$LEGOLIZE_FREECADCMD`, else `freecadcmd` on the PATH, else `/usr/bin/freecadcmd`).

## Use

**The window:** `legolize-it [part.stl|part.step]` (or `python3 -m lego_gui`). Open a part (or drop it on the window),
press *Legolize it* (F5).

- left: the settings - size (studs along the longest side, or a scale factor), layer height (brick 9.6 / plate 3.2 mm),
  up axis, fill threshold, shell, thin walls (auto / all / off), the brick catalogue, *Pieces* (the shaped families and
  special pieces with their tolerance - on in the window, off on the command line unless asked), the brick system's
  dimensions with the *stud play*, export options and *Run exports on* (this computer or a server); after the first
  run every change redoes the bricks by itself (*Update live*);
- middle: the 3D view - the input translucent, the bricks opaque; colours *auto* (the input's colours when it has any,
  else by size), from the input, by size / layer, mixed, random, rainbow, fade (presets: sunset, ocean, forest, greys,
  candy, earth - snapped to brick colours), one colour; the *Layers* slider builds the stack up layer by layer; bricks
  that are not connected to the bottom are magenta; under the view the *Resolution* slider (coarse - fine: studs along
  the longest side, the model is redone as you move it, with its size in mm and its number of parts);
- right: the numbers (with the *deviation* from the part: the volume where model and part disagree, as a share of
  the part's volume), the parts list and the layers;
- toolbar (and the same buttons at the bottom of the settings' *Export* group): *Export STL stack*, *Export FreeCAD*
  (each with the building manual next to it, `manual/` or `<name>_manual/`, unless *with the building manual* is off),
  *Export manual* (Ctrl+M, the manual on its own), *Open in FreeCAD*, the results folder. An export clicked while the
  model is being redone waits for it and then runs.

**The command line:**

```bash
legolize-it cli part.step --studs 24 --unit plate --shapes all --special --stl layers --fcstd --manual --out out/
#   out/result.json    settings, placement, grid, every part (layer, i, j, w, d; kinds, turns), colours, numbers
#   out/stl/           the STL stack (layers/, bricks/ with piece_*.stl and special_*.stl, assembly.stl, parts.csv,
#                      manifest.json, README.txt)
#   out/part.FCStd     coloured; sheet "Dimensions"; + part_colours.FCMacro (the same colours, set from FreeCAD)
#   out/manual/        manual.tex, manual.pdf, img/
python3 -m lego_engine --help        # the same without the installed command
```

`--shapes` takes `slope, curve, round, dome, inverted`, `all` or `none` (the default); `--special [TOL]` adds special
pieces where the best brick or piece leaves more than TOL of a cell wrong (0.3); `--colours` auto (default), source,
size, layer, mixed, random, rainbow, fade (`--fade ocean`), single (`--single blue`); `--stud-play` the fit.

## Servers (ssh)

```bash
legolize-it server list                      # the hosts of ~/.ssh/config (aliases only) and the servers set up
legolize-it server add HOST ~/legolize_it --python ~/.local/share/legolize-it/venv/bin/python
legolize-it server sync HOST                 # copy this Legolize it (lego_engine, lego_gui) to that path
legolize-it server check HOST                # Python and packages there; FreeCAD, pdflatex
legolize-it cli part.step --server HOST --studs 48 --shapes all --fcstd --manual --out out/
```

In the window: *Export > Run exports on* and *Servers ...* (pick a host from the ssh config, give the path, check,
copy). The part goes to a fresh `mktemp -d` folder on the server, the run's progress comes back as it goes, the results
are fetched into the local folder and the work folder is removed. Only ssh's host aliases are read (ssh resolves host
names, users and keys itself; BatchMode, so a key or an agent is needed); the servers set up are kept in
`~/.config/legolize-it/servers.json` (mode 600), never in the repository or in results; the returned files are
rewritten to name the local input and folder, not the server's.

## How it works

1. **Placement** (`voxelize.place`): the part is turned so its up axis is +z, scaled (studs along the longest
   side, or a factor - the part's units are taken as mm), centred in x/y with its bottom on the grid's floor.
2. **Fill fraction** (`voxelize.fill_fraction`): each cell (pitch x pitch x layer height) is sampled on a regular
   sub-grid (3 x 3 x 3 by default); a point is inside when its generalized winding number is above 0.5 (libigl - robust
   for STLs with small holes). *Thin walls* (`surface_cells`, `thin_cells`): sheet metal, profiles and slim bars fill
   no cell to the threshold and fall between the sample points; *auto* (the default) adds the cells the surface passes
   where no filled cell is near (and the step that attaches them to their body) - a solid part does not change;
   *all* adds every cell the surface passes; *off*.
3. **Shaped and special pieces** (`shapes.py`, optional): the cells near the surface are measured finely (6 x 6 x 6
   points); a piece goes where it fits the part better than the bricks (or gap) it replaces - by at least 0.12 of a
   cell, and no cell of it worse - if nothing sits on a cell without a stud (a curved top) and it holds (on a stud
   below or under something above). Pieces that end up floating are replaced by bricks. Special pieces: cells where
   the best brick or piece leaves more than the tolerance wrong get the input cut to the cell (manifold3d for a closed
   mesh; exact B-rep in FreeCAD for a STEP), joined into rectangles per layer. Measured 2026-11-12, deviation from the
   part: sphere 12 studs 12.9 % (bricks) -> 8.0 % (shaped), 24 studs 6.3 -> 3.9 %; the logo's mushroom (24) 26 -> 17 %.
4. **Layout** (`layout.py`): per layer, the first free cell in scan order anchors the brick with the best score: area,
   + bridging two or three bricks below, + the layer's direction (layers cross), - ending on a joint of the layer below
   (running bond). Repair passes (`layout_connected`): where two groups touch side by side without holding together
   (a fin on a block, a joint of a frame), the layout is redone so a brick reaches across. The cover is exact
   (`check_cover`).
5. **Analysis** (`analysis.py`): parts hold through studs only - a lower part's stud cell under an upper part's seat
   cell; a group that does not reach the bottom is floating (the largest group is the model itself, never floating).
6. **Exports**: printable manifold solids (manifold3d) for the STL stack; a FreeCAD document built by `freecadcmd`,
   its colours written into the file afterwards; the building manual (LaTeX, TikZ plans, VTK pictures).

**The fit of a stud** is one number, the stud play (radial, 0.15 mm): walls (1.35), tubes (6.21), posts (2.90) and
sockets (5.10) are sized from it (`BrickSystem.fit`), and every part over a stud gets a socket. The FreeCAD export
stacks pairs of parts that sit on each other in the model and measures them with OpenCASCADE - common volume 0, the
gap around every stud = the play - and writes it all into the document's spreadsheet *Dimensions*.

**Colours in FreeCAD**: a console FreeCAD has no view objects, so the export writes `GuiDocument.xml` and FreeCAD
1.x's binary `ShapeAppearance` record per part into the saved file itself (byte for byte what FreeCAD writes, checked
against freecadcmd), plus ShapeColor for FreeCAD 0.2x and an isometric camera; the document opens coloured.

Colours from the input: a PLY/OBJ/GLB with vertex or face colours gives every brick the colour of the nearest surface,
snapped to a palette of brick colours (*Colours: from the input*).

## The logo

A Golden Teacher (*Psilocybe cubensis*): caramel umbo, golden cap, pale margin, dark gills, the white stem with its
spore-darkened ring - modelled in Blender, turned into bricks by Legolize it itself and rendered in Blender, not fully
built:

```bash
python3 packaging/logo/make_logo.py       # ~1.5 min: examples/golden_teacher.*, packaging/legolize-it.png, icons/
```

`packaging/logo/golden_teacher.py` (the model, Blender) -> `python3 -m lego_engine ... --colours source` (the bricks)
-> `packaging/logo/render_logo.py` (Cycles; the scene is saved as `packaging/logo/legolize-it_logo.blend`).

## Tests

```bash
python3 -m pytest tests -q          # ~9 s; the FreeCAD tests skip without freecadcmd (servers: a stand-in for ssh)
python3 scripts/check_parts.py <folder of parts> --sample 40 --stl --fcstd   # robustness over many parts
```

Tested on the [ABC dataset](https://deep-geometry.github.io/abc-dataset/) (chunk 0000, 2026-11-11), defaults
(16 studs, bricks, thin walls auto): 20 parts with both exports - among them a 0.1 mm sheet, a 1.2 m bar, a 48-solid
assembly, a 6 mm part, an invalid solid, a file with unbounded surfaces - and 40 random parts with the printable
slabs: all gave bricks, no errors, every slab watertight. 25 of the 40 needed thin walls; 12 of the 40 hold together
as one piece, the others are assemblies, single-layer flat parts or have detached bodies (the window shows which).

## Examples

- `examples/golden_teacher.ply` - the logo's mushroom with its colours (`.stl` without, `.blend` the Blender model);
- `examples/portal_frame.step` - a steel portal frame (6.35 x 4.39 m, IPE beams, thin walls): try `--studs 24`.

## Limits

- One layer height per model (bricks or plates). Shaped pieces are one layer high and parametric (a curve in plate
  layers is a plate high); their names follow the common parts, their exact sizes are this program's.
- Special pieces are printed parts, not off-the-shelf bricks. For a mesh input that is not closed they are built from
  the fine samples (blocky); in FreeCAD they are meshes for mesh inputs, exact solids for STEP inputs.
- The colours inside the FreeCAD document are written in FreeCAD 1.x's own format (checked byte for byte), but
  opening it in the FreeCAD window is the user's check - this program never starts the FreeCAD GUI.
- The layout is greedy: it removes most floating bricks, not all - thin overhangs (a mushroom cap's wavy margin) can
  stay floating; the window marks them.
- A flat part that becomes a single layer does not hold together (bricks side by side are not connected): plates or a
  bigger size give it more layers. Bodies of an assembly that do not touch stay separate.
- A STEP input with unbounded surfaces goes into the FreeCAD document as its tessellated mesh (the export says so).
- Runs on servers need ssh with a key or an agent (BatchMode: no password prompts) and Legolize it on the server
  (`server sync` copies it; the packages it needs must be there).
