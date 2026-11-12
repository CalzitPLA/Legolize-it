"""The 3D scene of a result, in plain VTK (no Qt): the window's view and the offscreen pictures (`render_png`) draw
the same thing.

Layers:
    input    the part as given, scaled onto the grid: light grey and translucent (opacity 0.22)
    bricks   one actor per layer, opaque, a colour per brick (lego_engine.palette; every brick's brightness varies a
             little, so neighbours of one colour stay apart); bricks of floating groups magenta when marked
    voxels   the raw voxelization (cells at or above the threshold), translucent - off by default
The layer limit shows the stack only up to a layer (building it up), the camera presets are Iso / Front / Side / Top.
"""
from __future__ import annotations

from typing import Dict, List, Optional

import numpy as np

INPUT_COLOUR, INPUT_OPACITY = (0.80, 0.80, 0.78), 0.22
VOXEL_COLOUR, VOXEL_OPACITY = (0.36, 0.72, 0.86), 0.25
FLOATING = (0.90, 0.10, 0.75)
BACKGROUND = (0.97, 0.97, 0.96)


def polydata(vertices: np.ndarray, triangles: np.ndarray, cell_rgb: Optional[np.ndarray] = None, smooth: bool = False):
    from vtkmodules.util import numpy_support
    from vtkmodules.vtkCommonCore import vtkPoints
    from vtkmodules.vtkCommonDataModel import vtkCellArray, vtkPolyData
    pts = vtkPoints()
    pts.SetData(numpy_support.numpy_to_vtk(np.ascontiguousarray(vertices, dtype=float), deep=True))
    tri = np.asarray(triangles, dtype=np.int64)
    cells = vtkCellArray()
    cells.SetData(numpy_support.numpy_to_vtkIdTypeArray(np.arange(0, 3 * len(tri) + 1, 3, dtype=np.int64), deep=True),
                  numpy_support.numpy_to_vtkIdTypeArray(np.ascontiguousarray(tri.ravel()), deep=True))
    poly = vtkPolyData()
    poly.SetPoints(pts)
    poly.SetPolys(cells)
    if cell_rgb is not None:
        arr = numpy_support.numpy_to_vtk(np.ascontiguousarray(np.clip(cell_rgb * 255, 0, 255).astype(np.uint8)),
                                         deep=True)
        arr.SetName("colours")
        poly.GetCellData().SetScalars(arr)
    if smooth:
        from vtkmodules.vtkFiltersCore import vtkPolyDataNormals
        nf = vtkPolyDataNormals()
        nf.SetInputData(poly)
        nf.SetFeatureAngle(35.0)
        nf.SplittingOn()
        nf.ConsistencyOff()
        nf.Update()
        poly = nf.GetOutput()
    return poly


def _actor(renderer, colour=(1, 1, 1), opacity=1.0, scalars=False, plastic=True):
    from vtkmodules.vtkRenderingCore import vtkActor, vtkPolyDataMapper
    mapper = vtkPolyDataMapper()
    if scalars:
        mapper.ScalarVisibilityOn()
        mapper.SetScalarModeToUseCellData()
        mapper.SetColorModeToDirectScalars()
    else:
        mapper.ScalarVisibilityOff()
    actor = vtkActor()
    actor.SetMapper(mapper)
    prop = actor.GetProperty()
    prop.SetColor(*colour)
    prop.SetOpacity(opacity)
    if plastic:
        prop.SetSpecular(0.35)
        prop.SetSpecularPower(40)
        prop.SetDiffuse(0.85)
        prop.SetAmbient(0.12)
    renderer.AddActor(actor)
    return actor


def jitter(colours: np.ndarray, amount: float = 0.06) -> np.ndarray:
    """Every brick's colour a little brighter or darker (from its index): bricks of one colour stay apart."""
    n = len(colours)
    f = 1.0 + amount * np.sin(np.arange(n) * 2.399963)          # golden-angle steps: neighbours differ
    return np.clip(colours * f[:, None], 0, 1)


