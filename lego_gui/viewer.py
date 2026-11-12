"""The 3D view: lego_gui.scene in a Qt widget, with its bar - the layers (input translucent, bricks opaque, voxels),
the colours (with the fade preset or the single colour), a slider that builds the stack up layer by layer, the camera
presets and a screenshot - and below it the resolution: studs along the longest side, coarse to fine, which redoes the
model (`resolutionChanged`).

Without a result it shows the logo. Degrades to a text note without VTK/OpenGL (or with LEGOLIZE_NO_3D set).
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from PyQt5 import QtCore, QtGui, QtWidgets

from lego_engine.palette import FADES, MODES as COLOUR_MODES, NAMES

ROOT = Path(__file__).resolve().parent.parent
LOGO = ROOT / "packaging" / "legolize-it.png"
COLOUR_LABELS = {"auto": "auto (the input's if any)", "source": "from the input", "size": "by size",
                 "layer": "by layer", "mixed": "mixed", "random": "random", "rainbow": "rainbow", "fade": "fade",
                 "single": "one colour"}
RESOLUTION = (2, 160)                  # studs along the longest side, the slider's range


class BrickView(QtWidgets.QWidget):
    colourModeChanged = QtCore.pyqtSignal(str)
    resolutionChanged = QtCore.pyqtSignal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.scene = None
        self.vtk_ok = False
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        bar = QtWidgets.QHBoxLayout()
        bar.setContentsMargins(6, 4, 6, 0)
        self.checks = {}
        for name, label, on, tip in (("input", "Input", True, "the part as given, translucent"),
                                     ("bricks", "Bricks", True, "the brick model, opaque"),
                                     ("voxels", "Voxels", False, "the raw voxelization (cells at or above the threshold)")):
            box = QtWidgets.QCheckBox(label)
            box.setChecked(on)
            box.setToolTip(tip)
            box.toggled.connect(lambda v, n=name: self._toggle(n, v))
            self.checks[name] = box
            bar.addWidget(box)
        self.mark_floating = QtWidgets.QCheckBox("Mark floating")
        self.mark_floating.setChecked(True)
        self.mark_floating.setToolTip("bricks that are not connected to the bottom layer in magenta")
        self.mark_floating.toggled.connect(lambda _v: self.redraw())
        bar.addWidget(self.mark_floating)
        bar.addSpacing(10)
        bar.addWidget(QtWidgets.QLabel("Colours"))
        self.colour = QtWidgets.QComboBox()
        for m in COLOUR_MODES:
            self.colour.addItem(COLOUR_LABELS[m], m)
        self.colour.currentIndexChanged.connect(self._colour_changed)
        bar.addWidget(self.colour)
        self.fade = QtWidgets.QComboBox()
        for name in FADES:
            self.fade.addItem(name, name)
        self.fade.setToolTip("the gradient from the bottom to the top (snapped to brick colours)")
        self.fade.currentIndexChanged.connect(self._colour_changed)
        bar.addWidget(self.fade)
        self.single = QtWidgets.QComboBox()
        for name in NAMES:
            self.single.addItem(name.replace("_", " "), name)
        self.single.currentIndexChanged.connect(self._colour_changed)
        bar.addWidget(self.single)
        self.fade.hide()
        self.single.hide()
        bar.addSpacing(10)
        bar.addWidget(QtWidgets.QLabel("Layers"))
        self.slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.slider.setMinimumWidth(140)
        self.slider.setEnabled(False)
        self.slider.setToolTip("build the stack up: show the layers up to this one")
        self.slider.valueChanged.connect(self._limit)
        bar.addWidget(self.slider, 1)
        self.slider_label = QtWidgets.QLabel("-")
        self.slider_label.setMinimumWidth(70)
        bar.addWidget(self.slider_label)
        for text, view in (("Iso", "iso"), ("Front", "front"), ("Side", "side"), ("Top", "top")):
            b = QtWidgets.QToolButton()
            b.setText(text)
            b.clicked.connect(lambda _=False, v=view: self.set_view(v))
            bar.addWidget(b)
        shot = QtWidgets.QToolButton()
        shot.setText("PNG")
        shot.setToolTip("save a picture of the view")
        shot.clicked.connect(self._screenshot)
        bar.addWidget(shot)
        holder = QtWidgets.QWidget()
        holder.setLayout(bar)
        layout.addWidget(holder)

        row = QtWidgets.QHBoxLayout()
        row.setContentsMargins(6, 0, 6, 0)
        row.addWidget(QtWidgets.QLabel("Resolution"))
        row.addWidget(QtWidgets.QLabel("coarse"))
        self.resolution = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.resolution.setRange(*RESOLUTION)
        self.resolution.setValue(16)
        self.resolution.setToolTip("studs along the part's longest side: fewer - a coarse, blocky likeness; more - a "
                                   "finer one with more bricks (the model is redone as you go)")
        self.resolution.valueChanged.connect(self._resolution_moved)
        row.addWidget(self.resolution, 1)
        row.addWidget(QtWidgets.QLabel("fine"))
        self.res_spin = QtWidgets.QSpinBox()
        self.res_spin.setRange(1, 1000)
        self.res_spin.setValue(16)
        self.res_spin.setSuffix(" studs")
        self.res_spin.setKeyboardTracking(False)
        self.res_spin.valueChanged.connect(self._resolution_typed)
        row.addWidget(self.res_spin)
        self.res_label = QtWidgets.QLabel("")
        self.res_label.setMinimumWidth(240)
        row.addWidget(self.res_label)
        holder2 = QtWidgets.QWidget()
        holder2.setLayout(row)
        layout.addWidget(holder2)

        self.stack = QtWidgets.QStackedWidget()
        self.logo = QtWidgets.QLabel()
        self.logo.setAlignment(QtCore.Qt.AlignCenter)
        self.logo.setStyleSheet("background: #f7f7f5; color: #555; font-size: 15px;")
        self._logo_pixmap = QtGui.QPixmap(str(LOGO)) if LOGO.is_file() else None
        self._show_logo_text()
        self.stack.addWidget(self.logo)
        layout.addWidget(self.stack, 1)
        self.result = None
        if os.environ.get("LEGOLIZE_NO_3D") or QtWidgets.QApplication.platformName() == "offscreen":
            return                                    # VTK's widget needs a real window (save_png still renders)
        try:
            from vtkmodules.qt.QVTKRenderWindowInteractor import QVTKRenderWindowInteractor
            from vtkmodules.vtkInteractionStyle import vtkInteractorStyleTrackballCamera
            from vtkmodules.vtkRenderingCore import vtkRenderer
            import vtkmodules.vtkRenderingOpenGL2  # noqa: F401
            from lego_gui.scene import Scene

            self.widget = QVTKRenderWindowInteractor(self.stack)
            self.stack.addWidget(self.widget)
            window = self.widget.GetRenderWindow()
            window.SetAlphaBitPlanes(1)
            window.SetMultiSamples(0)
            self.renderer = vtkRenderer()
            window.AddRenderer(self.renderer)
            self.scene = Scene(self.renderer)
            self.interactor = window.GetInteractor()
            self.interactor.SetInteractorStyle(vtkInteractorStyleTrackballCamera())
            self.interactor.Initialize()
            self.vtk_ok = True
        except Exception as exc:                                  # no OpenGL: the window still works without 3D
            self.logo.setText(f"3D view unavailable: {exc}")

    def _show_logo_text(self):
        if self._logo_pixmap is not None and not self._logo_pixmap.isNull():
            side = max(160, min(self.stack.width(), self.stack.height()) - 80)
            self.logo.setPixmap(self._logo_pixmap.scaled(side, side, QtCore.Qt.KeepAspectRatio,
                                                         QtCore.Qt.SmoothTransformation))
        else:
            self.logo.setText("Legolize it\n\nOpen an STL or STEP file (or drop it here)")

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.stack.currentIndex() == 0:
            self._show_logo_text()

    # ------------------------------------------------------------------------------------------------ drawing
    @property
    def colour_mode(self) -> str:
        return self.colour.currentData()

    @property
    def colour_options(self) -> dict:
        """The colour choice as keyword arguments of LegoResult.colours: mode, fade, single."""
        return {"mode": self.colour_mode, "fade": self.fade.currentData(), "single": self.single.currentData()}

    def set_colour_options(self, fade: Optional[str] = None, single: Optional[str] = None):
        for box, value in ((self.fade, fade), (self.single, single)):
            if value is not None and box.findData(value) >= 0:
                box.blockSignals(True)
                box.setCurrentIndex(box.findData(value))
                box.blockSignals(False)

    # ------------------------------------------------------------------------------------------------ resolution
    def set_resolution(self, studs: Optional[float], text: str = ""):
        """Show the size (studs along the longest side; None for a scale factor) without emitting a change."""
        for w in (self.resolution, self.res_spin):
            w.blockSignals(True)
        if studs is not None:
            self.resolution.setValue(int(round(min(max(studs, RESOLUTION[0]), RESOLUTION[1]))))
            self.res_spin.setValue(int(round(studs)))
        for w in (self.resolution, self.res_spin):
            w.blockSignals(False)
        self.res_label.setText(text)

    def _resolution_moved(self, v: int):
        self.res_spin.blockSignals(True)
        self.res_spin.setValue(v)
        self.res_spin.blockSignals(False)
        self.resolutionChanged.emit(float(v))

    def _resolution_typed(self, v: int):
        self.resolution.blockSignals(True)
        self.resolution.setValue(min(max(v, RESOLUTION[0]), RESOLUTION[1]))
        self.resolution.blockSignals(False)
        self.resolutionChanged.emit(float(v))

    def set_colour_mode(self, mode: str):
        i = self.colour.findData(mode)
        if i >= 0:
            self.colour.setCurrentIndex(i)

    def set_result(self, result, keep_camera: bool = False):
        self.result = result
        if not self.vtk_ok:
            if result is not None:
                self.logo.setText(f"{result.stats['bricks']} bricks (no 3D view in this session)")
            return
        if result is None:
            self.scene.clear()
            self.stack.setCurrentIndex(0)
            self._render()
            return
        first = self.scene.result is None or not keep_camera
        o = self.colour_options
        self.scene.set_result(result, o["mode"], self.mark_floating.isChecked(), fade=o["fade"], single=o["single"])
        for name, box in self.checks.items():
            self.scene.visible[name] = box.isChecked()
        layers = self.scene.layer_index
        self.slider.blockSignals(True)
        self.slider.setEnabled(bool(layers))
        self.slider.setRange(0, max(0, len(layers) - 1))
        self.slider.setValue(len(layers) - 1)
        self.slider.blockSignals(False)
        self._limit(self.slider.value())
        self.stack.setCurrentIndex(1)
        if first:
            self.scene.view("iso")
        self._render()

    def redraw(self):
        if self.vtk_ok and self.scene.result is not None:
            limit = self.slider.value()
            self.set_result(self.scene.result, keep_camera=True)
            self.slider.setValue(limit)

    def _colour_changed(self, _i):
        self.fade.setVisible(self.colour_mode == "fade")
        self.single.setVisible(self.colour_mode == "single")
        self.colourModeChanged.emit(self.colour_mode)
        self.redraw()

    def _toggle(self, name: str, on: bool):
        if self.vtk_ok:
            self.scene.set_visible(name, on)
            self._render()

    def _limit(self, value: int):
        if not self.vtk_ok or not self.scene.layer_index:
            self.slider_label.setText("-")
            return
        k = self.scene.layer_index[min(value, len(self.scene.layer_index) - 1)]
        last = value >= len(self.scene.layer_index) - 1
        self.scene.set_limit(None if last else k)
        self.slider_label.setText(f"{value + 1} / {len(self.scene.layer_index)}")
        self._render()

    def set_view(self, view: str):
        if self.vtk_ok:
            self.scene.view(view)
            self._render()

    def _render(self):
        if self.vtk_ok:
            self.widget.GetRenderWindow().Render()

    def save_png(self, path) -> Optional[Path]:
        if not self.vtk_ok:                            # no widget: the same scene, rendered offscreen
            if self.result is None:
                return None
            from lego_gui.scene import render_png
            o = self.colour_options
            return render_png(self.result, path, colour_mode=o["mode"], fade=o["fade"], single=o["single"],
                              show_input=self.checks["input"].isChecked())
        from vtkmodules.vtkIOImage import vtkPNGWriter
        from vtkmodules.vtkRenderingCore import vtkWindowToImageFilter
        self._render()
        grab = vtkWindowToImageFilter()
        grab.SetInput(self.widget.GetRenderWindow())
        grab.ReadFrontBufferOff()
        grab.Update()
        writer = vtkPNGWriter()
        writer.SetFileName(str(path))
        writer.SetInputConnection(grab.GetOutputPort())
        writer.Write()
        return Path(path)

    def _screenshot(self):
        if self.result is None:
            return
        from lego_engine.pipeline import RESULTS
        stem = Path(str(self.result.settings.input)).stem
        RESULTS.mkdir(parents=True, exist_ok=True)
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Save picture", str(RESULTS / f"{stem}.png"),
                                                        "PNG (*.png)")
        if path:
            self.save_png(path)
