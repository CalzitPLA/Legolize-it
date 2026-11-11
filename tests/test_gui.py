"""The window's parts on Qt's offscreen platform (no 3D widget there: the view falls back to text and to offscreen
pictures)."""
import os

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
                     sizes=((1, 1), (1, 2), (2, 4)), max_length=6, bridge_weight=2.0)
    panel.set_settings(s)
    got = panel.settings()
    for key in ("input", "up", "size", "unit", "threshold", "shell", "thin_walls", "max_length", "bridge_weight"):
        assert getattr(got, key) == getattr(s, key), key
    assert set(got.sizes) == set(s.sizes)


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
