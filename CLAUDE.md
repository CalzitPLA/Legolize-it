# Legolize it - STL/STEP parts into stud-and-tube bricks

A workbench on SHaPe ReCo's layout (`../SHaPe_ReCo`): `lego_engine/` (the pipeline, like `reco_engine/`), `lego_gui/`
(PyQt5 + VTK window, like `reco_gui/`), `install.sh` (venv `~/.local/share/legolize-it/venv`, command `legolize-it`,
desktop entry), `tests/`, `packaging/logo/` (the logo, made with Blender and by the framework itself). Started
2026-11-11. The name is a pun on "Legalize it" - the logo is a Golden Teacher mushroom of bricks, not fully built.

Read `README.md` (use, pipeline, limits) and `docs/CODEMAP.md` (modules, data flow) before exploring the code.

## Commands (from the repository root)

- Tests: `python3 -m pytest tests -q` (~1 s, the whole suite; the FreeCAD test skips without freecadcmd).
- Window: `python3 -m lego_gui [part] [--studs N] [--unit plate] [--run]`; headless check:
  `QT_QPA_PLATFORM=offscreen python3 -m lego_gui part.stl --screenshot out.png` (Qt's offscreen platform has no VTK
  widget; the view then renders the same scene offscreen - `lego_gui/scene.py`, `render_png`).
  The window needs the user's display: start it in the background and let the user look.
- Command line: `python3 -m lego_engine part.step --studs 24 --unit plate --stl layers --fcstd --out DIR [--png P]`.
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
- FreeCAD: a console FreeCAD has no view objects and FreeCAD 1.x keeps colours in a binary ShapeAppearance record, so
  the colours and the input's transparency go into `<name>_colours.FCMacro` next to the `.FCStd`. In FreeCAD 1.x a
  group's `Shape` is the compound of its children (don't count solids over groups).
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
