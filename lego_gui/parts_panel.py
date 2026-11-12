"""The parts dock: the numbers of the result, the parts list (size, count, colour) and the layers."""
from __future__ import annotations

from PyQt5 import QtCore, QtGui, QtWidgets

from lego_engine.palette import size_colours
from lego_engine.system import name_area


def _swatch(rgb) -> QtGui.QIcon:
    pix = QtGui.QPixmap(14, 14)
    pix.fill(QtGui.QColor.fromRgbF(*[float(c) for c in rgb]))
    return QtGui.QIcon(pix)


class PartsPanel(QtWidgets.QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QtWidgets.QVBoxLayout(self)
        self.summary = QtWidgets.QLabel("No result yet.")
        self.summary.setWordWrap(True)
        self.summary.setTextFormat(QtCore.Qt.RichText)
        layout.addWidget(self.summary)
        layout.addWidget(QtWidgets.QLabel("<b>Parts list</b>"))
        self.parts = QtWidgets.QTableWidget(0, 3)
        self.parts.setHorizontalHeaderLabels(["Part", "Count", "Cells"])
        self._tidy(self.parts)
        layout.addWidget(self.parts, 2)
        layout.addWidget(QtWidgets.QLabel("<b>Layers</b>"))
        self.layers = QtWidgets.QTableWidget(0, 4)
        self.layers.setHorizontalHeaderLabels(["Layer", "z (mm)", "Bricks", "Along"])
        self._tidy(self.layers)
        layout.addWidget(self.layers, 3)

    @staticmethod
    def _tidy(table: QtWidgets.QTableWidget):
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.Stretch)

    def set_result(self, result, colour_mode: str = "auto"):
        if result is None:
            self.summary.setText("No result yet.")
            self.parts.setRowCount(0)
            self.layers.setRowCount(0)
            return
        s = result.stats
        state = ("<span style='color:#1a7f37'>holds together</span>" if s["stable"] else
                 f"<span style='color:#b3261e'>{s['groups']} separate groups, {s['floating']} floating bricks</span>")
        t = result.timings
        pieces = "".join(f", <b>{s[k]}</b> {k}" for k in ("shaped", "special") if s.get(k))
        self.summary.setText(
            f"<b>{s['bricks']}</b> parts{pieces}, <b>{s['layers']}</b> layers ({result.settings.unit}s)<br>"
            f"{s['size_mm'][0]:g} x {s['size_mm'][1]:g} x {s['size_mm'][2]:g} mm, grid {' x '.join(map(str, s['grid']))}"
            f"<br>scale {result.placement.scale:.4g}, {s['studs_visible']} visible studs<br>"
            + (f"deviation from the part: {100 * s['deviation']:.1f} % of its volume<br>" if "deviation" in s else "")
            + f"{state}<br>"
            f"<span style='color:#666'>voxelize {t.get('voxelize_s', 0):.2f} s, layout {t.get('layout_s', 0):.2f} s"
            f"</span>")
        swatches = size_colours(result.model)
        colour_mode = result.colour_mode(colour_mode)
        self.parts.setRowCount(len(s["counts"]))
        for r, (name, count) in enumerate(s["counts"].items()):
            item = QtWidgets.QTableWidgetItem(name)
            if colour_mode == "size":
                item.setIcon(_swatch(swatches[name]))
            self.parts.setItem(r, 0, item)
            self.parts.setItem(r, 1, QtWidgets.QTableWidgetItem(str(count)))
            self.parts.setItem(r, 2, QtWidgets.QTableWidgetItem(str(name_area(name) * count)))
        rows = s["per_layer"]
        self.layers.setRowCount(len(rows))
        for r, row in enumerate(rows):
            for c, v in enumerate((row["layer"], f"{row['z_mm']:g}", row["bricks"], row["axis"])):
                self.layers.setItem(r, c, QtWidgets.QTableWidgetItem(str(v)))
