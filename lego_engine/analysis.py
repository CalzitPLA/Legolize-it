"""What holds and what does not, and the numbers of a brick model.

Bricks hold together only through studs: two bricks are connected when they are in neighbouring layers and their
footprints share at least one cell. Bricks side by side in one layer are not connected. A group of connected bricks
that does not reach the lowest layer is floating - it has nothing under it (it would fall off when built). The largest
group is the model itself and never floating: when a small stray body is the only thing in the lowest layer (seen on
ABC parts with several bodies, 2026-11-11: 38 of 41 bricks were "floating"), the model still stands on its own bottom.
"""
from __future__ import annotations

from typing import Dict, List

import numpy as np

from .layout import BrickModel
from .system import BrickSystem


class _UnionFind:
    def __init__(self, n: int):
        self.parent = list(range(n))

    def find(self, a: int) -> int:
        while self.parent[a] != a:
            self.parent[a] = self.parent[self.parent[a]]
            a = self.parent[a]
        return a

    def union(self, a: int, b: int):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[max(ra, rb)] = min(ra, rb)


def connectivity(model: BrickModel) -> Dict:
    """{"groups": number of connected groups, "largest": bricks in the largest, "floating": [brick indices of the
    groups that do not reach the lowest layer], "group_of": [group id per brick]}."""
    n = len(model.bricks)
    if n == 0:
        return {"groups": 0, "largest": 0, "floating": [], "group_of": []}
    uf = _UnionFind(n)
    ids = model.ids
    for k in range(1, ids.shape[2]):
        a, b = ids[:, :, k], ids[:, :, k - 1]
        both = (a >= 0) & (b >= 0)
        if both.any():
            for x, y in set(zip(a[both].tolist(), b[both].tolist())):
                uf.union(x, y)
    roots = [uf.find(m) for m in range(n)]
    lowest = min(b.layer for b in model.bricks)
    grounded = {roots[m] for m, b in enumerate(model.bricks) if b.layer == lowest}
    sizes: Dict[int, int] = {}
    for r in roots:
        sizes[r] = sizes.get(r, 0) + 1
    grounded.add(max(sizes, key=lambda r: (sizes[r], -r)))      # the largest group: the model itself
    order = {r: g for g, r in enumerate(sorted(sizes, key=lambda r: -sizes[r]))}
    return {"groups": len(sizes), "largest": max(sizes.values()),
            "floating": [m for m in range(n) if roots[m] not in grounded],
            "group_of": [order[r] for r in roots]}


def layer_islands(model: BrickModel) -> List[int]:
    """Per layer: the number of 4-connected pieces of its cells (the bodies of that layer's printed slab)."""
    from scipy import ndimage
    occ = model.occupancy
    return [int(ndimage.label(occ[:, :, k])[1]) for k in range(occ.shape[2])]


def stats(model: BrickModel, system: BrickSystem, unit: str) -> Dict:
    """Counts and sizes for the parts list."""
    occ = model.occupancy
    h = system.layer_height(unit)
    layers = [k for k in range(occ.shape[2]) if occ[:, :, k].any()]
    con = connectivity(model)
    per_layer = []
    for k in layers:
        idx = model.layer_bricks(k)
        per_layer.append({"layer": k, "z_mm": round(k * h, 3), "bricks": len(idx), "cells": int(occ[:, :, k].sum()),
                          "axis": model.layer_axis.get(k, "x")})
    above = np.zeros_like(occ)
    above[:, :, :-1] = occ[:, :, 1:]
    return {
        "bricks": len(model.bricks),
        "cells": int(occ.sum()),
        "studs_visible": int((occ & ~above).sum()),
        "layers": len(layers),
        "grid": [int(x) for x in occ.shape],
        "size_mm": [round(occ.shape[0] * system.pitch, 2), round(occ.shape[1] * system.pitch, 2),
                    round(occ.shape[2] * h, 2)],
        "counts": model.counts(),
        "per_layer": per_layer,
        "groups": con["groups"],
        "largest_group": con["largest"],
        "floating": len(con["floating"]),
        "stable": con["groups"] == 1 and not con["floating"],
    }
