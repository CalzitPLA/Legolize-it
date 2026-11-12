"""The Legolize it window: settings on the left, the 3D view in the middle (input translucent, bricks opaque), the parts
list on the right, the log at the bottom; Open / Legolize / Export STL stack / Export FreeCAD / Export manual / Open in
FreeCAD in the toolbar (the exports also as buttons in the settings' Export group). Parts can be dropped onto the
window.

After the first run, changing a setting redoes the bricks by itself ("Update live", after a short pause): the layout
settings at once, the size and the voxelization from the cached part. The resolution slider under the view is the
size in studs along the longest side (the settings' "Size", kept in step).
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
    requestManual = QtCore.pyqtSignal(object, str, object, object, bool, bool)
    requestRemote = QtCore.pyqtSignal(str, str, object, str)

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
        scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        scroll.setMinimumWidth(max(410, self.settings_panel.minimumSizeHint().width()          # all of it, no
                                   + scroll.verticalScrollBar().sizeHint().width() + 6))      # sideways scrolling
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
        self.a_manual = tb.addAction(style.standardIcon(QtWidgets.QStyle.SP_FileDialogDetailedView), "Export manual ...",
                                     self.export_manual)
        self.a_manual.setShortcut("Ctrl+M")
        self.a_manual.setToolTip("the building manual: LaTeX and PDF, one step per layer (Ctrl+M)")
        self.a_open_fc = tb.addAction(style.standardIcon(QtWidgets.QStyle.SP_ComputerIcon), "Open in FreeCAD", self.open_freecad)
        self.a_folder = tb.addAction(style.standardIcon(QtWidgets.QStyle.SP_DirOpenIcon), "Results folder", self.open_folder)
        for a in (self.a_stl, self.a_fc, self.a_open_fc, self.a_manual):
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
        self.requestManual.connect(self.worker.export_manual)
        self.requestRemote.connect(self.worker.export_remote)
        self.worker.done.connect(self._done)
        self.worker.exported.connect(self._exported)
        self.worker.failed.connect(self._failed)
        self.worker.message.connect(self.say)
        self.worker.busy.connect(self._set_busy)
        self.thread.start()

        self.settings_panel.legolize.connect(self.run)
        self.settings_panel.export.connect(lambda kind: {"stl": self.export_stl, "fcstd": self.export_fcstd,
                                                         "manual": self.export_manual}[kind]())
        self.settings_panel.changed.connect(self._settings_changed)
        self.view.colourModeChanged.connect(self._colour_mode_changed)
        self.view.resolutionChanged.connect(self.settings_panel.set_studs)
        self.settings_panel.size.valueChanged.connect(lambda _v: self._sync_resolution())
        self.settings_panel.size_mode.currentIndexChanged.connect(lambda _i: self._sync_resolution())
        self._debounce = QtCore.QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(350)
        self._debounce.timeout.connect(self.run)
        self.say("Legolize it: open an STL or STEP file (or drop it here), then Legolize it (F5).")

    def _colour_mode_changed(self, mode: str):
        """Colours from the input also shape the layout (each visible brick one colour): switching to or from that
        mode lays the bricks again; the other modes only repaint."""
        self.parts.set_result(self.result, mode)
        r = self.result
        if r is not None and r.source_colours is not None and (r.colour_mode(mode) == "source") != \
                (r.colour_mode() == "source"):
            self._debounce.start()

    def _settings(self):
        o = self.view.colour_options
        return self.settings_panel.settings(o["mode"], o["fade"], o["single"])

    def _sync_resolution(self, result=None):
        """The view's resolution row from the settings' size (and the numbers of the last result)."""
        p = self.settings_panel
        studs = float(p.size.value()) if p.size_mode.currentData() == "studs" else None
        text = "" if studs is not None else f"(scale factor {p.size.value():g})"
        r = result or self.result
        if r is not None:
            s = r.stats
            text = (f"{s['size_mm'][0]:g} x {s['size_mm'][1]:g} x {s['size_mm'][2]:g} mm, {s['bricks']} parts" +
                    ("" if studs is not None else f", scale {r.placement.scale:.3g}"))
        self.view.set_resolution(studs, text)

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
        s = self._settings()
        if not s.input:
            self.settings_panel.browse()
            s = self._settings()
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
        self._sync_resolution(result)
        for a in (self.a_stl, self.a_fc, self.a_manual):
            a.setEnabled(bool(result.model.bricks))           # nothing to export without bricks
        self.settings_panel.set_exports_enabled(bool(result.model.bricks))
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

    def _ready_to_export(self) -> bool:
        """An export is never dropped: while the worker is busy (a live update) it waits in the worker's queue and
        runs next, with the model the view shows. (Until 2026-11-12 a click during a run did nothing, silently.)"""
        if self.result is None:
            self.say("nothing to export yet: Legolize it (F5) first")
            return False
        if self._busy:
            self.say("busy with a run: the export starts when it is done")
        return True

    def export_stl(self):
        if not self._ready_to_export():
            return
        start = self._default_dir() / time.strftime("stl_%Y%m%d-%H%M%S")
        start.parent.mkdir(parents=True, exist_ok=True)
        out = QtWidgets.QFileDialog.getExistingDirectory(self, "Directory for the STL stack (a new folder is made in it)",
                                                         str(start.parent))
        if not out:
            return
        target = Path(out) / start.name
        p = self.settings_panel
        if self._remote(["--stl", p.stl_mode.currentData()] + ([] if p.hollow_bricks.isChecked() else
                                                               ["--no-hollow-bricks"]) + self._manual_args(), target):
            return
        self.requestStl.emit(self.result, str(target), p.stl_mode.currentData(), p.hollow_bricks.isChecked())
        if p.with_manual.isChecked():
            self._manual_request(target / "manual", quiet=True)

    def _manual_args(self) -> list:
        p = self.settings_panel
        if not p.with_manual.isChecked():
            return []
        return ["--manual"] + ([] if p.manual_pdf.isChecked() else ["--no-pdf"])

    def _remote(self, export_args, target: Path) -> bool:
        """With a server chosen under Export: run there with the window's settings and these exports; the results come
        back into `target`. False: run here."""
        alias = self.settings_panel.run_on.currentData()
        if not alias:
            return False
        from lego_engine.cli import settings_args
        s = self._settings()
        self.say(f"running on the server {alias} ...")
        self.requestRemote.emit(alias, s.input, settings_args(s) + list(export_args) + ["--quiet"], str(target))
        return True

    def export_fcstd(self):
        if not self._ready_to_export():
            return
        stem = Path(str(self.result.settings.input)).stem
        d = self._default_dir()
        d.mkdir(parents=True, exist_ok=True)
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "FreeCAD document", str(d / f"{stem}_bricks.FCStd"),
                                                        "FreeCAD (*.FCStd)")
        if not path:
            return
        p = self.settings_panel
        if self._remote(["--fcstd"] + ([] if p.fc_input.isChecked() else ["--no-input"]) +
                        ([] if p.fc_hollow.isChecked() else ["--solid-fc"]) + self._manual_args(),
                        Path(path).parent / time.strftime(f"{Path(path).stem}_server_%Y%m%d-%H%M%S")):
            return
        self.requestFcstd.emit(self.result, path, p.fc_input.isChecked(), p.fc_hollow.isChecked(),
                               self.result.colours(**self.view.colour_options))
        if p.with_manual.isChecked():
            self._manual_request(Path(path).parent / f"{Path(path).stem}_manual", quiet=True)

    def export_manual(self):
        if not self._ready_to_export():
            return
        start = self._default_dir() / time.strftime("manual_%Y%m%d-%H%M%S")
        start.parent.mkdir(parents=True, exist_ok=True)
        out = QtWidgets.QFileDialog.getExistingDirectory(self, "Directory for the building manual (a new folder is made "
                                                         "in it)", str(start.parent))
        if not out:
            return
        target = Path(out) / start.name
        if self._remote(["--manual"] + ([] if self.settings_panel.manual_pdf.isChecked() else ["--no-pdf"]), target):
            return
        self._manual_request(target)

    def _manual_request(self, target: Path, quiet: bool = False):
        """The building manual of the model on view into `target`: the pictures here (VTK in the GUI thread), the
        LaTeX and the PDF in the worker. `quiet`: part of another export - a line in the log, no dialog."""
        colours = self.result.colours(**self.view.colour_options)
        images = {}
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:                                       # VTK draws in this thread (OpenGL next to the 3D view's)
            from lego_gui.scene import render_steps
            self.say("building manual: pictures of the steps ...")
            QtWidgets.QApplication.processEvents()
            images = render_steps(self.result, target / "img", colours=colours)
        except Exception as exc:
            self.say(f"building manual without pictures: {str(exc).splitlines()[0][:160]}")
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()
        self.requestManual.emit(self.result, str(target), colours, images, self.settings_panel.manual_pdf.isChecked(),
                                quiet)

    def _exported(self, kind: str, info: dict):
        if kind == "stl":
            self.last_stl = info["dir"]
            files = info.get("files", {})
            n = len(files.get("layers", [])) + len(files.get("bricks", []))
            self.say(f"STL stack: {n} files in {info['dir']} (see README.txt there)")
        elif kind == "remote":
            self.last_stl = info["dir"]
            if info.get("fcstd"):
                self.last_fcstd = {"fcstd": info["fcstd"], "macro": info.get("macro")}
                self.a_open_fc.setEnabled(True)
            made = [k for k in ("stl", "fcstd", "manual") if info.get(k)]
            self.say(f"server {info['server']}: {', '.join(made) or 'result'} in {info['dir']}")
            QtWidgets.QMessageBox.information(self, "Legolize it", f"From the server {info['server']}:\n"
                                              + "\n".join(f"{k}: {info[k]}" for k in made) + f"\n\n{info['dir']}")
        elif kind == "manual":
            self.last_stl = info["dir"]
            doc = info.get("pdf") or info["tex"]
            self.say(f"building manual: {doc} ({info['steps']} steps)")
            if info.get("quiet"):
                return
            box = QtWidgets.QMessageBox(self)
            box.setWindowTitle("Legolize it")
            box.setText(f"Building manual written ({info['steps']} steps):\n{doc}")
            view = box.addButton("Open it", QtWidgets.QMessageBox.AcceptRole)
            box.addButton("Later", QtWidgets.QMessageBox.RejectRole)
            box.exec_()
            if box.clickedButton() is view:
                subprocess.Popen(["xdg-open", doc], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            self.last_fcstd = info
            self.a_open_fc.setEnabled(True)
            st = info.get("stacking") or {}
            fit = (f"; stacking check: {st['pairs']} pairs, overlap {st['max_overlap_mm3']:g} mm3, stud gap "
                   f"{st['min_stud_gap_mm']}..{st['max_stud_gap_mm']} mm") if st else ""
            self.say(f"FreeCAD: {info['fcstd']} ({info['bricks']} parts, {info['seconds']} s, coloured){fit}")
            box = QtWidgets.QMessageBox(self)
            box.setWindowTitle("Legolize it")
            box.setText(f"FreeCAD document written (coloured):\n{info['fcstd']}{fit.replace('; ', chr(10) * 2)}\n\n"
                        "Open it in FreeCAD now?")
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
            open_in_freecad(self.last_fcstd["fcstd"])
            self.say("FreeCAD starts with the document (its colours are inside; the macro next to it sets them again)")
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
        self.worker.legolizer.release()
        super().closeEvent(event)
