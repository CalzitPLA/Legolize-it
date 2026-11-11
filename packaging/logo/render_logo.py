"""The Legolize it logo: a brick model (its own result.json), not fully built, rendered in Blender (Cycles).

    blender -b --factory-startup --python packaging/logo/render_logo.py -- --result build/result.json \
        --out packaging/legolize-it.png [--size 1024] [--samples 128] [--gpu] [--blend packaging/logo/legolize-it_logo.blend]

What "not fully built" means here: the layers up to `--built` (a share of the height) are complete; above, a wedge
on the camera's side is missing, widening towards the top (`--wedge` degrees each side at the top), so the model shows
its stepped inside like a build in progress while the rest of its surface stays whole. A few of the missing bricks
hover just above their places, slightly turned, as if being put on. Floating bricks of the result (not connected to
the bottom) are left out.

Standard library, numpy and bpy only (Blender's Python): lengths in the file are mm, the scene is in m (x 0.01).
"""
import argparse
import json
import math
import os
import sys
from pathlib import Path

import bpy
import numpy as np
from mathutils import Euler, Vector

U = 0.01                     # mm -> m
STUD_SEGMENTS = 32


def srgb_to_linear(c):
    c = np.asarray(c, float)
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


# ------------------------------------------------------------------------------------------------ the selection


