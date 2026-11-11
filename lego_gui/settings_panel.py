"""The settings dock: the input, its size on the grid, the bricks, the brick system and the export options.

`settings()` returns a lego_engine.pipeline.LegoSettings. `changed(level)` says what a change costs: "layout" (threshold,
shell, catalogue, colours - redone at once), "voxelize" (size, unit, up axis, sampling, system) or "load" (the file).
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from PyQt5 import QtCore, QtWidgets

from lego_engine.mesh_io import UP_AXES
from lego_engine.pipeline import LegoSettings
from lego_engine.system import DEFAULT_SIZES, BrickSystem

ALL_SIZES = ((1, 1), (1, 2), (1, 3), (1, 4), (1, 6), (1, 8), (1, 10), (1, 12),
             (2, 2), (2, 3), (2, 4), (2, 6), (2, 8), (2, 10), (2, 12), (4, 4), (4, 6), (4, 8))
FILE_FILTER = "Parts (*.stl *.STL *.step *.STEP *.stp *.STP *.obj *.ply *.off *.3mf *.glb);;All files (*)"


class SettingsPanel(QtWidgets.QWidget):
    changed = QtCore.pyqtSignal(str)
    legolize = QtCore.pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        d = LegoSettings()
        s = d.system
        root = QtWidgets.QVBoxLayout(self)

        g = QtWidgets.QGroupBox("Input")
        f = QtWidgets.QFormLayout(g)
        row = QtWidgets.QHBoxLayout()
        self.path = QtWidgets.QLineEdit()
        self.path.setPlaceholderText("an STL or STEP file")
        self.path.editingFinished.connect(lambda: self.changed.emit("load"))
        browse = QtWidgets.QToolButton()
        browse.setText("...")
        browse.clicked.connect(self.browse)
        row.addWidget(self.path, 1)
        row.addWidget(browse)
        f.addRow("File", row)
        self.up = QtWidgets.QComboBox()
        for a in UP_AXES:
            self.up.addItem(f"{a} is up", a)
        self.up.currentIndexChanged.connect(lambda _: self.changed.emit("voxelize"))
        f.addRow("Up axis", self.up)
        self.deflection = self._dspin(0.005, 2.0, d.deflection, 3, 0.01, "load", "STEP: largest distance of the "
                                      "triangles from the surface (mm)")
        f.addRow("STEP deflection", self.deflection)
        root.addWidget(g)

        g = QtWidgets.QGroupBox("Size")
        f = QtWidgets.QFormLayout(g)
        self.size_mode = QtWidgets.QComboBox()
        self.size_mode.addItem("studs along the longest side", "studs")
        self.size_mode.addItem("scale factor (1 = mm)", "scale")
        self.size_mode.currentIndexChanged.connect(self._size_mode_changed)
        f.addRow("Size by", self.size_mode)
        self.size = self._dspin(0.01, 1000, d.size, 2, 1.0, "voxelize", "studs along the longest side, or "
                                "the scale factor")
        f.addRow("Size", self.size)
        self.unit = QtWidgets.QComboBox()
        self.unit.addItem("brick (9.6 mm)", "brick")
        self.unit.addItem("plate (3.2 mm)", "plate")
        self.unit.currentIndexChanged.connect(lambda _: self.changed.emit("voxelize"))
        f.addRow("Layer", self.unit)
        self.samples = QtWidgets.QSpinBox()
        self.samples.setRange(1, 6)
        self.samples.setValue(d.samples)
        self.samples.setToolTip("sub-samples per cell and axis: 3 measures every cell at 27 points")
        self.samples.valueChanged.connect(lambda _: self.changed.emit("voxelize"))
        f.addRow("Samples", self.samples)
        root.addWidget(g)

        g = QtWidgets.QGroupBox("Bricks")
        f = QtWidgets.QFormLayout(g)
        row = QtWidgets.QHBoxLayout()
        self.threshold = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.threshold.setRange(1, 99)
        self.threshold.setValue(int(round(d.threshold * 100)))
        self.threshold.setToolTip("how much of a cell must be material for a brick there")
        self.threshold_label = QtWidgets.QLabel(f"{d.threshold:.2f}")
        self.threshold.valueChanged.connect(self._threshold_changed)
        row.addWidget(self.threshold, 1)
        row.addWidget(self.threshold_label)
        f.addRow("Fill threshold", row)
        self.shell = QtWidgets.QSpinBox()
        self.shell.setRange(0, 20)
        self.shell.setSpecialValueText("solid")
        self.shell.setToolTip("hollow: keep only a shell this many cells thick (saves bricks)")
        self.shell.valueChanged.connect(lambda _: self.changed.emit("layout"))
        f.addRow("Shell", self.shell)
        self.thin = QtWidgets.QComboBox()
        self.thin.addItem("auto (where too thin for a cell)", "auto")
        self.thin.addItem("every cell the surface passes", "all")
        self.thin.addItem("off", "off")
        self.thin.setToolTip("sheet metal and profiles fill too little of any cell for the threshold: 'auto' adds the "
                             "surface's cells where no cell nearby is filled, 'all' adds every cell the surface passes")
        self.thin.currentIndexChanged.connect(lambda _i: self.changed.emit("layout"))
        f.addRow("Thin walls", self.thin)
        self.max_length = QtWidgets.QSpinBox()
        self.max_length.setRange(1, 12)
        self.max_length.setValue(d.max_length)
        self.max_length.valueChanged.connect(lambda _: self.changed.emit("layout"))
        f.addRow("Longest brick", self.max_length)
        self.bridge = self._dspin(0.0, 5.0, d.bridge_weight, 2, 0.25, "layout", "how strongly a brick should span "
                                  "the seams of the layer below (interlocking)")
        f.addRow("Interlock", self.bridge)
        grid = QtWidgets.QGridLayout()
        self.size_checks = {}
        for n, (a, b) in enumerate(ALL_SIZES):
            box = QtWidgets.QCheckBox(f"{a}x{b}")
            box.setChecked((a, b) in DEFAULT_SIZES)
            if (a, b) == (1, 1):
                box.setEnabled(False)                  # always there: every cell can be covered
                box.setChecked(True)
            box.toggled.connect(lambda _v: self.changed.emit("layout"))
            self.size_checks[(a, b)] = box
            grid.addWidget(box, n // 4, n % 4)
        f.addRow("Catalogue", grid)
        self.snap = QtWidgets.QCheckBox("snap input colours to brick colours")
        self.snap.setChecked(d.snap_colours)
        self.snap.toggled.connect(lambda _v: self.changed.emit("layout"))
        f.addRow("", self.snap)
        root.addWidget(g)

        self.system_box = QtWidgets.QGroupBox("Brick system (mm)")
        self.system_box.setCheckable(True)
        self.system_box.setChecked(False)
        f = QtWidgets.QFormLayout(self.system_box)
        self.sys_fields = {}
        for name, label, lo, hi, step in (("pitch", "Stud pitch", 1, 50, 0.1), ("plate_height", "Plate height", 0.5, 20, 0.1),
                                          ("stud_diameter", "Stud diameter", 0.5, 20, 0.05),
                                          ("stud_height", "Stud height", 0.1, 10, 0.05),
                                          ("clearance", "Clearance per side", 0, 1, 0.01),
                                          ("socket_clearance", "Socket play (print)", 0, 1, 0.01)):
            spin = self._dspin(lo, hi, getattr(s, name), 3, step, "voxelize" if name in ("pitch", "plate_height")
                               else "layout")
            self.sys_fields[name] = spin
            f.addRow(label, spin)
        self.system_box.toggled.connect(self._system_toggled)
        self._system_toggled(False)
        root.addWidget(self.system_box)

        g = QtWidgets.QGroupBox("Export")
        f = QtWidgets.QFormLayout(g)
        self.stl_mode = QtWidgets.QComboBox()
        self.stl_mode.addItem("layer slabs (plug together)", "layers")
        self.stl_mode.addItem("single bricks + parts list", "bricks")
        self.stl_mode.addItem("both", "both")
        f.addRow("STL stack", self.stl_mode)
        self.hollow_bricks = QtWidgets.QCheckBox("hollow underside (tubes) for single bricks")
        self.hollow_bricks.setChecked(True)
        f.addRow("", self.hollow_bricks)
        self.fc_input = QtWidgets.QCheckBox("include the input in the FreeCAD document")
        self.fc_input.setChecked(True)
        f.addRow("", self.fc_input)
        self.fc_hollow = QtWidgets.QCheckBox("hollow bricks in FreeCAD (slower)")
        f.addRow("", self.fc_hollow)
        root.addWidget(g)

        self.live = QtWidgets.QCheckBox("Update live")
        self.live.setChecked(True)
        self.live.setToolTip("redo the bricks at once when a setting changes (after the first run)")
        root.addWidget(self.live)
        self.go = QtWidgets.QPushButton("Legolize it")
        self.go.setMinimumHeight(34)
        self.go.setStyleSheet("QPushButton { font-weight: bold; }")
        self.go.clicked.connect(self.legolize.emit)
        root.addWidget(self.go)
        root.addStretch(1)

    def _dspin(self, lo, hi, value, decimals, step, level, tip=""):
        spin = QtWidgets.QDoubleSpinBox()
        spin.setRange(lo, hi)
        spin.setDecimals(decimals)
        spin.setSingleStep(step)
        spin.setValue(value)
        spin.setKeyboardTracking(False)
        if tip:
            spin.setToolTip(tip)
        spin.valueChanged.connect(lambda _v: self.changed.emit(level))
        return spin

    def _size_mode_changed(self, _i):
        studs = self.size_mode.currentData() == "studs"
        self.size.blockSignals(True)
        self.size.setValue(16.0 if studs else 1.0)
        self.size.setDecimals(1 if studs else 3)
        self.size.setSingleStep(1.0 if studs else 0.1)
        self.size.blockSignals(False)
        self.changed.emit("voxelize")

    def _threshold_changed(self, v):
        self.threshold_label.setText(f"{v / 100:.2f}")
        self.changed.emit("layout")

    def _system_toggled(self, on):
        for w in self.sys_fields.values():
            w.setVisible(on)
            label = self.system_box.layout().labelForField(w)
            if label is not None:
                label.setVisible(on)

    def browse(self):
        start = str(Path(self.path.text()).parent) if self.path.text() else str(Path.home())
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Open a part", start, FILE_FILTER)
        if path:
            self.set_input(path)

    def set_input(self, path: str):
        self.path.setText(str(Path(path).resolve()))
        self.changed.emit("load")

    def set_settings(self, s: LegoSettings):
        """Show `s` in the widgets (the command line's options)."""
        for w in self.findChildren(QtWidgets.QWidget):
            w.blockSignals(True)
        try:
            if s.input:
                self.path.setText(s.input)
            self.up.setCurrentIndex(self.up.findData(s.up))
            self.size_mode.setCurrentIndex(self.size_mode.findData(s.size_mode))
            self.size.setValue(s.size)
            self.unit.setCurrentIndex(self.unit.findData(s.unit))
            self.samples.setValue(s.samples)
            self.threshold.setValue(int(round(s.threshold * 100)))
            self.threshold_label.setText(f"{s.threshold:.2f}")
            self.shell.setValue(s.shell)
            self.thin.setCurrentIndex(max(0, self.thin.findData(s.thin_walls)))
            self.max_length.setValue(s.max_length)
            self.bridge.setValue(s.bridge_weight)
            self.snap.setChecked(s.snap_colours)
            for k, box in self.size_checks.items():
                box.setChecked(k in s.sizes or k == (1, 1))
        finally:
            for w in self.findChildren(QtWidgets.QWidget):
                w.blockSignals(False)

    def settings(self, colour_mode: str = "size") -> LegoSettings:
        system = replace(BrickSystem(), **{k: float(w.value()) for k, w in self.sys_fields.items()})
        return LegoSettings(
            input=self.path.text().strip(), up=self.up.currentData(), size_mode=self.size_mode.currentData(),
            size=float(self.size.value()), unit=self.unit.currentData(), threshold=self.threshold.value() / 100.0,
            samples=int(self.samples.value()), shell=int(self.shell.value()), thin_walls=self.thin.currentData(),
            sizes=tuple(k for k, box in self.size_checks.items() if box.isChecked()),
            max_length=int(self.max_length.value()), bridge_weight=float(self.bridge.value()),
            colour_mode=colour_mode, snap_colours=self.snap.isChecked(),
            deflection=float(self.deflection.value()), system=system)
