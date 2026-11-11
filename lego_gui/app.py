"""The Legolize it window: settings on the left, the 3D view in the middle (input translucent, bricks opaque), the parts
list on the right, the log at the bottom; Open / Legolize / Export STL stack / Export FreeCAD / Open in FreeCAD in the
toolbar. Parts can be dropped onto the window.

After the first run, changing a setting redoes the bricks by itself ("Update live", after a short pause): the layout
settings at once, the size and the voxelization from the cached part.
"""
from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import Optional

from PyQt5 import QtCore, QtGui, QtWidgets

from lego_engine.pipeline import RESULTS
from lego_gui.parts_panel import PartsPanel
from lego_gui.settings_panel import SettingsPanel
from lego_gui.viewer import LOGO, BrickView
from lego_gui.worker import Worker

ROOT = Path(__file__).resolve().parent.parent


class MainWindow(QtWidgets.QMainWindow):
    requestRun = QtCore.pyqtSignal(object)
    requestStl = QtCore.pyqtSignal(object, str, str, bool)
    requestFcstd = QtCore.pyqtSignal(object, str, bool, bool, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Legolize it")
        if LOGO.is_file():
            self.setWindowIcon(QtGui.QIcon(str(LOGO)))
        self.resize(1500, 950)
        self.setAcceptDrops(True)
        self.result = None
        self.last_fcstd: Optional[dict] = None
        self.last_stl: Optional[str] = None
        self._busy = False
        self._pending = False
        self._t0 = 0.0

        self.view = BrickView()
        self.setCentralWidget(self.view)
        self.settings_panel = SettingsPanel()
        scroll = QtWidgets.QScrollArea()
        scroll.setWidget(self.settings_panel)
        scroll.setWidgetResizable(True)
        scroll.setMinimumWidth(410)
        self._dock("Settings", scroll, QtCore.Qt.LeftDockWidgetArea)
        self.parts = PartsPanel()
        self._dock("Parts", self.parts, QtCore.Qt.RightDockWidgetArea).setMinimumWidth(260)
        self.log = QtWidgets.QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(5000)
        self._dock("Output", self.log, QtCore.Qt.BottomDockWidgetArea).setMaximumHeight(180)

        tb = self.addToolBar("Main")
        tb.setMovable(False)
        tb.setToolButtonStyle(QtCore.Qt.ToolButtonTextBesideIcon)
        style = self.style()
        self.a_open = tb.addAction(style.standardIcon(QtWidgets.QStyle.SP_DialogOpenButton), "Open ...", self.settings_panel.browse)
        self.a_open.setShortcut(QtGui.QKeySequence.Open)
        self.a_run = tb.addAction(style.standardIcon(QtWidgets.QStyle.SP_MediaPlay), "Legolize it", self.run)
        self.a_run.setShortcut("F5")
        tb.addSeparator()
        self.a_stl = tb.addAction(style.standardIcon(QtWidgets.QStyle.SP_DialogSaveButton), "Export STL stack ...", self.export_stl)
        self.a_fc = tb.addAction(style.standardIcon(QtWidgets.QStyle.SP_DialogSaveButton), "Export FreeCAD ...", self.export_fcstd)
        self.a_open_fc = tb.addAction(style.standardIcon(QtWidgets.QStyle.SP_ComputerIcon), "Open in FreeCAD", self.open_freecad)
        self.a_folder = tb.addAction(style.standardIcon(QtWidgets.QStyle.SP_DirOpenIcon), "Results folder", self.open_folder)
        for a in (self.a_stl, self.a_fc, self.a_open_fc):
            a.setEnabled(False)
        self.status = self.statusBar()
        self.progress = QtWidgets.QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setMaximumWidth(160)
        self.progress.hide()
        self.status.addPermanentWidget(self.progress)

        self.thread = QtCore.QThread(self)
        self.worker = Worker()
        self.worker.moveToThread(self.thread)
        self.requestRun.connect(self.worker.run)
        self.requestStl.connect(self.worker.export_stl)
        self.requestFcstd.connect(self.worker.export_fcstd)
        self.worker.done.connect(self._done)
        self.worker.exported.connect(self._exported)
        self.worker.failed.connect(self._failed)
        self.worker.message.connect(self.say)
        self.worker.busy.connect(self._set_busy)
        self.thread.start()

        self.settings_panel.legolize.connect(self.run)
        self.settings_panel.changed.connect(self._settings_changed)
        self.view.colourModeChanged.connect(lambda m: self.parts.set_result(self.result, m))
        self._debounce = QtCore.QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(350)
        self._debounce.timeout.connect(self.run)
        self.say("Legolize it: open an STL or STEP file (or drop it here), then Legolize it (F5).")

    def _dock(self, title, widget, area):
        dock = QtWidgets.QDockWidget(title, self)
        dock.setObjectName(title)
        dock.setWidget(widget)
        dock.setFeatures(QtWidgets.QDockWidget.DockWidgetMovable | QtWidgets.QDockWidget.DockWidgetFloatable)
        self.addDockWidget(area, dock)
        return dock

    # ----------------------------------------------------------------------------------------------- running
    def say(self, text: str):
        self.log.appendPlainText(f"{time.strftime('%H:%M:%S')}  {text}")
        self.status.showMessage(text.splitlines()[0][:200], 8000)

    def run(self):
        s = self.settings_panel.settings(self.view.colour_mode)
        if not s.input:
            self.settings_panel.browse()
            s = self.settings_panel.settings(self.view.colour_mode)
            if not s.input:
                return
        if not Path(s.input).is_file():
            self._failed(f"no such file: {s.input}")
            return
        if self._busy:
            self._pending = True                      # once the running job is done
            return
        self._t0 = time.time()
        self.requestRun.emit(s)

    def _settings_changed(self, level: str):
        if level == "load" and self.result is None:
            path = self.settings_panel.path.text().strip()
            if path:
                self.say(f"input: {path}")
            if path and Path(path).is_file():
                self._debounce.start()
            return
        if self.result is not None and self.settings_panel.live.isChecked():
            self._debounce.start()

    def _done(self, result):
        keep = self.result is not None and Path(str(self.result.settings.input)) == Path(str(result.settings.input))
        self.result = result
        self.view.set_result(result, keep_camera=keep)
        self.parts.set_result(result, self.view.colour_mode)
        for a in (self.a_stl, self.a_fc):
            a.setEnabled(bool(result.model.bricks))           # nothing to export without bricks
        if not result.stats["stable"]:
            self.say(f"note: {result.stats['floating']} bricks are not connected to the bottom layer (magenta) - "
                     "a lower threshold, a larger size or plates instead of bricks usually connect them")
        self.say(f"done in {time.time() - self._t0:.2f} s")

    def _failed(self, text: str):
        self.say("error: " + text.splitlines()[0])
        self.log.appendPlainText(text)
        QtWidgets.QMessageBox.warning(self, "Legolize it", text.split("\n\n")[0][:1500])

    def _set_busy(self, busy: bool):
        self._busy = busy
        self.progress.setVisible(busy)
        self.a_run.setEnabled(not busy)
        self.settings_panel.go.setEnabled(not busy)
        if not busy and self._pending:
            self._pending = False
            QtCore.QTimer.singleShot(0, self.run)

    # ----------------------------------------------------------------------------------------------- exports
    def _default_dir(self) -> Path:
        stem = Path(str(self.result.settings.input)).stem if self.result else "part"
        return RESULTS / stem

    def export_stl(self):
        if self.result is None or self._busy:
            return
        start = self._default_dir() / time.strftime("stl_%Y%m%d-%H%M%S")
        start.parent.mkdir(parents=True, exist_ok=True)
        out = QtWidgets.QFileDialog.getExistingDirectory(self, "Directory for the STL stack (a new folder is made in it)",
                                                         str(start.parent))
        if not out:
            return
        target = Path(out) / start.name
        p = self.settings_panel
        self.requestStl.emit(self.result, str(target), p.stl_mode.currentData(), p.hollow_bricks.isChecked())

    def export_fcstd(self):
        if self.result is None or self._busy:
            return
        stem = Path(str(self.result.settings.input)).stem
        d = self._default_dir()
        d.mkdir(parents=True, exist_ok=True)
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "FreeCAD document", str(d / f"{stem}_bricks.FCStd"),
                                                        "FreeCAD (*.FCStd)")
        if not path:
            return
        p = self.settings_panel
        self.requestFcstd.emit(self.result, path, p.fc_input.isChecked(), p.fc_hollow.isChecked(),
                               self.result.colours(self.view.colour_mode))

    def _exported(self, kind: str, info: dict):
        if kind == "stl":
            self.last_stl = info["dir"]
            files = info.get("files", {})
            n = len(files.get("layers", [])) + len(files.get("bricks", []))
            self.say(f"STL stack: {n} files in {info['dir']} (see README.txt there)")
        else:
            self.last_fcstd = info
            self.a_open_fc.setEnabled(True)
            self.say(f"FreeCAD: {info['fcstd']} ({info['bricks']} bricks, {info['seconds']} s); colours: {info['macro']}")
            box = QtWidgets.QMessageBox(self)
            box.setWindowTitle("Legolize it")
            box.setText(f"FreeCAD document written:\n{info['fcstd']}\n\nOpen it in FreeCAD now (with the colour macro)?")
            yes = box.addButton("Open in FreeCAD", QtWidgets.QMessageBox.AcceptRole)
            box.addButton("Later", QtWidgets.QMessageBox.RejectRole)
            box.exec_()
            if box.clickedButton() is yes:
                self.open_freecad()

    def open_freecad(self):
        if not self.last_fcstd:
            return
        from lego_engine.export_freecad import open_in_freecad
        try:
            open_in_freecad(self.last_fcstd["fcstd"], self.last_fcstd["macro"])
            self.say("FreeCAD starts with the document and its colour macro")
        except Exception as exc:
            self._failed(str(exc))

    def open_folder(self):
        target = Path(self.last_stl) if self.last_stl else (self._default_dir() if self.result else RESULTS)
        target.mkdir(parents=True, exist_ok=True)
        subprocess.Popen(["xdg-open", str(target)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # ----------------------------------------------------------------------------------------------- misc
    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        urls = [u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
        if urls:
            self.result = None                       # a new part: its first run starts from the input signal
            self.settings_panel.set_input(urls[0])

    def closeEvent(self, event):
        self.thread.quit()
        self.thread.wait(3000)
        super().closeEvent(event)
