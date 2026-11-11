"""python3 -m lego_gui [part.stl|part.step] [--studs N] [--unit plate] [--run] [--window maximized]

    --screenshot PNG   headless check: run the part, save the window's 3D view, quit (with QT_QPA_PLATFORM=offscreen
                       the window is never shown)
"""
import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main():
    ap = argparse.ArgumentParser(prog="legolize-it", description="Legolize it: parts into bricks")
    ap.add_argument("input", nargs="?", help="a part to open (.stl, .step/.stp, ...)")
    ap.add_argument("--studs", type=float, default=None, help="studs along the longest horizontal side")
    ap.add_argument("--scale", type=float, default=None, help="scale factor instead of --studs")
    ap.add_argument("--unit", choices=("brick", "plate"), default=None)
    ap.add_argument("--up", default=None, help="the part's up axis: z, y, x, -z, -y, -x")
    ap.add_argument("--colours", default=None, help="size, layer, mixed, source, single")
    ap.add_argument("--run", action="store_true", help="legolize the part at once")
    ap.add_argument("--window", choices=("normal", "maximized", "full"), default="normal")
    ap.add_argument("--screenshot", default=None, metavar="PNG", help="run, save the 3D view, quit")
    args = ap.parse_args()
    if os.environ.get("DISPLAY") and not os.environ.get("QT_QPA_PLATFORM"):
        os.environ["QT_QPA_PLATFORM"] = "xcb"                 # VTK's OpenGL widget is reliable on X11 / XWayland
    from dataclasses import replace
    from PyQt5 import QtCore, QtGui, QtWidgets
    from lego_gui.app import MainWindow
    from lego_gui.viewer import LOGO
    app = QtWidgets.QApplication(sys.argv)
    app.setApplicationName("Legolize it")
    app.setDesktopFileName("legolize-it")
    if LOGO.is_file():
        app.setWindowIcon(QtGui.QIcon(str(LOGO)))
    win = MainWindow()
    panel = win.settings_panel
    s = panel.settings()
    if args.input:
        s = replace(s, input=str(Path(args.input).resolve()))
    if args.scale is not None:
        s = replace(s, size_mode="scale", size=args.scale)
    elif args.studs is not None:
        s = replace(s, size_mode="studs", size=args.studs)
    if args.unit:
        s = replace(s, unit=args.unit)
    if args.up:
        s = replace(s, up=args.up)
    panel.set_settings(s)
    if args.colours:
        win.view.set_colour_mode(args.colours)
    {"maximized": win.showMaximized, "full": win.showFullScreen}.get(args.window, win.show)()
    if args.screenshot:
        def shoot(_result):
            QtCore.QTimer.singleShot(300, lambda: (win.view.save_png(args.screenshot), app.quit()))
        win.worker.done.connect(shoot)
        win.worker.failed.connect(lambda t: (print(t, file=sys.stderr), app.exit(1)))
        QtCore.QTimer.singleShot(200, win.run)
    elif args.run and args.input:
        QtCore.QTimer.singleShot(200, win.run)
    code = app.exec_()
    win.thread.quit()
    win.thread.wait(3000)
    sys.exit(code)


if __name__ == "__main__":
    main()
