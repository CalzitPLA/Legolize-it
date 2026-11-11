"""A Golden Teacher (Psilocybe cubensis "Golden Teacher") for the Legolize it logo and examples, modelled in Blender.

    blender -b --factory-startup --python packaging/logo/golden_teacher.py -- --out examples/golden_teacher

writes <out>.ply (vertex colours, sRGB), <out>.stl (shape only) and <out>.blend. Lengths in mm, z up, the stem's foot
at z = 0. Every part is a closed surface of revolution (bodies may overlap - the voxelizer's winding number takes their
union):

- stipe (stem): long, slightly leaning, a little thicker towards the bulbous foot; white to cream, a faint blue bruise
  at the foot;
- annulus (ring): a membranous skirt high on the stem, dark purple-black from the spores that fall on it;
- pileus (cap): broadly convex with a low umbo, the margin slightly wavy; caramel at the umbo, golden over most of the
  cap, pale cream at the margin; the underside (the gills) dark purple-grey, lighter at the edge.
"""
import argparse
import math
import sys
from pathlib import Path

import bpy
import numpy as np

STEM_HEIGHT = 86.0
CAP_RADIUS = 31.0
CAP_HEIGHT = 19.0
SEGMENTS = 128

CAP_STOPS = [   # (r / R on the cap's top, sRGB)
    (0.00, (0.50, 0.26, 0.10)),     # the umbo: caramel brown
    (0.16, (0.70, 0.42, 0.14)),
    (0.42, (0.86, 0.60, 0.20)),     # golden
    (0.72, (0.93, 0.77, 0.40)),
    (1.00, (0.97, 0.91, 0.70)),     # pale cream margin
]
GILLS = (0.30, 0.24, 0.30)
GILL_EDGE = (0.62, 0.56, 0.55)
STEM = (0.95, 0.94, 0.90)
BRUISE = (0.62, 0.74, 0.86)
RING = (0.17, 0.11, 0.19)


def ramp(stops, x):
    x = min(1.0, max(0.0, x))
    for (a, ca), (b, cb) in zip(stops, stops[1:]):
        if x <= b:
            t = (x - a) / (b - a) if b > a else 0.0
            return tuple(ca[i] + t * (cb[i] - ca[i]) for i in range(3))
    return stops[-1][1]


def mix(c1, c2, t):
    return tuple(c1[i] + t * (c2[i] - c1[i]) for i in range(3))


def revolve(profile, colour, wobble=None, segments=SEGMENTS):
    """Closed surface of revolution about z. `profile`: [(r, z, s)] from pole to pole (r = 0 at both ends, s a
    parameter handed to `colour(s, phi)` and `wobble(s, phi)` -> radius factor). Returns vertices, faces, colours."""
    verts, cols, faces = [], [], []
    rings = []
    for n, (r, z, s) in enumerate(profile):
        if r == 0.0:
            rings.append([len(verts)])
            verts.append((0.0, 0.0, z))
            cols.append(colour(s, 0.0))
            continue
        ring = []
        for q in range(segments):
            phi = 2 * math.pi * q / segments
            f = wobble(s, phi) if wobble else 1.0
            ring.append(len(verts))
            verts.append((r * f * math.cos(phi), r * f * math.sin(phi), z))
            cols.append(colour(s, phi))
        rings.append(ring)
    for a, b in zip(rings, rings[1:]):
        if len(a) == 1:
            faces += [(a[0], b[q], b[(q + 1) % segments]) for q in range(segments)]
        elif len(b) == 1:
            faces += [(a[q], b[0], a[(q + 1) % segments]) for q in range(segments)]
        else:                            # triangles only: every reader takes them (trimesh rejects mixed binary PLY)
            for q in range(segments):
                r = (q + 1) % segments
                faces += [(a[q], b[q], b[r]), (a[q], b[r], a[r])]
    v = np.array(verts)
    volume = sum(np.dot(v[f[0]], np.cross(v[f[k]], v[f[k + 1]])) for f in faces for k in range(1, len(f) - 1))
    if volume < 0:                       # the profile ran the other way round: normals outward
        faces = [tuple(reversed(f)) for f in faces]
    return v, faces, cols


def stem_radius(t):
    """Radius of the stem at height fraction t (0 = foot)."""
    return 7.0 - 1.4 * t + 2.4 * math.exp(-(t / 0.07) ** 2) + 0.5 * math.exp(-((t - 0.97) / 0.05) ** 2)


def lean(t):
    """Sideways offset (x) of the stem's axis at height fraction t - a gentle lean."""
    return 5.5 * t ** 1.8


def stem():
    prof = [(0.0, 0.0, 0.0)]
    n = 60
    for m in range(n + 1):
        t = m / n
        prof.append((stem_radius(t), t * STEM_HEIGHT, t))
    prof.append((0.0, STEM_HEIGHT, 1.0))

    def colour(s, phi):
        streak = 0.03 * math.sin(7 * phi + 9 * s)                           # fibrous stripes
        base = mix(STEM, (0.90, 0.86, 0.78), 0.35 * s + streak)
        return mix(base, BRUISE, max(0.0, 1.0 - s / 0.09) * (0.55 + 0.45 * math.cos(phi - 0.8)) * 0.8)
    v, f, c = revolve(prof, colour)
    t = v[:, 2] / STEM_HEIGHT
    v[:, 0] += np.array([lean(x) for x in t])
    return v, f, c


