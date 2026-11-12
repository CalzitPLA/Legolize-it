# Legolize it: builds the FreeCAD document of a brick model. Runs INSIDE FreeCAD (freecadcmd), started by
# lego_engine/export_freecad.py with the job file in $LEGOLIZE_JOB - standard library and FreeCAD only.
#
# The document:
#   Bricks      a group per layer; every brick or piece a Part::Feature named Brick00000 ... labelled "2x4 L3 (5,2)":
#               bricks with their hollow underside (walls, tubes, posts) and studs, shaped pieces (slopes, curves, round
#               corners, domes - curved faces are B-splines) with studs and a socket under every cell, special pieces
#               (the input inside their cells: exact B-rep for a STEP input, else a mesh) with studs and sockets;
#   Dimensions  a spreadsheet: the brick system, the stud play and every size derived from it, the gaps around a stud,
#               and the stacking check below;
#   Input       the part, scaled and placed like the bricks (a Part solid for STEP, a mesh otherwise).
# Every part that receives a stud is sized from one stud play (lego_engine.system.BrickSystem.fit). The stacking check
# takes pairs of parts that sit on each other in this model and measures, with OpenCASCADE, their common volume
# (0 = they stack) and the gap between the lower part's studs and the upper part (= the stud play).
# Colours: export_freecad.py writes them into the saved file afterwards (a console FreeCAD has no view objects).
import json
import os
import sys

import FreeCAD as App
import Part

job = json.load(open(os.environ["LEGOLIZE_JOB"]))
S = job["system"]
F = job["fit"]
p, c, h = S["pitch"], S["clearance"], job["layer_height"]
ox, oy, oz = job["origin"]
V = App.Vector
bricks = job["bricks"]
kinds = job.get("kinds") or [""] * len(bricks)
turns = job.get("turns") or [0] * len(bricks)
warnings = []
cache, stud_cache = {}, {}


def cyl(r, height, x, y, z):
    return Part.makeCylinder(r, height, V(x, y, z))


def studs_of(cells):
    return [cyl(S["stud_diameter"] / 2, S["stud_height"], (a + 0.5) * p, (b + 0.5) * p, h) for a, b in cells]


def sockets_of(cells):
    return [cyl(F["socket_diameter"] / 2, F["socket_depth"] + 1.0, (a + 0.5) * p, (b + 0.5) * p, -1.0)
            for a, b in cells]


def fuse_cut(body, add=(), cut=()):
    if add:
        body = body.fuse(list(add))
    if cut:
        body = body.cut(list(cut))
    return body.removeSplitter()


def brick_shape(w, d):
    key = ("brick", w, d)
    if key in cache:
        return cache[key]
    body = Part.makeBox(w * p - 2 * c, d * p - 2 * c, h, V(c, c, 0))
    extra = []
    wall = F["wall"]
    if job.get("hollow", True):
        iw, idp, ih = w * p - 2 * c - 2 * wall, d * p - 2 * c - 2 * wall, h - S["top"]
        if min(iw, idp, ih) > 0.2:
            body = body.cut(Part.makeBox(iw, idp, ih + 1.0, V(c + wall, c + wall, -1.0)))
            if w >= 2 and d >= 2:
                for a in range(1, w):
                    for b in range(1, d):
                        extra.append(cyl(F["tube_outer"] / 2, ih, a * p, b * p, 0)
                                     .cut(cyl(S["tube_inner"] / 2, ih + 2, a * p, b * p, -1)))
            elif max(w, d) >= 2:
                for a in range(1, max(w, d)):
                    x, y = (a * p, p / 2) if w >= d else (p / 2, a * p)
                    extra.append(cyl(F["post_diameter"] / 2, ih, x, y, 0))
        else:                                       # too low for a cavity (plates in a small system): sockets
            body = body.cut(sockets_of([(a, b) for a in range(w) for b in range(d)]))
    cells = [(a, b) for a in range(w) for b in range(d)]
    shape = fuse_cut(body, extra + studs_of(cells))
    cache[key] = shape
    stud_cache[key] = cells
    return shape