def select(bricks, built, seed, flying, azimuth, wedge, wedge_offset):
    """(kept brick indices, [(index, lift_mm, (rx, ry, rz) degrees)] of the hovering bricks).

    Above `built` (a share of the height) a wedge facing the camera (turned by `wedge_offset` degrees) is missing; it
    widens from nothing to `wedge` degrees on each side at the top, so every kept brick still stands on kept bricks."""
    rng = np.random.default_rng(seed)
    layers = bricks[:, 0]
    kmin, kmax = int(layers.min()), int(layers.max())
    kcut = kmin + built * (kmax - kmin)
    top = layers >= kcut                                          # the axis of the top part (the cap)
    sel = top if top.any() else np.ones(len(bricks), bool)
    centre = np.array([(bricks[sel, 1] + bricks[sel, 3] / 2).mean(), (bricks[sel, 2] + bricks[sel, 4] / 2).mean()])
    cx = bricks[:, 1] + bricks[:, 3] / 2 - centre[0]
    cy = bricks[:, 2] + bricks[:, 4] / 2 - centre[1]
    facing = math.radians(azimuth + wedge_offset)
    theta = (np.arctan2(cy, cx) - facing + math.pi) % (2 * math.pi) - math.pi   # 0 = the wedge's middle
    keep = np.ones(len(bricks), bool)
    for n, b in enumerate(bricks):
        if b[0] <= kcut:
            continue
        u = (b[0] - kcut) / max(1e-9, kmax - kcut)               # 0 .. 1 over the unfinished layers
        half = math.radians(wedge) * u ** 0.6
        keep[n] = abs(theta[n]) > half + 0.08 * rng.standard_normal()
    # the hovering bricks: missing, chunky, resting on kept bricks once placed; the highest first, spread out
    occ = set()
    for n in np.flatnonzero(keep):
        occ.update(_cells(bricks[n]))
    cands = []
    for n in np.flatnonzero(~keep):
        b = bricks[n]
        support = sum((c[0], c[1], c[2] - 1) in occ for c in _cells(b))
        if support and min(b[3], b[4]) >= 2:
            tier = 0 if max(b[3], b[4]) <= 4 else (1 if max(b[3], b[4]) <= 6 else 2)   # compact ones read as bricks
            cands.append((-(b[0] // 3), tier, abs(theta[n]), n))   # the top first, compact ones within a band
    cands.sort()
    hover, used = [], []
    for _band, _tier, _t, n in cands:
        if len(hover) >= flying:
            break
        p = np.array([bricks[n, 1] + bricks[n, 3] / 2, bricks[n, 2] + bricks[n, 4] / 2])
        if any(np.linalg.norm(p - q) < 5.0 for q in used):
            continue
        used.append(p)
        m = len(hover)
        hover.append((int(n), 20.0 + 12.0 * m + 4.0 * rng.random(),            # mm: above, about to be put on
                      (rng.uniform(-8, 8), rng.uniform(-8, 8), rng.uniform(-25, 25))))
    return np.flatnonzero(keep), hover


def _cells(b):
    k, i, j, w, d = (int(x) for x in b)
    return [(a, q, k) for a in range(i, i + w) for q in range(j, j + d)]


def _overlap(a, b):
    return not (a[1] + a[3] <= b[1] or b[1] + b[3] <= a[1] or a[2] + a[4] <= b[2] or b[2] + b[4] <= a[2])


# ------------------------------------------------------------------------------------------------ geometry


class MeshBuilder:
    """Vertices and faces of many bricks, gathered per material; flat boxes, smooth stud sides, flat stud tops."""

    def __init__(self):
        self.v, self.f, self.smooth = [], [], []

    def box(self, lo, hi):
        b = len(self.v)
        x0, y0, z0 = lo
        x1, y1, z1 = hi
        self.v += [(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0),
                   (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)]
        for q in ((0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)):
            self.f.append(tuple(b + i for i in q))
            self.smooth.append(False)

    def stud(self, x, y, z0, r, h, seg=STUD_SEGMENTS):
        a = [2 * math.pi * s / seg for s in range(seg)]
        ring = [(x + r * math.cos(t), y + r * math.sin(t)) for t in a]
        b = len(self.v)
        self.v += [(px, py, z0) for px, py in ring] + [(px, py, z0 + h) for px, py in ring]
        for s in range(seg):                                   # side, smooth
            t = (s + 1) % seg
            self.f.append((b + s, b + t, b + seg + t, b + seg + s))
            self.smooth.append(True)
        c = len(self.v)                                        # top: its own ring, flat
        self.v += [(px, py, z0 + h) for px, py in ring]
        self.f.append(tuple(c + s for s in range(seg)))
        self.smooth.append(False)

    def to_object(self, name, material, bevel_mm=0.0):
        mesh = bpy.data.meshes.new(name)
        mesh.from_pydata(self.v, [], self.f)
        mesh.polygons.foreach_set("use_smooth", self.smooth)
        mesh.validate()
        mesh.materials.append(material)
        obj = bpy.data.objects.new(name, mesh)
        bpy.context.scene.collection.objects.link(obj)
        if bevel_mm > 0:
            mod = obj.modifiers.new("bevel", "BEVEL")
            mod.width = bevel_mm * U
            mod.segments = 2
            mod.limit_method = "ANGLE"
            mod.angle_limit = math.radians(40)
        return obj


def brick_into(mb, b, grid, sysd, studs_on, frame=None):
    """Add brick b (layer, i, j, w, d) to the MeshBuilder - in grid coordinates (mm -> m), or, with `frame` True, in
    local coordinates centred on the brick's bottom (a hovering brick, placed by its object's transform)."""
    k, i, j, w, d = (int(x) for x in b)
    p, h, c = grid["cell"][0], grid["cell"][2], sysd["clearance"]
    ox, oy, oz = grid["origin"]
    if frame is None:
        x0, y0, z0 = ox + i * p, oy + j * p, oz + k * h
    else:
        x0, y0, z0 = -w * p / 2, -d * p / 2, 0.0
    mb.box(((x0 + c) * U, (y0 + c) * U, z0 * U), ((x0 + w * p - c) * U, (y0 + d * p - c) * U, (z0 + h) * U))
    for a in range(w):
        for q in range(d):
            if studs_on(i + a, j + q, k):
                mb.stud((x0 + (a + 0.5) * p) * U, (y0 + (q + 0.5) * p) * U, (z0 + h) * U,
                        sysd["stud_diameter"] / 2 * U, sysd["stud_height"] * U)


def plastic(name, rgb):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    lin = srgb_to_linear(rgb)
    bsdf.inputs["Base Color"].default_value = (*lin, 1.0)
    bsdf.inputs["Roughness"].default_value = 0.30
    for key, val in (("Coat Weight", 0.25), ("Coat Roughness", 0.08), ("Specular IOR Level", 0.55)):
        if key in bsdf.inputs:
            bsdf.inputs[key].default_value = val
    mat.diffuse_color = (*lin, 1.0)
    return mat


# ------------------------------------------------------------------------------------------------ scene


def look_at(obj, target):
    direction = Vector(target) - obj.location
    obj.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()


def area_light(name, location, target, size, energy, colour=(1, 1, 1)):
    data = bpy.data.lights.new(name, "AREA")
    data.size = size
    data.energy = energy
    data.color = colour
    obj = bpy.data.objects.new(name, data)
    bpy.context.scene.collection.objects.link(obj)
    obj.location = location
    look_at(obj, target)
    return obj


def frame_camera(cam, points, azimuth, elevation, margin=0.86):
    """Place the camera on a ray from the points' centre (azimuth, elevation in degrees) so all points fit the square
    frame with `margin`, and centre their picture with the lens shift."""
    pts = np.asarray(points)
    centre = (pts.min(0) + pts.max(0)) / 2
    az, el = math.radians(azimuth), math.radians(elevation)
    back = np.array([math.cos(el) * math.cos(az), math.cos(el) * math.sin(az), math.sin(el)])
    fwd = -back
    right = np.cross(fwd, [0, 0, 1.0])
    right /= np.linalg.norm(right)
    up = np.cross(right, fwd)
    t = math.tan(cam.data.angle / 2)

    def extents(dist):
        rel = pts - (centre + back * dist)
        z = rel @ fwd
        return (rel @ right) / (z * t), (rel @ up) / (z * t)

    lo, hi = 0.1, 100.0
    for _ in range(60):
        mid = (lo + hi) / 2
        sx, sy = extents(mid)
        if max(np.ptp(sx), np.ptp(sy)) / 2 <= margin:
            hi = mid
        else:
            lo = mid
    sx, sy = extents(hi)
    cam.location = Vector(centre + back * hi)
    look_at(cam, centre)
    cam.data.shift_x = (sx.min() + sx.max()) / 4
    cam.data.shift_y = (sy.min() + sy.max()) / 4


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    ap = argparse.ArgumentParser()
    ap.add_argument("--result", required=True, help="result.json of Legolize it")
    ap.add_argument("--out", required=True, help="the picture (.png, RGBA)")
    ap.add_argument("--size", type=int, default=1024)
    ap.add_argument("--samples", type=int, default=128)
    ap.add_argument("--built", type=float, default=0.80, help="share of the height that is complete")
    ap.add_argument("--wedge", type=float, default=48.0, help="half-width of the missing wedge at the top (degrees)")
    ap.add_argument("--wedge-offset", type=float, default=-28.0, help="turn of the wedge from the camera (degrees)")
    ap.add_argument("--flying", type=int, default=3, help="bricks hovering above their places")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--azimuth", type=float, default=-58.0, help="camera direction around z (degrees)")
    ap.add_argument("--elevation", type=float, default=24.0)
    ap.add_argument("--gpu", action="store_true", help="Cycles on the GPU (CUDA/HIP) instead of the CPU")
    ap.add_argument("--blend", default=None, help="also save the scene")
    args = ap.parse_args(argv)

    res = json.loads(Path(args.result).read_text())
    grid, sysd = res["grid"], res["settings"]["system"]
    bricks_all = np.array(res["bricks"], int)
    colours_all = np.array(res["colours"], float)
    good = np.setdiff1d(np.arange(len(bricks_all)), res.get("floating_bricks", []))
    bricks, colours = bricks_all[good], colours_all[good]
    h = grid["cell"][2]
    keep, hover = select(bricks, args.built, args.seed, args.flying, args.azimuth, args.wedge, args.wedge_offset)

    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    occ = set()
    for n in keep:
        occ.update(_cells(bricks[n]))
    builders, materials = {}, {}

    def material_for(rgb):
        key = tuple(np.round(rgb, 3))
        if key not in materials:
            materials[key] = plastic(f"plastic_{len(materials)}", rgb)
            builders[key] = MeshBuilder()
        return key

    for n in keep:
        key = material_for(colours[n])
        brick_into(builders[key], bricks[n], grid, sysd, lambda a, q, k: (a, q, k + 1) not in occ)
    points = []
    for key, mb in builders.items():
        obj = mb.to_object(f"bricks_{len(points)}", materials[key], bevel_mm=0.35)
        points += [tuple(obj.matrix_world @ Vector(c)) for c in obj.bound_box]
    p, ox, oy, oz = grid["cell"][0], *grid["origin"]
    for n, lift, (rx, ry, rz) in hover:
        b = bricks[n]
        key = material_for(colours[n])
        mb = MeshBuilder()
        brick_into(mb, b, grid, sysd, lambda a, q, k: True, frame=True)
        obj = mb.to_object(f"hovering_{n}", materials[key], bevel_mm=0.35)
        cx, cy = ox + (b[1] + b[3] / 2) * p, oy + (b[2] + b[4] / 2) * p
        obj.location = (cx * U, cy * U, (oz + b[0] * h + lift) * U)
        obj.rotation_euler = Euler((math.radians(rx), math.radians(ry), math.radians(rz)))
        bpy.context.view_layer.update()
        points += [tuple(obj.matrix_world @ Vector(c)) for c in obj.bound_box]

    pts = np.array(points)
    lo, hi = pts.min(0), pts.max(0)
    centre = (lo + hi) / 2
    size = float(np.linalg.norm(hi - lo))
    bpy.ops.mesh.primitive_plane_add(size=size * 6, location=(centre[0], centre[1], lo[2]))
    ground = bpy.context.active_object
    ground.is_shadow_catcher = True
    world = bpy.data.worlds.new("world")
    world.use_nodes = True
    bg = world.node_tree.nodes["Background"]
    bg.inputs["Color"].default_value = (0.80, 0.82, 0.86, 1.0)
    bg.inputs["Strength"].default_value = 0.30
    scene.world = world
    az = math.radians(args.azimuth)
    key_dir = np.array([math.cos(az - 0.8), math.sin(az - 0.8), 1.9])     # high: a short shadow
    area_light("key", tuple(centre + size * 1.3 * key_dir / np.linalg.norm(key_dir)), centre, size * 0.9, 42 * size ** 2)
    fill_dir = np.array([math.cos(az + 1.0), math.sin(az + 1.0), 0.45])
    area_light("fill", tuple(centre + size * 1.5 * fill_dir / np.linalg.norm(fill_dir)), centre, size * 1.2,
               14 * size ** 2, (0.95, 0.97, 1.0))
    rim_dir = np.array([math.cos(az + math.pi), math.sin(az + math.pi), 0.9])
    area_light("rim", tuple(centre + size * 1.4 * rim_dir / np.linalg.norm(rim_dir)), centre, size * 0.7,
               34 * size ** 2, (1.0, 0.96, 0.9))

    cam_data = bpy.data.cameras.new("camera")
    cam_data.lens = 70
    cam = bpy.data.objects.new("camera", cam_data)
    scene.collection.objects.link(cam)
    scene.camera = cam
    frame_camera(cam, pts, args.azimuth, args.elevation)

    scene.render.engine = "CYCLES"
    scene.cycles.samples = args.samples
    # denoise where the build can (Debian's Blender has no OpenImageDenoise: more samples instead)
    scene.cycles.use_denoising = bool(getattr(bpy.app.build_options, "openimagedenoise", False))
    scene.cycles.use_adaptive_sampling = True
    scene.cycles.device = "CPU"
    if args.gpu:
        prefs = bpy.context.preferences.addons["cycles"].preferences
        for kind in ("OPTIX", "CUDA", "HIP"):
            try:
                prefs.compute_device_type = kind
                prefs.get_devices()
                for dev in prefs.devices:
                    dev.use = dev.type != "CPU"
                scene.cycles.device = "GPU"
                break
            except Exception:
                continue
    scene.render.film_transparent = True
    scene.render.resolution_x = scene.render.resolution_y = args.size
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"
    scene.view_settings.view_transform = "Standard"           # the plastic colours as they are (AgX pales them)
    scene.view_settings.look = "None"
    scene.view_settings.exposure = -0.9
    out = Path(args.out).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    if args.blend:                  # the scene alone, its output path relative: no home path in a published file
        blend = Path(args.blend).resolve()
        scene.render.filepath = "//" + os.path.relpath(out, blend.parent)
        bpy.data.libraries.write(str(blend), {scene}, path_remap="RELATIVE_ALL", fake_user=True)
    scene.render.filepath = str(out)
    bpy.ops.render.render(write_still=True)
    print("LEGOLIZE_LOGO", out, f"{len(keep)} of {len(bricks)} bricks built, {len(hover)} hovering:",
          ", ".join(f"{min(bricks[n, 3:5])}x{max(bricks[n, 3:5])} (layer {bricks[n, 0]}, +{lift:.0f} mm)" for n, lift, _r in hover))


main()
