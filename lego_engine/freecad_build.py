# Legolize it: builds the FreeCAD document of a brick model. Runs INSIDE FreeCAD (freecadcmd), started by
# lego_engine/export_freecad.py with the job file in $LEGOLIZE_JOB - standard library and FreeCAD only.
#
# The document: a group "Bricks" with a group per layer, every brick a Part::Feature (a solid: body + studs, with the
# hollow underside if the job asks for it) named Brick00000 ... and labelled "2x4 L3 (5,2)"; the input, scaled and
# placed like the bricks, as "Input" (a Part solid for STEP, a mesh for STL). Colours are view properties, which a
# console FreeCAD cannot set: the export writes them into a macro next to the document.
import json
import os
import sys

import FreeCAD as App
import Part

job = json.load(open(os.environ["LEGOLIZE_JOB"]))
S = job["system"]
p, c, h = S["pitch"], S["clearance"], job["layer_height"]
ox, oy, oz = job["origin"]
V = App.Vector
cache = {}


def cyl(r, height, x, y, z):
    return Part.makeCylinder(r, height, V(x, y, z))


def brick_shape(w, d):
    if (w, d) in cache:
        return cache[(w, d)]
    body = Part.makeBox(w * p - 2 * c, d * p - 2 * c, h, V(c, c, 0))
    extra = []
    if job.get("hollow"):
        iw, idp, ih = w * p - 2 * c - 2 * S["wall"], d * p - 2 * c - 2 * S["wall"], h - S["top"]
        if min(iw, idp, ih) > 0.2:
            body = body.cut(Part.makeBox(iw, idp, ih + 1.0, V(c + S["wall"], c + S["wall"], -1.0)))
            if w >= 2 and d >= 2:
                for a in range(1, w):
                    for b in range(1, d):
                        extra.append(cyl(S["tube_outer"] / 2, ih, a * p, b * p, 0)
                                     .cut(cyl(S["tube_inner"] / 2, ih + 2, a * p, b * p, -1)))
            elif max(w, d) >= 2:
                for a in range(1, max(w, d)):
                    x, y = (a * p, p / 2) if w >= d else (p / 2, a * p)
                    extra.append(cyl(S["post_diameter"] / 2, ih, x, y, 0))
    extra += [cyl(S["stud_diameter"] / 2, S["stud_height"], (a + 0.5) * p, (b + 0.5) * p, h)
              for a in range(w) for b in range(d)]
    shape = body.fuse(extra).removeSplitter() if extra else body
    cache[(w, d)] = shape
    return shape


doc = App.newDocument(job["name"])
root = doc.addObject("App::DocumentObjectGroup", "Bricks")
root.Label = "Bricks"
groups = {}
for k in sorted({b[0] for b in job["bricks"]}):
    g = doc.addObject("App::DocumentObjectGroup", "Layer%03d" % k)
    g.Label = "Layer %d (z %.1f mm)" % (k, oz + k * h)
    root.addObject(g)
    groups[k] = g
for n, (k, i, j, w, d) in enumerate(job["bricks"]):
    obj = doc.addObject("Part::Feature", "Brick%05d" % n)
    obj.Shape = brick_shape(w, d)
    obj.Placement = App.Placement(V(ox + i * p, oy + j * p, oz + k * h), App.Rotation())
    obj.Label = "%dx%d L%d (%d,%d)" % (min(w, d), max(w, d), k, i, j)
    groups[k].addObject(obj)

inp = job.get("input")
warnings = []


def add_mesh(path):
    import Mesh
    mesh = Mesh.Mesh(path)
    mesh.transform(m)
    obj = doc.addObject("Mesh::Feature", "Input")
    obj.Mesh = mesh
    return obj


if inp:
    m = App.Matrix(*[float(x) for row in inp["matrix"] for x in row])
    obj = None
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
            obj = doc.addObject("Part::Feature", "Input")
            obj.Shape = moved
        except Exception as exc:
            warnings.append("input as a solid failed (%s)" % str(exc).splitlines()[0][:160])
            if obj is not None:
                doc.removeObject(obj.Name)
                obj = None
            if inp.get("mesh"):
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

doc.recompute()
doc.saveAs(job["out"])
print("LEGOLIZE_DONE " + json.dumps({"objects": len(doc.Objects), "bricks": len(job["bricks"]),
                                     "types": len(cache), "file": job["out"], "warnings": warnings}))
sys.stdout.flush()
