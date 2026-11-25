"""The parts dialog: every part Legolize it can lay (lego_engine.catalogues), a checkbox each, limited to the range
chosen on top - Standard (every part, to print), LEGO or BlueBrixx. Parts outside the range are greyed out; parts in
it can be switched off one by one. The makers' websites are linked for parts, colours, prices and stock; the
trademark notice is at the bottom."""
from typing import Iterable, Set

from PyQt5 import QtCore, QtWidgets

from lego_engine.catalogues import CATALOGUES, GROUPS, NOTICE, available

ALWAYS = {"1x1"}                     # fills any cell: always there


def range_note(name: str) -> str:
    """The range's note with a link to its website (rich text)."""
    c = CATALOGUES.get(name, CATALOGUES["standard"])
    link = f' - <a href="{c.url}">{c.url.split("//")[1].split("/")[0]}</a>' if c.url else ""
    return f"{c.note}{link}"


class PartsDialog(QtWidgets.QDialog):
    def __init__(self, parent=None, parts_range: str = "standard", unit: str = "plate", off: Iterable[str] = ()):
        super().__init__(parent)
        self.setWindowTitle("Parts")
        self.resize(640, 720)
        self.unit = unit
        self.off: Set[str] = set(off)
        root = QtWidgets.QVBoxLayout(self)
        row = QtWidgets.QHBoxLayout()
        row.addWidget(QtWidgets.QLabel("Catalogue"))
        self.range = QtWidgets.QComboBox()
        for name, c in CATALOGUES.items():
            self.range.addItem(c.label, name)
        self.range.setCurrentIndex(max(0, self.range.findData(parts_range)))
        self.range.currentIndexChanged.connect(lambda _i: self._fill())
        row.addWidget(self.range, 1)
        root.addLayout(row)
        self.note = QtWidgets.QLabel()
        self.note.setWordWrap(True)
        self.note.setOpenExternalLinks(True)
        self.note.setTextFormat(QtCore.Qt.RichText)
        root.addWidget(self.note)

        makers = [n for n in CATALOGUES if n != "standard"]
        self.tree = QtWidgets.QTreeWidget()
        self.tree.setColumnCount(2 + len(makers))
        self.tree.setHeaderLabels(["Part", "id"] + [CATALOGUES[n].label for n in makers])
        self.tree.setToolTip("untick a part to leave it out; greyed out: not in the chosen range")
        self.have = {n: available(n, unit) for n in makers}
        self.items = {}
        for group, parts in GROUPS(unit):
            top = QtWidgets.QTreeWidgetItem(self.tree, [group])
            top.setFlags(top.flags() | QtCore.Qt.ItemIsUserCheckable | QtCore.Qt.ItemIsAutoTristate)
            for pid, label in parts:
                it = QtWidgets.QTreeWidgetItem(top, [label, pid] + ["x" if pid in self.have[n] else "-" for n in makers])
                it.setFlags(it.flags() | QtCore.Qt.ItemIsUserCheckable)
                for col in range(2, 2 + len(makers)):
                    it.setTextAlignment(col, QtCore.Qt.AlignCenter)
                self.items[pid] = it
        self.tree.header().setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
        self.tree.itemChanged.connect(self._changed)
        root.addWidget(self.tree, 1)

        row = QtWidgets.QHBoxLayout()
        for text, slot, tip in (("All", self._all, "every part of the range"),
                                ("None", self._none, "no part but 1x1 (it fills any cell)"),
                                ("Reset", self._reset, "every part of the range, nothing switched off by hand")):
            b = QtWidgets.QPushButton(text)
            b.setToolTip(tip)
            b.clicked.connect(slot)
            row.addWidget(b)
        row.addStretch(1)
        root.addLayout(row)
        notice = QtWidgets.QLabel(NOTICE + " " + " · ".join(f'<a href="{c.url}">{c.label}</a>'
                                                            for c in CATALOGUES.values() if c.url))
        notice.setWordWrap(True)
        notice.setOpenExternalLinks(True)
        notice.setTextFormat(QtCore.Qt.RichText)
        notice.setStyleSheet("QLabel { color: palette(mid); font-size: small; }")
        root.addWidget(notice)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        self._fill()
        self.tree.expandAll()

    def parts_range(self) -> str:
        return self.range.currentData()

    def _fill(self):
        """The checks for the chosen range: greyed out outside it, else ticked unless switched off."""
        name = self.parts_range()
        avail = available(name, self.unit)
        url = CATALOGUES[name].url
        self.note.setText(range_note(name))
        self.tree.blockSignals(True)
        try:
            for pid, it in self.items.items():
                inside = pid in avail
                on = inside and (pid in ALWAYS or pid not in self.off)
                it.setCheckState(0, QtCore.Qt.Checked if on else QtCore.Qt.Unchecked)
                it.setDisabled(not inside or pid in ALWAYS)
                it.setToolTip(0, "always there: it fills any cell" if pid in ALWAYS else
                              "" if inside else f"not in the {CATALOGUES[name].label} range"
                              + (f" - see {url}" if url else ""))
        finally:
            self.tree.blockSignals(False)
        self.tree.viewport().update()

    def _changed(self, it, col):
        pid = it.text(1)
        if col != 0 or pid not in self.items or it.isDisabled():
            return
        if it.checkState(0) == QtCore.Qt.Checked:
            self.off.discard(pid)
        else:
            self.off.add(pid)

    def _set_all(self, on: bool):
        avail = available(self.parts_range(), self.unit)
        for pid in self.items:
            if pid in avail and pid not in ALWAYS:
                (self.off.discard if on else self.off.add)(pid)
        self._fill()

    def _all(self):
        self._set_all(True)

    def _none(self):
        self._set_all(False)

    def _reset(self):
        self.off.clear()
        self._fill()

    def set_off(self, pid: str, off: bool = True):
        """Switch one part off (or on) - as a click on its box would."""
        self.items[pid].setCheckState(0, QtCore.Qt.Unchecked if off else QtCore.Qt.Checked)
