"""The window's parts on Qt's offscreen platform (no 3D widget there: the view falls back to text and to offscreen
pictures)."""
import os
from pathlib import Path

import pytest

pytest.importorskip("PyQt5")
from PyQt5 import QtWidgets  # noqa: E402

from lego_engine import LegoSettings, Legolizer  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def test_settings_panel_round_trip(app):
    from lego_gui.settings_panel import SettingsPanel
    panel = SettingsPanel()
    s = LegoSettings(input="/tmp/x.stl", up="y", size=24, unit="plate", threshold=0.35, shell=1, thin_walls="all",
                     sizes=((1, 1), (1, 2), (2, 4)), max_length=6, bridge_weight=2.0, shapes=("curve", "dome"),
                     special=True, special_tolerance=0.2)
    panel.set_settings(s)
    got = panel.settings()
    for key in ("input", "up", "size", "unit", "threshold", "shell", "thin_walls", "max_length", "bridge_weight",
                "shapes", "special", "special_tolerance"):
        assert getattr(got, key) == getattr(s, key), key
    assert set(got.sizes) == set(s.sizes)


def test_the_resolution_slider_sets_the_size(app):
    from lego_gui.app import MainWindow
    win = MainWindow()
    try:
        win.settings_panel.size_mode.setCurrentIndex(win.settings_panel.size_mode.findData("scale"))
        win.view.resolution.setValue(40)                    # the slider under the view
        s = win._settings()
        assert (s.size_mode, s.size) == ("studs", 40.0)
        win.settings_panel.size.setValue(24.0)               # and back: the settings move the slider
        assert win.view.resolution.value() == 24 and win.view.res_spin.value() == 24
        win.view.colour.setCurrentIndex(win.view.colour.findData("fade"))
        assert win.view.fade.isVisibleTo(win.view) and win._settings().colour_mode == "fade"
    finally:
        win.close()


def test_window_shows_a_result(app, parts, tmp_path):
    from lego_gui.app import MainWindow
    win = MainWindow()
    try:
        r = Legolizer().run(LegoSettings(input=str(parts["L"]), size_mode="scale", size=1.0))
        win._done(r)
        assert win.parts.parts.rowCount() == len(r.stats["counts"])
        assert win.parts.layers.rowCount() == r.stats["layers"]
        assert win.a_stl.isEnabled() and win.a_fc.isEnabled()
        if os.environ.get("DISPLAY"):                       # the offscreen picture needs an OpenGL context
            png = win.view.save_png(tmp_path / "view.png")
            assert png is not None and png.stat().st_size > 1000
    finally:
        win.close()


def _wait(app, signal, timeout_ms=120000):
    """Run the event loop until `signal` fires (the worker thread's answer); its arguments."""
    from PyQt5 import QtCore
    got, loop = [], QtCore.QEventLoop()
    signal.connect(lambda *a: (got.append(a), loop.quit()))
    QtCore.QTimer.singleShot(timeout_ms, loop.quit)
    loop.exec_()
    return got[0] if got else None


def test_window_exports_through_its_worker(app, parts, tmp_path, monkeypatch):
    """The toolbar's path without its file dialogs: run, STL stack and FreeCAD document in the worker thread."""
    from lego_engine.mesh_io import find_freecadcmd
    from lego_gui.app import MainWindow
    win = MainWindow()
    try:
        win.settings_panel.set_settings(LegoSettings(input=str(parts["L"]), size_mode="scale", size=1.0))
        win.requestRun.emit(win.settings_panel.settings())
        assert _wait(app, win.worker.done) is not None and win.result.stats["bricks"] == 3
        win.requestStl.emit(win.result, str(tmp_path / "stl"), "both", True)
        kind, info = _wait(app, win.worker.exported)
        assert kind == "stl" and win.last_stl == str(tmp_path / "stl") and (tmp_path / "stl" / "assembly.stl").is_file()
        if find_freecadcmd():
            monkeypatch.setattr(QtWidgets.QMessageBox, "exec_", lambda self: 0)   # "open in FreeCAD?": later
            win.requestFcstd.emit(win.result, str(tmp_path / "L.FCStd"), True, False, win.result.colours())
            kind, info = _wait(app, win.worker.exported)
            assert kind == "fcstd" and (tmp_path / "L.FCStd").is_file() and win.a_open_fc.isEnabled()
    finally:
        win.close()


def test_an_export_clicked_during_a_run_is_not_dropped_and_brings_its_manual(app, parts, tmp_path, monkeypatch):
    """2026-11-12: "no manual gets exported" - an export clicked while a live update ran did nothing, and the STL and
    FreeCAD exports wrote no manual. Now the click waits for the run, and the manual comes with the export."""
    from lego_gui.app import MainWindow
    win = MainWindow()
    try:
        win._done(Legolizer().run(LegoSettings(input=str(parts["L"]), size_mode="scale", size=1.0)))
        win._busy = True                                     # as if a live update were running
        monkeypatch.setattr(QtWidgets.QFileDialog, "getExistingDirectory", lambda *a, **k: str(tmp_path))
        got = []
        from PyQt5 import QtCore
        loop = QtCore.QEventLoop()
        win.worker.exported.connect(lambda k, i: (got.append((k, i)), len(got) == 2 and loop.quit()))
        win.worker.failed.connect(lambda t: (got.append(("failed", t)), loop.quit()))
        win.export_stl()
        QtCore.QTimer.singleShot(120000, loop.quit)
        loop.exec_()
        kinds = [k for k, _ in got]
        assert kinds == ["stl", "manual"], got
        manual = dict(got)["manual"]
        assert manual["quiet"] and (tmp_path / Path(manual["dir"]).relative_to(tmp_path) / "manual.tex").is_file()
        assert Path(manual["dir"]).parent == Path(dict(got)["stl"]["dir"])
    finally:
        win.close()


def test_the_export_manual_button(app, parts, tmp_path, monkeypatch):
    """The settings' Export group has an "Export manual" button (the toolbar's can hide behind its overflow menu)."""
    from PyQt5 import QtCore
    from lego_gui.app import MainWindow
    win = MainWindow()
    try:
        button = win.settings_panel.export_buttons["manual"]
        assert button.text() == "Export manual" and not button.isEnabled()
        assert win.a_manual.text() == "Export manual ..."
        win._done(Legolizer().run(LegoSettings(input=str(parts["L"]), size_mode="scale", size=1.0)))
        assert button.isEnabled()
        monkeypatch.setattr(QtWidgets.QFileDialog, "getExistingDirectory", lambda *a, **k: str(tmp_path))
        monkeypatch.setattr(QtWidgets.QMessageBox, "exec_", lambda self: 0)
        got, loop = [], QtCore.QEventLoop()
        win.worker.exported.connect(lambda k, i: (got.append((k, i)), loop.quit()))
        win.worker.failed.connect(lambda t: (got.append(("failed", t)), loop.quit()))
        button.click()
        QtCore.QTimer.singleShot(120000, loop.quit)
        loop.exec_()
        assert got and got[0][0] == "manual", got
        assert (Path(got[0][1]["dir"]) / "manual.tex").is_file()
    finally:
        win.close()