class Scene:
    """The actors of one result in `renderer` (a vtkRenderer); `set_result(result)` rebuilds them."""

    def __init__(self, renderer):
        self.renderer = renderer
        renderer.SetBackground(*BACKGROUND)
        renderer.SetUseDepthPeeling(1)
        renderer.SetMaximumNumberOfPeels(12)
        renderer.SetOcclusionRatio(0.05)
        self._light_kit()
        self.input = _actor(renderer, INPUT_COLOUR, INPUT_OPACITY, plastic=False)
        self.voxels = _actor(renderer, VOXEL_COLOUR, VOXEL_OPACITY, plastic=False)
        self.layers: List = []                 # per layer: bodies and the studs nothing covers
        self.caps: List = []                   # per layer: the covered studs (shown when the view stops there)
        self.layer_index: List[int] = []
        self.visible: Dict[str, bool] = {"input": True, "bricks": True, "voxels": False}
        self.limit: Optional[int] = None
        self.result = None
        self.bounds = None
        for a in (self.input, self.voxels):
            a.VisibilityOff()

    def _light_kit(self):
        from vtkmodules.vtkRenderingCore import vtkLightKit
        kit = vtkLightKit()
        kit.SetKeyLightIntensity(0.85)
        kit.AddLightsToRenderer(self.renderer)

    def clear(self):
        for a in self.layers + [c for c in self.caps if c is not None]:
            self.renderer.RemoveActor(a)
        self.layers, self.caps, self.layer_index, self.result, self.bounds = [], [], [], None, None
        self.input.VisibilityOff()
        self.voxels.VisibilityOff()

    def set_result(self, result, colour_mode: Optional[str] = None, mark_floating: bool = True,
                   fade: Optional[str] = None, single: Optional[str] = None):
        from lego_engine.analysis import connectivity
        from lego_engine.geometry import bricks_display, voxel_mesh
        self.clear()
        self.result = result
        self.input.GetMapper().SetInputData(polydata(result.placed_vertices, result.faces, smooth=True))
        vv, vf = voxel_mesh(result.occupancy, result.grid, shrink=0.3)
        if len(vf):
            self.voxels.GetMapper().SetInputData(polydata(vv, vf))
        colours = jitter(result.colours(colour_mode, fade, single))
        if mark_floating:
            for n in connectivity(result.model)["floating"]:
                colours[n] = FLOATING
        model = result.model
        for k in range(model.shape[2]):
            idx = model.layer_bricks(k)
            if not idx:
                continue
            v, f, owner = bricks_display(model, result.grid, result.system, idx)
            actor = _actor(self.renderer, scalars=True)
            actor.GetMapper().SetInputData(polydata(v, f, colours[owner], smooth=True))
            cv, cf, cowner = bricks_display(model, result.grid, result.system, idx, studs="covered", bodies=False)
            cap = None
            if len(cf):
                cap = _actor(self.renderer, scalars=True)
                cap.GetMapper().SetInputData(polydata(cv, cf, colours[cowner], smooth=True))
            self.layers.append(actor)
            self.caps.append(cap)
            self.layer_index.append(k)
        lo = np.minimum(result.placed_vertices.min(0), result.grid.origin)
        hi = np.maximum(result.placed_vertices.max(0), result.grid.origin + result.grid.size)
        self.bounds = (lo, hi)
        self.apply()

    def dim_except(self, layer: Optional[int], amount: float = 0.5):
        """The layers below `layer` pale and flat, so the bricks of `layer` stand out (None: all as usual)."""
        for k, actor in zip(self.layer_index, self.layers):
            prop = actor.GetProperty()
            dim = layer is not None and k < layer
            prop.SetAmbient(amount if dim else 0.12)
            prop.SetDiffuse(1.0 - amount if dim else 0.85)
            prop.SetSpecular(0.0 if dim else 0.35)
            prop.SetOpacity(0.45 if dim else 1.0)

    def set_visible(self, name: str, on: bool):
        self.visible[name] = bool(on)
        self.apply()

    def set_limit(self, k: Optional[int]):
        """Show the brick layers up to layer k (None: all)."""
        self.limit = k
        self.apply()

    def apply(self):
        has = self.result is not None
        self.input.SetVisibility(int(has and self.visible["input"]))
        self.voxels.SetVisibility(int(has and self.visible["voxels"] and self.voxels.GetMapper().GetInput() is not None))
        top = None if self.limit is None else max([k for k in self.layer_index if k <= self.limit], default=None)
        for k, actor, cap in zip(self.layer_index, self.layers, self.caps):
            on = self.visible["bricks"] and (self.limit is None or k <= self.limit)
            actor.SetVisibility(int(on))
            if cap is not None:
                cap.SetVisibility(int(on and top is not None and k == top and k != self.layer_index[-1]))

    def view(self, name: str = "iso"):
        if self.bounds is None:
            return
        lo, hi = self.bounds
        c = (lo + hi) / 2
        d = 2.4 * float(np.linalg.norm(hi - lo))
        cam = self.renderer.GetActiveCamera()
        cam.SetFocalPoint(*c)
        cam.SetViewUp(0, 0, 1)
        offset = {"iso": (0.62, -0.68, 0.48), "front": (0, -1, 0), "side": (1, 0, 0), "top": (0, 0, 1)}[name]
        cam.SetPosition(*(c + d * np.asarray(offset)))
        if name == "top":
            cam.SetViewUp(0, 1, 0)
        self.renderer.ResetCamera()
        cam.Zoom(1.25)
        self.renderer.ResetCameraClippingRange()


