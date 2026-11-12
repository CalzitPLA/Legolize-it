"""The servers dialog: the hosts of ~/.ssh/config (their aliases only) and the servers set up here - pick a host, give
the path where Legolize it lives on it (and its Python), check it, copy this Legolize it there. Kept in
~/.config/legolize-it/servers.json (lego_engine.remote), never in the repository."""
from __future__ import annotations

from PyQt5 import QtCore, QtWidgets

from lego_engine import remote


class ServersDialog(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Servers (ssh)")
        self.resize(720, 420)
        layout = QtWidgets.QVBoxLayout(self)
        note = QtWidgets.QLabel("Hosts come from your ssh config (only their aliases are read; ssh resolves the rest "
                                "and needs a key or an agent). A server needs the path where Legolize it lives on it - "
                                "'Copy Legolize it there' puts this version there.")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.table = QtWidgets.QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Host", "Path", "Python", "Status"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.Stretch)
        self.table.itemSelectionChanged.connect(self._picked)
        layout.addWidget(self.table, 1)
        form = QtWidgets.QFormLayout()
        self.host = QtWidgets.QComboBox()
        self.host.setEditable(True)
        self.path = QtWidgets.QLineEdit("~/legolize_it")
        self.path.setToolTip("on the server: the folder with lego_engine/ in it (a checkout, or what 'Copy' fills)")
        self.python = QtWidgets.QLineEdit("python3")
        self.python.setToolTip("the server's Python, e.g. ~/.local/share/legolize-it/venv/bin/python")
        form.addRow("Host", self.host)
        form.addRow("Path on the server", self.path)
        form.addRow("Python on the server", self.python)
        layout.addLayout(form)
        row = QtWidgets.QHBoxLayout()
        for text, slot in (("Save", self.save), ("Remove", self.remove), ("Check", self.check),
                           ("Copy Legolize it there", self.sync)):
            b = QtWidgets.QPushButton(text)
            b.clicked.connect(slot)
            row.addWidget(b)
        row.addStretch(1)
        close = QtWidgets.QPushButton("Close")
        close.clicked.connect(self.accept)
        row.addWidget(close)
        layout.addLayout(row)
        self.status = {}
        self.refresh()

    def refresh(self):
        servers = remote.load_servers()
        hosts = remote.known_hosts()
        current = self.host.currentText()
        self.host.clear()
        self.host.addItems(hosts)
        if current:
            self.host.setCurrentText(current)
        self.table.setRowCount(len(hosts))
        for r, h in enumerate(hosts):
            s = servers.get(h)
            for c, text in enumerate((h, s.path if s else "", s.python if s else "",
                                      self.status.get(h, "set up" if s else "-"))):
                self.table.setItem(r, c, QtWidgets.QTableWidgetItem(text))

    def _picked(self):
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return
        h = self.table.item(rows[0].row(), 0).text()
        s = remote.load_servers().get(h)
        self.host.setCurrentText(h)
        if s:
            self.path.setText(s.path)
            self.python.setText(s.python)

    def _alias(self) -> str:
        return self.host.currentText().strip()

    def _busy(self, fn, what: str):
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            return fn()
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "Servers", f"{what}: {exc}")
            return None
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()

    def save(self):
        if self._busy(lambda: remote.add_server(self._alias(), self.path.text(), self.python.text()), "saving"):
            self.refresh()

    def remove(self):
        remote.remove_server(self._alias())
        self.status.pop(self._alias(), None)
        self.refresh()

    def check(self):
        self.save()
        info = self._busy(lambda: remote.check(self._alias()), "checking")
        if info is not None:
            extra = ", ".join(n for n, k in (("FreeCAD", "freecad"), ("pdflatex", "pdflatex")) if info.get(k))
            self.status[self._alias()] = info["message"] + (f" (+ {extra})" if extra and info.get("ok") else "")
            self.refresh()

    def sync(self):
        self.save()
        done = self._busy(lambda: remote.sync_code(self._alias()), "copying")
        if done:
            self.status[self._alias()] = f"copied to {done}"
            self.refresh()
