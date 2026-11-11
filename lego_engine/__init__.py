"""Legolize it - the engine: STL/STEP -> voxels on the brick grid -> bricks -> STL stack and FreeCAD document.

    from lego_engine import LegoSettings, Legolizer
    result = Legolizer(print).run(LegoSettings(input="part.step", size=24, unit="plate"))
    result.stats["bricks"], result.model.bricks[0]

Modules: system (dimensions, catalogue), mesh_io (loading, STEP via freecadcmd), voxelize (placement, fill fraction),
layout (bricks per layer), analysis (connectivity, numbers), geometry (display meshes, printable solids), palette
(colours), pipeline (the steps with caches), export_stl, export_freecad (+ freecad_build, run inside FreeCAD), cli.
"""
from .pipeline import LegoResult, LegoSettings, Legolizer, run
from .system import DEFAULT_SIZES, BrickSystem

__version__ = "0.1.0"
__all__ = ["BrickSystem", "DEFAULT_SIZES", "LegoResult", "LegoSettings", "Legolizer", "run", "__version__"]
