# Code map

```
part.stl / part.step ──mesh_io.load_part──▶ trimesh.Trimesh (mm)            STEP: freecadcmd tessellates (cached)
        │
        ▼ voxelize.place(vertices, cell, size_mode, size, up)  ─▶ Placement (x' = s·R·x + t), Grid (origin, cell, shape)
        ▼ voxelize.fill_fraction(...)                          ─▶ fraction[nx, ny, nz]  (winding number, s³ samples)
        ▼ occupancy(fraction, threshold) | thin_cells(occ, surface_cells) (thin walls) ; hollow(shell) ─▶ occ bool
        ▼ shapes.fine_samples (6³ per surface cell) ─▶ fit_pieces (shaped) ─▶ special_cells / special_pieces
                                                               ─▶ pieces [(Brick kind/rot, studs, seats)], occ + their cells
        ▼ layout.layout_connected(occ, sizes, pieces=...)      ─▶ BrickModel(bricks[layer, i, j, w, d, kind, rot],
                                                                  ids[nx, ny, nz], stud_cells, seat_cells, special_meshes)
          (pieces that float are dropped and the bricks laid again; specials cut: geometry.special_solid)
        ▼ analysis.stats / connectivity (stud -> seat contacts); shapes.deviation ─▶ counts, groups, floating, deviation
        ▼ palette.brick_colours (auto/source/size/layer/mixed/random/rainbow/fade/single)
LegoResult ──▶ lego_gui (scene.Scene: input translucent, bricks opaque per layer; render_steps for the manual)
           ──▶ export_stl.write_stl_stack   (geometry.layer_slab / model_slab / brick_solid / piece_print_solid /
                                             special_print_solid: manifold3d solids, sockets from the stud play)
           ──▶ export_freecad.write_fcstd   (freecadcmd runs freecad_build.py: parts, sheet "Dimensions", stacking
                                             check; then write_view_colours puts the colours into the .FCStd)
           ──▶ export_manual.write_manual   (manual.tex: TikZ plans, VTK pictures; pdflatex)
           ──▶ result.json (LegoResult.to_dict) ──▶ packaging/logo/render_logo.py (Blender)
(LegoSettings.details: details.bar_pieces join the pieces before the layout, details.apply turns plates into tiles and
 1x1 columns into rods after it)
LegoResult ──▶ shop.needs ──▶ shop.price (Catalogue) ──▶ export_order.write_order (the BlueBrixx order list)
           (cli --cheapest: shop.cheapest_sizes lays the bricks again with other sizes per colour -
            LegoSettings.colour_sizes - the cheapest wins)
cli --server HOST ──▶ remote.run_cli (ssh + tar: the part there, `python -m lego_engine` there, the results back)
```

Arrays are indexed `[i, j, k]` = `[x, y, layer]`; cell `(i, j, k)` spans `origin + (i, j, k) * cell` to
`origin + (i + 1, j + 1, k + 1) * cell`. A brick `(layer, i, j, w, d)` covers cells `i .. i+w-1` along x and
`j .. j+d-1` along y; its body is inset by `clearance`, its studs sit on the cell centres.

