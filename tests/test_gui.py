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
    s = LegoSettings(input="/tmp/x.stl", up="y", size=24, unit="plate", threshold=0.35, shell=1, thin_walls=True,
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
