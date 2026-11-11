# Code map

```
part.stl / part.step ──mesh_io.load_part──▶ trimesh.Trimesh (mm)            STEP: freecadcmd tessellates (cached)
        │
        ▼ voxelize.place(vertices, cell, size_mode, size, up)  ─▶ Placement (x' = s·R·x + t), Grid (origin, cell, shape)
        ▼ voxelize.fill_fraction(...)                          ─▶ fraction[nx, ny, nz]  (winding number, s³ samples)
        ▼ occupancy(fraction, threshold) | surface_cells (thin walls) ; hollow(shell)   ─▶ occ[nx, ny, nz] bool
        ▼ layout.layout_bricks(occ, sizes)                     ─▶ BrickModel(bricks[layer, i, j, w, d], ids[nx, ny, nz])
        ▼ analysis.stats / connectivity                        ─▶ counts, layers, groups, floating
        ▼ palette.brick_colours (+ surface colours of a coloured input)
LegoResult ──▶ lego_gui (scene.Scene: input translucent, bricks opaque per layer)
           ──▶ export_stl.write_stl_stack   (geometry.layer_slab / brick_solid: manifold3d solids)
           ──▶ export_freecad.write_fcstd   (freecadcmd runs freecad_build.py; + the colour macro)
           ──▶ result.json (LegoResult.to_dict) ──▶ packaging/logo/render_logo.py (Blender)
```

Arrays are indexed `[i, j, k]` = `[x, y, layer]`; cell `(i, j, k)` spans `origin + (i, j, k) * cell` to
`origin + (i + 1, j + 1, k + 1) * cell`. A brick `(layer, i, j, w, d)` covers cells `i .. i+w-1` along x and
`j .. j+d-1` along y; its body is inset by `clearance`, its studs sit on the cell centres.

| module | what it does |
|---|---|
| `lego_engine/system.py` | `BrickSystem` (every dimension, mm), the size catalogue, `parse_sizes`, `normalise_sizes` |
| `lego_engine/mesh_io.py` | loading; `find_freecadcmd`, `tessellate_step` (cache `~/.cache/legolize/step/`); `up_matrix` |
| `lego_engine/voxelize.py` | `Grid`, `Placement`, `place`, `fill_fraction`, `surface_cells`, `occupancy`, `hollow` |
| `lego_engine/layout.py` | `Brick`, `BrickModel`, `layout_layer` (the score: area, bridging, direction, running bond), `check_cover` |
| `lego_engine/analysis.py` | `connectivity` (union-find over stud contacts), `layer_islands`, `stats` |
| `lego_engine/geometry.py` | display meshes (`bricks_display`, `voxel_mesh`: numpy, no booleans) and solids (`brick_solid`, `layer_slab`) |
| `lego_engine/palette.py` | brick colours, colour modes, `surface_colours` (nearest face of a coloured input), `snap` |
| `lego_engine/pipeline.py` | `LegoSettings`, `LegoResult`, `Legolizer` (cached load / voxelization, cheap re-layout) |
| `lego_engine/export_stl.py` | the STL stack: layer slabs, single bricks, assembly, parts.csv, manifest, README |
| `lego_engine/export_freecad.py`, `freecad_build.py` | the `.FCStd` (built inside FreeCAD) and the colour macro; `open_in_freecad` |
| `lego_engine/cli.py` | `python3 -m lego_engine` |
| `lego_gui/scene.py` | the VTK scene (Qt-free), `render_png` |
| `lego_gui/viewer.py` | the 3D view widget with its bar (layers, colours, layer slider, camera, PNG) |
| `lego_gui/settings_panel.py`, `parts_panel.py`, `worker.py`, `app.py`, `__main__.py` | the window |
| `packaging/logo/` | `golden_teacher.py` (Blender model), `render_logo.py` (Blender render), `make_logo.py` (the chain) |