| module | what it does |
|---|---|
| `lego_engine/system.py` | `BrickSystem` (every dimension, mm; `fit()`: walls, tubes, posts, sockets from the stud play), the size catalogue, `parse_sizes`, `normalise_sizes`, `name_area` |
| `lego_engine/shapes.py` | shaped pieces (`CATALOGUE`: height fields, `mask`, `cell_props` studs/seats, `outline` for the solids - also the details' tiles and bars), `fine_samples`, `fit_pieces`, `special_cells`, `special_pieces`, `deviation` |
| `lego_gui/settings_panel.py` `MODES` | the window's *Mode* presets (bricky / detailed / artsy): style, catalogue, longest brick, shaped, special and detail pieces at once; `set_mode`, `mode_of` (else "custom") |
| `lego_engine/technic.py` | Technic parts for artsy mode: axles, parts with a cross hole, support bricks (`PARTS`, Pick a Brick design numbers, radius and length along the axle), `axles_for`, `standard_colour`, `part_mesh` |
| `lego_engine/assembly.py` | `Assembly`: an axle with threaded parts along x, y or z, its support bricks and cells; `place`, `meshes`, `frame`, `cells_mask`; one rigid node in `analysis.connectivity` |
| `lego_engine/artsy.py` | artsy mode: `prepare` (roundness per colour / component / whole, axle stacks from profiles, visibility, symmetry, corner cells, base, posts, printed details), `block_colours`, `clean`, `craft` (the report) |
| `lego_engine/details.py` | details laid by rule: `bar_pieces` (bars on two clips, pieces before the layout), `apply` (tiles on uncovered plates, round 1x1 rods - after the layout), `parse_details` |
| `lego_engine/mesh_io.py` | loading; `find_freecadcmd`, `tessellate_step` (cache `~/.cache/legolize/step/`); `up_matrix` |
| `lego_engine/voxelize.py` | `Grid`, `Placement`, `place`, `fill_fraction`, `surface_cells`, `thin_cells`, `occupancy`, `hollow` |
| `lego_engine/layout.py` | `Brick`, `BrickModel`, `layout_layer` (the score: area, bridging, direction, running bond, junctions), `layout_connected` (repair passes), `check_cover`; `colour_sizes`: per colour the sizes a brick may have |
| `lego_engine/analysis.py` | `connectivity` (union-find over stud contacts), `layer_islands`, `stats` |
| `lego_engine/geometry.py` | display meshes (`bricks_display`, `piece_template`, `voxel_mesh`) and solids (`brick_solid`, `layer_slab`, `model_slab`, `piece_solid`, `piece_print_solid`, `input_solid`, `special_solid`, `special_print_solid`), `stacking_check` |
| `lego_engine/palette.py` | brick colours, colour modes, `surface_colours` (nearest face of a coloured input), `snap` |
| `lego_engine/pipeline.py` | `LegoSettings` (`colour_sizes`, `recolour`: palette colour -> the one to build in), `LegoResult`, `Legolizer` (cached load / voxelization, cheap re-layout) |
| `lego_engine/export_stl.py` | the STL stack: layer slabs, single bricks, assembly, parts.csv, manifest, README |
| `lego_engine/export_freecad.py`, `freecad_build.py` | the `.FCStd` (built inside FreeCAD: parts, sheet, stacking check), `write_view_colours` (GuiDocument.xml + ShapeAppearance records), the colour macro; `open_in_freecad` |
| `lego_engine/export_manual.py` | the building manual (LaTeX + PDF) |
| `lego_engine/shop.py` | buying the parts at BlueBrixx: `crawl` (the part-pack catalogue, cached in ~/.cache/legolize/bluebrixx/; a dated snapshot comes with it: `lego_engine/data/bluebrixx_catalogue.json`, `default_catalogue`), `Catalogue`/`Pack`, `needs` (from a result, result.json or manual.tex), `cheapest` (pack mix), `price` -> `Order`, `cheapest_sizes` (per colour the brick sizes with the cheapest order: greedy, cached voxelization), `cheapest_together` (several builds, one order, shared sizes per unit and colour), `Order.usage`; `legolize-it shop crawl|order|plan` (plan: colour pairs x builds recoloured by roles) |
| `lego_engine/export_order.py` | the order list: LaTeX + PDF, Markdown, CSV; English or German |
| `lego_engine/remote.py` | servers over ssh: `ssh_config_hosts`, the servers file (~/.config/legolize-it), `check`, `sync_code`, `run_cli`; `legolize-it server ...` |
| `lego_engine/cli.py` | `python3 -m lego_engine` |
| `lego_gui/scene.py` | the VTK scene (Qt-free), `render_png` |
| `lego_gui/viewer.py` | the 3D view widget with its bar (layers, colours, layer slider, camera, PNG) |
| `lego_gui/settings_panel.py`, `parts_panel.py`, `servers.py`, `worker.py`, `app.py`, `__main__.py` | the window |
| `packaging/logo/` | `golden_teacher.py` (Blender model), `render_logo.py` (Blender render), `make_logo.py` (the chain) |
| `scripts/check_parts.py` | robustness over many parts (the ABC check) |