def render_png(result, path, size=(1280, 960), colour_mode: Optional[str] = None, view: str = "iso",
               limit: Optional[int] = None, show_input: bool = True, fade: Optional[str] = None,
               single: Optional[str] = None, highlight=None, background=None):
    """An offscreen picture of the result (as the window draws it) -> path."""
    from pathlib import Path
    import vtkmodules.vtkRenderingOpenGL2  # noqa: F401
    from vtkmodules.vtkIOImage import vtkPNGWriter
    from vtkmodules.vtkRenderingCore import vtkRenderer, vtkRenderWindow, vtkWindowToImageFilter
    window = vtkRenderWindow()
    window.SetOffScreenRendering(1)
    window.SetAlphaBitPlanes(1)
    window.SetMultiSamples(0)
    window.SetSize(*size)
    renderer = vtkRenderer()
    window.AddRenderer(renderer)
    scene = Scene(renderer)
    scene.set_result(result, colour_mode, fade=fade, single=single)
    if background is not None:
        renderer.SetBackground(*background)
    if highlight is not None:                    # the building manual: this step's bricks bright, the rest dimmed
        scene.dim_except(highlight)
    scene.visible["input"] = show_input
    scene.set_limit(limit)
    scene.view(view)
    window.Render()
    grab = vtkWindowToImageFilter()
    grab.SetInput(window)
    grab.ReadFrontBufferOff()
    grab.Update()
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    writer = vtkPNGWriter()
    writer.SetFileName(str(path))
    writer.SetInputConnection(grab.GetOutputPort())
    writer.Write()
    window.Finalize()
    return path


def render_steps(result, out_dir, size=(900, 760), colours=None, prefix: str = "step", progress=None):
    """Offscreen pictures of the build, one per layer (the model up to that layer, the layer bright, the ones below
    pale), all from the same iso camera; plus "<prefix>_final.png". -> {layer: path, "final": path}."""
    from pathlib import Path
    import vtkmodules.vtkRenderingOpenGL2  # noqa: F401
    from vtkmodules.vtkIOImage import vtkPNGWriter
    from vtkmodules.vtkRenderingCore import vtkRenderer, vtkRenderWindow, vtkWindowToImageFilter
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    window = vtkRenderWindow()
    window.SetOffScreenRendering(1)
    window.SetMultiSamples(0)
    window.SetSize(*size)
    renderer = vtkRenderer()
    window.AddRenderer(renderer)
    scene = Scene(renderer)
    scene.set_result(result, mark_floating=False)
    if colours is not None:                                  # the colours of the export (the view's choice)
        from vtkmodules.util import numpy_support
        cols = jitter(np.asarray(colours, float))
        for k, actor in zip(scene.layer_index, scene.layers):
            idx = result.model.layer_bricks(k)
            from lego_engine.geometry import bricks_display
            _v, _f, owner = bricks_display(result.model, result.grid, result.system, idx)
            arr = numpy_support.numpy_to_vtk(np.ascontiguousarray((np.clip(cols[owner], 0, 1) * 255).astype(np.uint8)),
                                             deep=True)
            arr.SetName("colours")
            actor.GetMapper().GetInput().GetCellData().SetScalars(arr)
        for k, cap in zip(scene.layer_index, scene.caps):
            if cap is not None:
                idx = result.model.layer_bricks(k)
                from lego_engine.geometry import bricks_display
                _v, _f, owner = bricks_display(result.model, result.grid, result.system, idx, studs="covered",
                                               bodies=False)
                arr = numpy_support.numpy_to_vtk(
                    np.ascontiguousarray((np.clip(cols[owner], 0, 1) * 255).astype(np.uint8)), deep=True)
                arr.SetName("colours")
                cap.GetMapper().GetInput().GetCellData().SetScalars(arr)
    scene.visible["input"] = False
    renderer.SetBackground(1.0, 1.0, 1.0)
    scene.set_limit(None)
    scene.view("iso")
    grab = vtkWindowToImageFilter()
    grab.SetInput(window)
    grab.ReadFrontBufferOff()
    writer = vtkPNGWriter()
    writer.SetInputConnection(grab.GetOutputPort())
    out = {}

    def shoot(path):
        window.Render()
        grab.Modified()
        grab.Update()
        writer.SetFileName(str(path))
        writer.Write()
        return path

    out["final"] = shoot(out_dir / f"{prefix}_final.png")
    for k in scene.layer_index:
        scene.set_limit(k)
        scene.dim_except(k)
        out[k] = shoot(out_dir / f"{prefix}_{k:03d}.png")
        if progress:
            progress(f"picture of layer {k}")
    window.Finalize()
    return out