def wire_of(points, curve):
    """A closed wire through `points` (FreeCAD vectors): straight edges, and a B-spline through the curved run."""
    n = len(points)
    edges, skip = [], set()
    if curve:
        c0, c1 = curve
        bs = Part.BSplineCurve()
        bs.interpolate(points[c0:c1 + 1])
        edges.append(bs.toShape())
        skip = set(range(c0, c1))
    for q in range(n):
        if q in skip:
            continue
        a, b = points[q], points[(q + 1) % n]
        if (a - b).Length > 1e-9:
            edges.append(Part.LineSegment(a, b).toShape())
    return Part.Wire(Part.__sortEdges__(edges))


def outline_shape(o):
    if o["op"] == "cylinder":
        x, y = o["centre"]
        return Part.makeCylinder(o["radius"], o["height"], V(x, y, 0))
    if o["op"] == "extrude_y":
        pts = [V(x, o["y0"], z) for x, z in o["poly"]]
        return Part.Face(wire_of(pts, o["curve"])).extrude(V(0, o["length"], 0))
    if o["op"] == "extrude_z":
        pts = [V(x, y, 0) for x, y in o["poly"]]
        return Part.Face(wire_of(pts, o["curve"])).extrude(V(0, 0, o["height"]))
    if o["op"] == "revolve":
        cx, cy = o["centre"]
        pts = [V(cx + r, cy, z) for r, z in o["poly"]]
        return Part.Face(wire_of(pts, o["curve"])).revolve(V(cx, cy, 0), V(0, 0, 1), 90)
    raise ValueError(o["op"])


def piece_shape(kind):
    key = ("piece", kind)
    if key in cache:
        return cache[key]
    t = job["types"][kind]
    body = outline_shape(t["outline"])
    shape = fuse_cut(body, studs_of(t["studs"]), sockets_of(t["cells"]))
    cache[key] = shape
    stud_cache[key] = [tuple(x) for x in t["studs"]]
    return shape


def turned(kind, rot, w, d):
    """The rotation and the shift of a turned piece inside its footprint (lego_engine.shapes.placement_offset)."""
    if not kind or kind == "special":
        return App.Rotation(), V(0, 0, 0)
    t = job["types"][kind]
    Lp, Wp = t["L"] * p, t["W"] * p
    off = {0: (0, 0), 1: (Wp, 0), 2: (Lp, Wp), 3: (0, Lp)}[rot % 4]
    return App.Rotation(V(0, 0, 1), 90 * (rot % 4)), V(off[0], off[1], 0)


# ---------------------------------------------------------------------------------------------- the input

doc = App.newDocument(job["name"])
inp = job.get("input")
moved = None
m = None
if inp:
    m = App.Matrix(*[float(x) for row in inp["matrix"] for x in row])
    if inp["kind"] == "step":
        # the exact shape, moved by the similarity (uniform scale + rotation + shift) without converting its surfaces;
        # transformGeometry only if that fails (it turns surfaces into B-splines, impossible for infinite ones); the
        # tessellated mesh if the geometry is unbounded or nothing else works - the input never sinks the document
        try:
            shape = Part.read(inp["path"])
            try:
                moved = shape.copy()
                moved.transformShape(m)
            except Exception:
                moved = shape.transformGeometry(m)
            if moved.BoundBox.DiagonalLength > 1000 * job["model_diagonal"]:
                raise ValueError("the STEP's geometry is unbounded (infinite surfaces)")
        except Exception as exc:
            warnings.append("input as a solid failed (%s)" % str(exc).splitlines()[0][:160])
            moved = None


def special_shape(n, spec):
    """A special piece in model coordinates: the exact input inside its box (STEP), with studs and sockets."""
    if moved is None or "box" not in spec:
        return None
    lo, size = spec["box"]
    try:
        body = moved.common(Part.makeBox(size[0], size[1], size[2], V(*lo)))
        if body.Volume < 1e-6:
            return None
        add = [cyl(S["stud_diameter"] / 2, S["stud_height"], x, y, z) for x, y, z in spec["studs"]]
        cut = [cyl(F["socket_diameter"] / 2, F["socket_depth"] + 1.0, x, y, z - 1.0) for x, y, z in spec["sockets"]]
        return fuse_cut(body, add, cut)
    except Exception as exc:
        warnings.append("special piece %d: %s" % (n, str(exc).splitlines()[0][:120]))
        return None


# ---------------------------------------------------------------------------------------------- the bricks

