"""Shared fixtures: small parts made on the fly (no files in the repository needed), Qt offscreen, few threads."""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
for _var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_var, "4")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402
import pytest  # noqa: E402
import trimesh  # noqa: E402


def box(lo, hi):
    m = trimesh.creation.box(np.subtract(hi, lo))
    m.apply_translation((np.add(lo, hi)) / 2)
    return m


@pytest.fixture(scope="session")
def parts(tmp_path_factory):
    """{name: path}: 'L' - a 32 x 16 x 9.6 slab with an 8 x 8 x 28.8 tower in its +x/+y corner (asymmetric, so a mixed
    up axis shows), 'sphere' (r 50), 'wall' (a thin 1 x 24 x 6 bricks wall), 'plate' (a 0.4 mm sheet), 'two' (two
    colours, as PLY: a red box for x < 15 and a blue one for x > 17)."""
    d = tmp_path_factory.mktemp("parts")
    out = {}
    L = trimesh.boolean.union([box((0, 0, 0), (32, 16, 9.6)), box((24, 8, 0), (32, 16, 28.8))], engine="manifold")
    L.export(d / "L.stl")
    out["L"] = d / "L.stl"
    trimesh.creation.icosphere(4, radius=50).export(d / "sphere.stl")
    out["sphere"] = d / "sphere.stl"
    box((0, 0, 0), (8 * 24, 8, 9.6 * 6)).export(d / "wall.stl")
    out["wall"] = d / "wall.stl"
    box((0, 0, 0), (80, 60, 0.4)).export(d / "plate.stl")
    out["plate"] = d / "plate.stl"
    left, right = box((0, 0, 0), (15, 32, 19.2)), box((17, 0, 0), (32, 32, 19.2))
    left.visual.face_colors = [200, 20, 25, 255]
    right.visual.face_colors = [10, 85, 175, 255]
    trimesh.util.concatenate([left, right]).export(d / "two.ply")
    out["two"] = d / "two.ply"
    return out
