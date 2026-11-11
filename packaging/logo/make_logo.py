"""The Legolize it logo, made by Blender and by Legolize it itself (run from the repository root with the system Python):

    python3 packaging/logo/make_logo.py [--samples 384] [--gpu] [--quick]

1. Blender models a Golden Teacher (packaging/logo/golden_teacher.py) -> examples/golden_teacher.{ply,stl,blend};
2. Legolize it turns it into bricks: 18 studs across the cap, plates, threshold 0.38 (the thin ring and the cap's margin
   stay), colours from the painted surface snapped to brick colours -> packaging/logo/build/result.json (+ the STL
   stack and a picture of the window's view, the framework's own outputs);
3. Blender renders the bricks, not fully built (packaging/logo/render_logo.py) -> packaging/legolize-it.png (1024 px, RGBA)
   and the scene packaging/logo/legolize-it_logo.blend;
4. the logo cropped square around the bricks (the shadow faded out), and the icon sizes
   packaging/icons/legolize-it_{512,256,128,64,32}.png.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LOGO_DIR = ROOT / "packaging" / "logo"
BUILD = LOGO_DIR / "build"
LEGO_ARGS = ["--studs", "18", "--unit", "plate", "--threshold", "0.38", "--colours", "source"]


def blender(script: Path, *args: str) -> str:
    exe = shutil.which("blender")
    if exe is None:
        sys.exit("blender not found (Debian/Ubuntu: sudo apt install blender)")
    proc = subprocess.run([exe, "-b", "--factory-startup", "--python", str(script), "--", *args],
                          capture_output=True, text=True)
    if proc.returncode != 0 or "Traceback" in proc.stdout + proc.stderr:
        sys.exit(f"{script.name} failed:\n{(proc.stdout + proc.stderr)[-3000:]}")
    return proc.stdout


def square_logo(path, pad: float = 0.06):
    """The render cropped to a square around the bricks (not their shadow), the shadow faded out towards the edge so
    the frame never cuts it."""
    import numpy as np
    from PIL import Image
    im = np.asarray(Image.open(path).convert("RGBA")).astype(float)
    solid = im[:, :, 3] >= 235                                 # the bricks; the shadow catcher is translucent
    ys, xs = np.nonzero(solid)
    x0, x1, y0, y1 = xs.min(), xs.max(), ys.min(), ys.max()
    half = int(max(x1 - x0, y1 - y0) / 2 * (1 + pad)) + 2
    cx, cy = (x0 + x1) // 2, (y0 + y1) // 2
    out = np.zeros((2 * half, 2 * half, 4))
    sx0, sy0 = max(0, cx - half), max(0, cy - half)
    sx1, sy1 = min(im.shape[1], cx + half), min(im.shape[0], cy + half)
    out[sy0 - (cy - half):sy1 - (cy - half), sx0 - (cx - half):sx1 - (cx - half)] = im[sy0:sy1, sx0:sx1]
    yy, xx = np.mgrid[0:2 * half, 0:2 * half]
    r = np.hypot(xx - half, yy - half) / half
    fade = np.clip((1.0 - r) / 0.25, 0, 1)                     # shadow pixels fade over the outer quarter
    shadow = out[:, :, 3] < 235
    out[:, :, 3] = np.where(shadow, out[:, :, 3] * fade, out[:, :, 3])
    return Image.fromarray(out.clip(0, 255).astype(np.uint8), "RGBA")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--samples", type=int, default=384)
    ap.add_argument("--size", type=int, default=1024)
    ap.add_argument("--gpu", action="store_true")
    ap.add_argument("--quick", action="store_true", help="512 px, 48 samples: a look, not the logo")
    args = ap.parse_args()
    t0 = time.time()
    print("1/4 Blender: the Golden Teacher")
    out = blender(LOGO_DIR / "golden_teacher.py", "--out", str(ROOT / "examples" / "golden_teacher"))
    print("   ", [ln for ln in out.splitlines() if ln.startswith("GOLDEN_TEACHER")][-1])

    print("2/4 Legolize it: into bricks")
    BUILD.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run([sys.executable, "-m", "lego_engine", str(ROOT / "examples" / "golden_teacher.ply"),
                           *LEGO_ARGS, "--out", str(BUILD), "--stl", "layers", "--png", str(BUILD / "window_view.png"),
                           "--quiet"], cwd=ROOT, capture_output=True, text=True)
    if proc.returncode != 0:
        sys.exit(proc.stderr[-3000:])
    summary = json.loads(proc.stdout)
    print(f"    {summary['bricks']} bricks in {summary['layers']} layers, floating {summary['floating']}")

    print("3/4 Blender: the render")
    size, samples = (512, 48) if args.quick else (args.size, args.samples)
    target = BUILD / "logo_quick.png" if args.quick else ROOT / "packaging" / "legolize-it.png"
    render_args = ["--result", str(BUILD / "result.json"), "--out", str(target), "--size", str(size),
                   "--samples", str(samples)] + (["--gpu"] if args.gpu else [])
    if not args.quick:
        render_args += ["--blend", str(LOGO_DIR / "legolize-it_logo.blend")]
    out = blender(LOGO_DIR / "render_logo.py", *render_args)
    print("   ", [ln for ln in out.splitlines() if ln.startswith("LEGOLIZE_LOGO")][-1])
    if args.quick:
        print(f"done in {time.time() - t0:.0f} s: {target}")
        return

    print("4/4 the square logo and the icon sizes")
    raw = BUILD / "render_raw.png"
    shutil.copyfile(target, raw)
    big = square_logo(raw)
    big.save(target)
    icons = ROOT / "packaging" / "icons"
    icons.mkdir(exist_ok=True)
    from PIL import Image
    for px in (512, 256, 128, 64, 32):
        big.resize((px, px), Image.LANCZOS).save(icons / f"legolize-it_{px}.png")
    print(f"done in {time.time() - t0:.0f} s: {target}, {icons}/")


if __name__ == "__main__":
    main()