root = doc.addObject("App::DocumentObjectGroup", "Bricks")
root.Label = "Bricks"
groups = {}
for k in sorted({b[0] for b in bricks}):
    g = doc.addObject("App::DocumentObjectGroup", "Layer%03d" % k)
    g.Label = "Layer %d (z %.1f mm)" % (k, oz + k * h)
    root.addObject(g)
    groups[k] = g
placed = {}                              # n -> (shape in model coordinates, stud cells in model cells)
specials = job.get("specials", {})
exact = 0
for n, (k, i, j, w, d) in enumerate(bricks):
    kind, rot = kinds[n], turns[n]
    name = "Brick%05d" % n
    corner = V(ox + i * p, oy + j * p, oz + k * h)
    if kind == "special":
        spec = specials.get(str(n), {})
        shape = special_shape(n, spec)
        if shape is not None:
            exact += 1
            obj = doc.addObject("Part::Feature", name)
            obj.Shape = shape
        elif spec.get("mesh"):
            import Mesh
            obj = doc.addObject("Mesh::Feature", name)
            obj.Mesh = Mesh.Mesh(spec["mesh"])
            shape = None
        else:
            warnings.append("special piece %d has no geometry" % n)
            continue
        obj.Label = "special %dx%d L%d (%d,%d)" % (min(w, d), max(w, d), k, i, j)
        groups[k].addObject(obj)
        if shape is not None:
            placed[n] = (shape, [(x, y) for x, y in spec.get("stud_cells", [])])
        continue
    obj = doc.addObject("Part::Feature", name)
    if kind:
        obj.Shape = piece_shape(kind)
        rotation, shift = turned(kind, rot, w, d)
        obj.Placement = App.Placement(corner + shift, rotation)
        obj.Label = "%s L%d (%d,%d) turn %d" % (job["types"][kind]["label"], k, i, j, rot)
        cells = job["stud_cells"].get(str(n), [])
    else:
        obj.Shape = brick_shape(w, d)
        obj.Placement = App.Placement(corner, App.Rotation())
        obj.Label = "%dx%d L%d (%d,%d)" % (min(w, d), max(w, d), k, i, j)
        cells = [(i + a, j + b) for a in range(w) for b in range(d)]
    groups[k].addObject(obj)
    shp = obj.Shape.copy()
    shp.Placement = obj.Placement
    placed[n] = (shp, cells)

# ---------------------------------------------------------------------------------------------- the input object


def add_mesh(path):
    import Mesh
    mesh = Mesh.Mesh(path)
    mesh.transform(m)
    obj = doc.addObject("Mesh::Feature", "Input")
    obj.Mesh = mesh
    return obj


if inp and not inp.get("hidden"):                  # hidden: loaded only to cut the special pieces
    obj = None
    if inp["kind"] == "step":
        if moved is not None:
            obj = doc.addObject("Part::Feature", "Input")
            obj.Shape = moved
        elif inp.get("mesh"):
            try:
                obj = add_mesh(inp["mesh"])
                warnings.append("the input is the tessellated mesh instead")
            except Exception as exc2:
                warnings.append("input as a mesh failed (%s)" % str(exc2).splitlines()[0][:160])
    else:
        try:
            obj = add_mesh(inp["path"])
        except Exception as exc:
            warnings.append("input mesh failed (%s)" % str(exc).splitlines()[0][:160])
    if obj is not None:
        obj.Label = "Input (scaled to the bricks)"
    else:
        warnings.append("the input is left out of the document")

# ---------------------------------------------------------------------------------------------- stacking check

