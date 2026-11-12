# Legolize it - STL/STEP parts into stud-and-tube bricks

A workbench on SHaPe ReCo's layout (`../SHaPe_ReCo`): `lego_engine/` (the pipeline, like `reco_engine/`), `lego_gui/`
(PyQt5 + VTK window, like `reco_gui/`), `install.sh` (venv `~/.local/share/legolize-it/venv`, command `legolize-it`,
desktop entry), `tests/`, `packaging/logo/` (the logo, made with Blender and by the framework itself). Started
2026-11-11. The name is a pun on "Legalize it" - the logo is a Golden Teacher mushroom of bricks, not fully built.

Read `README.md` (use, pipeline, limits) and `docs/CODEMAP.md` (modules, data flow) before exploring the code.

## Commands (from the repository root)

- Tests: `python3 -m pytest tests -q` (~10 s, the whole suite; the FreeCAD tests skip without freecadcmd; the server
  tests use a stand-in for ssh - never the user's hosts).
- Window: `python3 -m lego_gui [part] [--studs N] [--unit plate] [--run]`; headless check:
  `QT_QPA_PLATFORM=offscreen python3 -m lego_gui part.stl --screenshot out.png` (Qt's offscreen platform has no VTK
  widget; the view then renders the same scene offscreen - `lego_gui/scene.py`, `render_png`).
  The window needs the user's display: start it in the background and let the user look.
- Command line: `python3 -m lego_engine part.step --studs 24 --unit plate --shapes all --special --stl layers --fcstd
  --manual --out DIR [--png P] [--server HOST]`; servers: `python3 -m lego_engine.remote list|add|check|sync`.
- Logo: `python3 packaging/logo/make_logo.py` (~1.5 min; `--quick` for a 512 px look).
- Installed: `legolize-it`, `legolize-it cli ...`, `legolize-it check`.

## Facts measured here (don't re-derive)

- Fill fraction by winding number (libigl `fast_winding_number`, 200k points in 35 ms), sub-sampled per cell. Bodies
  must be oriented outward (a body with inward normals counts -1 and vanishes); overlapping bodies are fine (union).
- Thin walls (sheet metal, the 6.35 m IPE portal frame in `examples/`) fill no cell to the threshold and fall between
  the sample points (0.3 mm flanges at 24 studs): `thin_walls` / `--thin-walls` takes every cell the surface passes.
  An empty result says so in the log.
- Running bond (2026-11-11): without the seam term in `layout.py` every layer of the frame's beam started with an
  8-long brick, the joints lined up and the beam's middle floated (5 bricks at 24 studs, 12 at 32). With it: one group,
  0 floating; 0.4-3.3 % more bricks over the frame, the sphere and the mushroom at several sizes. Only the end along a brick's length counts (a crossing brick's
  side always lies on joints below; counting it filled crossing layers with 1x1s).
- ABC dataset (the user's local copy of chunk 0000 - 500 STEP parts with a `manifest.csv` - next to the thesis
  material, not in this repository; ask for its folder;
  check: `python3 scripts/check_parts.py <dir> --sample 40 --seed 11 --stl`). 2026-11-11: 20 parts with both exports
  and 40 random with the slabs - all fine. What the first pass found and fixed: sheet metal, profiles and slim bars gave
  no bricks (thin walls now "auto" by default: 25 of 40 parts use it; solid parts are unchanged - tested); the frame
  joints of an assembly and a fin on a block did not hold (repair passes, `layout_connected`); a small stray body in
  the lowest layer made the whole model "floating" (the largest group is never floating); sizing by the longest
  horizontal side made a 25 x 25 x 1219 mm rod 642 layers tall (now the longest side in any direction - the logo is
  pinned to `--scale 2.2568`, its bricks are unchanged); unbounded STEP surfaces broke the FreeCAD input (now an exact
  similarity transform, else the tessellated mesh, else left out with a warning); an empty model crashed the CLI's
  exports (now exit code 2 with a message).
- FreeCAD: in FreeCAD 1.x a group's `Shape` is the compound of its children (don't count solids over groups). The
  colour macro next to the `.FCStd` stays as a fallback (FreeCAD 0.2x, or to set the colours again).
- FreeCAD colours (2026-11-12): `FreeCADGui.setupWithoutGUI()` in freecadcmd still gives no ViewObjects. The export
  writes GuiDocument.xml + one `ShapeAppearance` file per object (PropertyMaterialList version 3: uint32 count; per
  material ambient, diffuse, specular, emissive as packed 0xRRGGBBAA uint32 LE, float32 shininess, float32
  transparency; then 3 empty strings (uint32 0) per material) - byte-identical to freecadcmd's own record (a test).
  Zip order matters: FreeCAD reads files in the order they were announced, so GuiDocument.xml and its records go after
  FreeCAD's entries. That the FreeCAD window shows them is the user's check (the rule below).
- Stud fit (2026-11-12): wall 1.2 / tube 6.51 left 0.3 at the walls and 0 at the tubes; now all from one play
  (`socket_clearance`, 0.15): wall 1.35, tube 6.2137, post 2.9, socket 5.1. A round corner's outer cell is partly
  material without a seat: the stud below ran into it (6.8 mm3) until every cell over a stud got a socket.
  `stacking_check` (manifold3d) and the FreeCAD export's check (OpenCASCADE, 60 pairs): overlap 0, gap 0.15.
- Shaped pieces (2026-11-12): without the per-cell rule (no cell more than 0.05 worse than its brick) inverted slopes
  stuck out of a sphere's equator like blades; pieces holding only each other floated (stacked round corners) - they
  are dropped after the layout. Deviation, bricks -> shaped: sphere 12.9 -> 8.0 % (12 studs), 6.3 -> 3.9 % (24); the
  mushroom 26 -> 17 % (24); specials add little at tolerance 0.3. Thin-wall cells may be special at >= 10 % material
  (a cone's tip), sheet metal (4 %) stays bricks.
- Manual pictures render in the GUI thread (VTK offscreen next to the QVTK widget works on X11, checked on the user's
  display); LaTeX runs in the worker. Exports clicked during a live update used to be dropped silently ("no manual
  gets exported", 2026-11-12); they queue now, and STL/FreeCAD exports write the manual too.
- Servers: only ssh aliases are read (never HostName/User); servers.json lives in ~/.config/legolize-it. A stand-in
  ssh that sets HOME hides ~/.local packages (the test sets only TMPDIR).
- Never launch the FreeCAD GUI or an AppImage yourself (the user's rule, also in SHaPe ReCo); "Open in FreeCAD" in the
  window is the user's own action.
- Debian's Blender 4.3 runs the system Python 3.13 (numpy available), has no OpenImageDenoise and no OptiX: the logo
  renders on the CPU with adaptive sampling (384 samples, ~80 s at 1024 px). trimesh rejects binary PLY with mixed
  quads and triangles: the Blender model exports triangles only. AgX pales the plastic colours: the logo uses
  "Standard" with exposure -0.9.

## Working style

- Results are visual: end by showing them (the window, or `--png` / `render_png`), and look at renders before claiming
  they are right.
- Results and exports go to `~/legolize_results/<part>/` unless asked otherwise.
- Repository: github.com/CalzitPLA/Legolize-it (public; commits as CalzitPLA with the GitHub noreply address, set in
  this repository's config). Commit and push when asked; keep personal paths out of published files (Blender
  files are saved with `bpy.data.libraries.write`, not the main-file save, which stores the file browser's folder).