def ring():
    t0 = 0.80
    z0, r0 = t0 * STEM_HEIGHT, stem_radius(t0)
    prof = [(0.0, z0 + 2.2, 0.0)]
    n = 24
    for m in range(n + 1):                                    # a drooping skirt: an inclined flat ellipse
        a = math.pi * m / n                                   # 0 .. pi, top to bottom over the outside
        prof.append((r0 - 0.6 + 5.6 * math.sin(a), z0 + 2.2 * math.cos(a) - 2.8 * math.sin(a) ** 2, m / n))
    prof.append((0.0, z0 - 2.2, 1.0))
    v, f, c = revolve(prof, lambda s, phi: mix(RING, (0.40, 0.33, 0.38), 0.4 * (1 - s)),
                      wobble=lambda s, phi: 1.0 + 0.05 * math.sin(6 * phi) * math.sin(math.pi * s))
    v[:, 0] += lean(t0)
    return v, f, c


def cap():
    R, H = CAP_RADIUS, CAP_HEIGHT

    def top(x):          # height of the top surface at r / R = x
        return H * (1 - x ** 2) ** 0.55 + 3.2 * math.exp(-(x / 0.16) ** 2) + 1.2 * x ** 4

    def under(x):        # the gills' surface: rises from the margin to the stem
        return 9.5 * (1 - x) ** 1.3 + 0.2

    prof = [(0.0, top(0.0), 0.0)]
    n = 48
    for m in range(1, n + 1):
        x = m / n
        prof.append((x * R, top(x), x))                           # s in [0, 1]: the top, centre to margin
    for m in range(n - 1, 0, -1):
        x = m / n
        prof.append((x * R, under(x), 2.0 - x))                   # s in (1, 2): the gills, margin to centre
    prof.append((0.0, under(0.0), 2.0))

    def colour(s, phi):
        if s <= 1.0:
            c = ramp(CAP_STOPS, s + 0.04 * math.sin(5 * phi + 1.3))
            return mix(c, (0.99, 0.97, 0.90), 0.5 * max(0.0, s - 0.94) / 0.06)   # the very margin paler
        x = 2.0 - s
        return mix(GILLS, GILL_EDGE, max(0.0, (x - 0.86) / 0.14))

    def wobble(s, phi):  # a wavy margin, stronger towards the edge
        x = s if s <= 1 else 2.0 - s
        return 1.0 + x ** 3 * (0.045 * math.sin(5 * phi + 0.7) + 0.025 * math.sin(9 * phi + 2.1))

    v, f, c = revolve(prof, colour, wobble)
    tilt = math.radians(7.0)                                       # the cap leans with the stem
    rot = np.array([[math.cos(tilt), 0, math.sin(tilt)], [0, 1, 0], [-math.sin(tilt), 0, math.cos(tilt)]])
    v = v @ rot.T
    v[:, 0] += lean(1.0) + 0.8
    v[:, 2] += STEM_HEIGHT - 4.0 - under(0.0)    # the stem's top reaches 4 mm into the cap's flesh: one body
    return v, f, c


def build():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    parts = [("stipe", *stem()), ("annulus", *ring()), ("pileus", *cap())]
    objects = []
    for name, v, f, c in parts:
        mesh = bpy.data.meshes.new(name)
        mesh.from_pydata([tuple(p) for p in v], [], f)
        mesh.validate()
        attr = mesh.color_attributes.new(name="Col", type="BYTE_COLOR", domain="POINT")
        for i, col in enumerate(c):
            attr.data[i].color_srgb = (*col, 1.0)
        mesh.color_attributes.active_color = attr
        mesh.color_attributes.render_color_index = 0
        obj = bpy.data.objects.new(name, mesh)
        bpy.context.scene.collection.objects.link(obj)
        for poly in mesh.polygons:
            poly.use_smooth = True
        objects.append(obj)
    return objects


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True, help="path without suffix: writes .ply, .stl and .blend")
    args = ap.parse_args(argv)
    out = Path(args.out).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    objects = build()
    bpy.ops.object.select_all(action="DESELECT")
    for o in objects:
        o.select_set(True)
    bpy.context.view_layer.objects.active = objects[-1]
    bpy.ops.wm.ply_export(filepath=str(out.with_suffix(".ply")), export_selected_objects=True, export_colors="SRGB",
                          export_normals=False, export_uv=False, ascii_format=False, forward_axis="Y", up_axis="Z",
                          global_scale=1.0, apply_modifiers=True)
    bpy.ops.wm.stl_export(filepath=str(out.with_suffix(".stl")), export_selected_objects=True, forward_axis="Y",
                          up_axis="Z", global_scale=1.0, apply_modifiers=True)
    # only the scene and its data: a main-file save would also store the window manager with the file browser's
    # last folder (the user's home path) - not for a file that is published
    bpy.data.libraries.write(str(out.with_suffix(".blend")), {bpy.context.scene}, path_remap="RELATIVE_ALL",
                             fake_user=True)
    print("GOLDEN_TEACHER", out.with_suffix(".ply"), sum(len(o.data.vertices) for o in objects), "vertices")


main()