checks = []
contacts = job.get("contacts", [])            # [lower n, upper n, [[x, y] model cells of the lower's studs under it]]
seen = set()
for lo_n, up_n, cells in contacts:
    if lo_n not in placed or up_n not in placed:
        continue
    lo_b, up_b = bricks[lo_n], bricks[up_n]
    sig = (kinds[lo_n], lo_b[3], lo_b[4], turns[lo_n], kinds[up_n], up_b[3], up_b[4], turns[up_n],
           up_b[1] - lo_b[1], up_b[2] - lo_b[2])
    if sig in seen and kinds[up_n] != "special" and kinds[lo_n] != "special":
        continue
    seen.add(sig)
    if len(checks) >= job.get("max_checks", 60):
        break
    lower, upper = placed[lo_n][0], placed[up_n][0]
    try:
        overlap = lower.common(upper).Volume
        zs = oz + (lo_b[0] + 1) * h
        studs = Part.makeCompound([cyl(S["stud_diameter"] / 2, S["stud_height"], ox + (x + 0.5) * p,
                                       oy + (y + 0.5) * p, zs) for x, y in cells])
        gap = studs.distToShape(upper)[0] if cells else None
        checks.append({"lower": lo_n, "upper": up_n, "overlap_mm3": round(overlap, 4),
                       "stud_gap_mm": None if gap is None else round(gap, 4)})
    except Exception as exc:
        warnings.append("stacking check %d/%d: %s" % (lo_n, up_n, str(exc).splitlines()[0][:100]))

# ---------------------------------------------------------------------------------------------- dimensions

sheet = doc.addObject("Spreadsheet::Sheet", "Dimensions")
sheet.Label = "Dimensions and tolerances"
rows = [("Brick system (mm)", None, None, None),
        ("pitch", S["pitch"], "pitch", "stud spacing = cell size in x and y"),
        ("layer", h, "layer", "layer height (brick 9.6 / plate 3.2)"),
        ("stud_diameter", S["stud_diameter"], "studD", ""),
        ("stud_height", S["stud_height"], "studH", ""),
        ("side_clearance", S["clearance"], "sideClearance", "per side between neighbouring parts"),
        ("Fit of a stud (mm)", None, None, None),
        ("stud_play", F["stud_play"], "studPlay", "radial play of a stud in what receives it - the one tolerance"),
        ("wall", F["wall"], "wall", "pitch/2 - studD/2 - sideClearance - studPlay"),
        ("top", S["top"], "top", "thickness of a hollow brick's top"),
        ("tube_outer", F["tube_outer"], "tubeD", "2 (pitch/sqrt 2 - studD/2 - studPlay), bricks >= 2 wide"),
        ("tube_inner", S["tube_inner"], "tubeInnerD", ""),
        ("post_diameter", F["post_diameter"], "postD", "2 (pitch/2 - studD/2 - studPlay), one-wide bricks"),
        ("socket_diameter", F["socket_diameter"], "socketD", "studD + 2 studPlay: shaped and special pieces"),
        ("socket_depth", F["socket_depth"], "socketDepth", "studH + 0.2"),
        ("Gaps around a stud (mm)", None, None, None),
        ("gap_wall", F["gap_wall"], "gapWall", "stud to the inside of a wall"),
        ("gap_tube", F["gap_tube"], "gapTube", "stud to a tube"),
        ("gap_post", F["gap_post"], "gapPost", "stud to a post"),
        ("gap_socket", F["gap_socket"], "gapSocket", "stud to a socket")]
if checks:
    gaps = [x["stud_gap_mm"] for x in checks if x["stud_gap_mm"] is not None]
    rows += [("Stacking check (OpenCASCADE)", None, None, None),
             ("pairs_checked", len(checks), "pairsChecked", "parts sitting on each other in this model"),
             ("max_overlap_mm3", max(x["overlap_mm3"] for x in checks), "maxOverlap", "0 = everything stacks"),
             ("min_stud_gap", min(gaps) if gaps else 0, "minStudGap", "narrowest gap around a stud"),
             ("max_stud_gap", max(gaps) if gaps else 0, "maxStudGap", "")]
for r, (label, value, alias, note) in enumerate(rows, start=1):
    sheet.set("A%d" % r, label)
    if value is not None:
        sheet.set("B%d" % r, "%g" % value)
        if alias:
            try:
                sheet.setAlias("B%d" % r, alias)
            except Exception:
                pass
        if note:
            sheet.set("C%d" % r, note)

doc.recompute()
doc.saveAs(job["out"])
print("LEGOLIZE_DONE " + json.dumps({"objects": len(doc.Objects), "bricks": len(bricks), "types": len(cache),
                                     "file": job["out"], "warnings": warnings, "checks": checks,
                                     "special_exact": exact,
                                     "names": [o.Name for o in doc.Objects], "kinds": [o.TypeId for o in doc.Objects]}))
sys.stdout.flush()
