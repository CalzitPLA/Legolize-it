"""Background work: loading, voxelizing and the exports take seconds (FreeCAD minutes for big models), so they run in
a worker thread; the GUI thread only draws finished results. The worker owns the Legolizer and its caches."""
from __future__ import annotations

import traceback

from PyQt5 import QtCore


class Worker(QtCore.QObject):
    done = QtCore.pyqtSignal(object)              # LegoResult
    exported = QtCore.pyqtSignal(str, object)     # "stl" | "fcstd" | "manual", info dict
    failed = QtCore.pyqtSignal(str)
    message = QtCore.pyqtSignal(str)
    busy = QtCore.pyqtSignal(bool)

    def __init__(self):
        super().__init__()
        from lego_engine.pipeline import Legolizer
        self.legolizer = Legolizer(self.message.emit)

    def _guard(self, fn):
        self.busy.emit(True)
        try:
            fn()
        except Exception as exc:
            self.failed.emit(f"{exc}\n\n{traceback.format_exc()}")
        finally:
            self.busy.emit(False)

    @QtCore.pyqtSlot(object)
    def run(self, settings):
        self._guard(lambda: self.done.emit(self.legolizer.run(settings)))

    @QtCore.pyqtSlot(object, str, str, bool)
    def export_stl(self, result, out_dir, mode, hollow):
        from lego_engine.export_stl import write_stl_stack

        def go():
            info = write_stl_stack(result, out_dir, mode, hollow_bricks=hollow, progress=self.message.emit)
            info["dir"] = out_dir
            self.exported.emit("stl", info)
        self._guard(go)

    @QtCore.pyqtSlot(object, str, bool, bool, object)
    def export_fcstd(self, result, path, include_input, hollow, colours):
        from lego_engine.export_freecad import write_fcstd
        self._guard(lambda: self.exported.emit("fcstd", write_fcstd(result, path, include_input, hollow, colours,
                                                                      progress=self.message.emit)))

    @QtCore.pyqtSlot(object, str, object, object, bool, bool)
    def export_manual(self, result, out_dir, colours, images, pdf, quiet=False):
        from lego_engine.export_manual import write_manual

        def go():
            info = write_manual(result, out_dir, colours, pdf=pdf, progress=self.message.emit, images=images,
                                pictures=False)
            info["dir"] = out_dir
            info["quiet"] = quiet
            self.exported.emit("manual", info)
        self._guard(go)

    @QtCore.pyqtSlot(str, str, object, str)
    def export_remote(self, alias, input_path, args, out_dir):
        from lego_engine.remote import run_cli

        def go():
            info = run_cli(alias, input_path, list(args), out_dir, progress=self.message.emit)
            info["dir"] = out_dir
            info["server"] = alias
            self.exported.emit("remote", info)
        self._guard(go)
