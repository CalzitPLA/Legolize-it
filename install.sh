#!/usr/bin/env bash
# Legolize it - installer (Linux): a Python environment with the packages, the command `legolize-it`, a desktop entry
# and the mushroom icon.
#   ./install.sh                 install (environment: ~/.local/share/legolize-it/venv, using the system's packages)
#   ./install.sh --check         check the installation: packages, FreeCAD, one part into bricks with both exports
#   ./install.sh --uninstall     remove the command, the desktop entry, the icons and the environment
#   ./install.sh --venv DIR      the environment somewhere else
#   ./install.sh --no-system     an environment without the system's site packages (everything from PyPI)
#   PYTHON=/path/to/python3 ./install.sh    another Python (3.10 or newer)
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
VENV="$HOME/.local/share/legolize-it/venv"
BIN="$HOME/.local/bin"
APPS="$HOME/.local/share/applications"
ICONS="$HOME/.local/share/icons/hicolor"
PY=${PYTHON:-python3}
SYSTEM=1
MODE=install
while [ $# -gt 0 ]; do
  case "$1" in
    --check) MODE=check ;;
    --uninstall) MODE=uninstall ;;
    --venv) VENV=$2; shift ;;
    --no-system) SYSTEM=0 ;;
    -h|--help) sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option $1 (see --help)" >&2; exit 2 ;;
  esac
  shift
done
say()  { printf '\033[1m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33mwarning:\033[0m %s\n' "$*" >&2; }

find_freecad() {      # the freecadcmd Legolize it will use (lego_engine/mesh_io.py looks the same way)
  for c in "${LEGOLIZE_FREECADCMD:-}" "$(command -v freecadcmd 2>/dev/null)" "$(command -v FreeCADCmd 2>/dev/null)" \
           /usr/bin/freecadcmd; do
    [ -n "$c" ] && [ -x "$c" ] && { echo "$c"; return 0; }
  done
  return 1
}

check() {
  local py="$VENV/bin/python" ok=1
  [ -x "$py" ] || { echo "no environment at $VENV - run ./install.sh first" >&2; exit 1; }
  say "packages"
  "$py" - <<'PYEOF' || ok=0
import importlib, sys
need = ["numpy", "scipy", "trimesh", "igl", "manifold3d"]
gui = ["PyQt5", "vtk", "PIL"]
bad = []
for m in need + gui:
    try:
        mod = importlib.import_module(m)
        print(f"  ok       {m} {getattr(mod, '__version__', '')}")
    except Exception as e:
        print(f"  MISSING  {m} ({e})")
        bad.append(m)
sys.exit(1 if bad else 0)
PYEOF
  say "FreeCAD (STEP input, FreeCAD export)"
  if fc=$(find_freecad); then
    echo "  $fc: $("$fc" --version 2>/dev/null | head -1)"
  else
    warn "no freecadcmd: STL input and the STL stack work; STEP input and the FreeCAD export need FreeCAD"
  fi
  say "one part into bricks (examples/golden_teacher.ply, 12 studs) with the STL stack$([ -n "${fc:-}" ] && echo ' and the FreeCAD document')"
  local tmp; tmp=$(mktemp -d)
  local args=("$ROOT/examples/golden_teacher.ply" --studs 12 --unit plate --colours source --out "$tmp" --stl layers --quiet)
  [ -n "${fc:-}" ] && args+=(--fcstd)
  if PYTHONPATH="$ROOT" "$py" -m lego_engine "${args[@]}" > "$tmp/summary.json" 2> "$tmp/log.txt"; then
    "$py" - "$tmp/summary.json" <<'PYEOF' || ok=0
import json, sys
s = json.load(open(sys.argv[1]))
print(f"  {s['bricks']} bricks in {s['layers']} layers, holds together: {s['stable']}" +
      (f", FreeCAD: {s['fcstd']}" if "fcstd" in s else ""))
sys.exit(0 if s["bricks"] > 0 else 1)
PYEOF
  else
    warn "the run failed - the log: $tmp/log.txt"; ok=0
  fi
  [ $ok = 1 ] && say "the installation works (output in $tmp)" || { warn "the installation is incomplete (see above)"; exit 1; }
}

if [ $MODE = uninstall ]; then
  say "removing the command, the desktop entry, the icons and $VENV"
  rm -f "$BIN/legolize-it" "$APPS/legolize-it.desktop"
  for px in 32 64 128 256 512; do rm -f "$ICONS/${px}x${px}/apps/legolize-it.png"; done
  case "$VENV" in "$HOME"/*) rm -rf "$VENV"; rmdir "$(dirname "$VENV")" 2>/dev/null || true ;; esac
  command -v update-desktop-database >/dev/null && update-desktop-database "$APPS" 2>/dev/null || true
  exit 0
fi
if [ $MODE = check ]; then check; exit 0; fi

say "Python"
"$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' \
  || { echo "Python 3.10 or newer is needed ($("$PY" --version 2>&1)); set PYTHON=/path/to/python3.x" >&2; exit 1; }
echo "  $("$PY" --version) at $(command -v "$PY")"

say "the environment: $VENV"
mkdir -p "$(dirname "$VENV")"
args=(); [ $SYSTEM = 1 ] && args+=(--system-site-packages)
[ -x "$VENV/bin/python" ] || "$PY" -m venv "${args[@]}" "$VENV"
say "packages (requirements.txt, requirements-gui.txt)"
"$VENV/bin/python" -m pip install --quiet -r "$ROOT/requirements.txt" -r "$ROOT/requirements-gui.txt"

if FC=$(find_freecad); then
  say "FreeCAD: $FC"
else
  warn "no freecadcmd found: STEP input and the FreeCAD export need FreeCAD (Debian/Ubuntu: sudo apt install freecad)"
fi

say "the command: $BIN/legolize-it"
mkdir -p "$BIN"
cat > "$BIN/legolize-it" <<LAUNCHER
#!/usr/bin/env bash
# Legolize it, installed by $ROOT/install.sh
#   legolize-it [part.stl|part.step] [--studs N] [--unit plate] [--run]   the window (legolize-it --help-window)
#   legolize-it cli part.step --studs 24 --stl layers --fcstd --out DIR   the command line (legolize-it cli --help)
#   legolize-it check                                                     check this installation
#   legolize-it server list|add HOST PATH|check HOST|sync HOST            runs on servers over ssh
ROOT="$ROOT"
PY="$VENV/bin/python"
export PYTHONPATH="\$ROOT\${PYTHONPATH:+:\$PYTHONPATH}"
export RESOURCE_NAME=legolize-it      # the window's WM_CLASS instance = the desktop file's name (the dock's icon)
case "\${1:-}" in
  cli) shift; exec "\$PY" -m lego_engine "\$@" ;;
  check) exec "\$ROOT/install.sh" --check --venv "$VENV" ;;
  server) shift; exec "\$PY" -m lego_engine.remote "\$@" ;;
  --help-window) exec "\$PY" -m lego_gui --help ;;
  -h|--help) sed -n '3,6p' "\$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
esac
exec "\$PY" -m lego_gui "\$@"
LAUNCHER
chmod +x "$BIN/legolize-it"

say "the desktop entry and the icon"
mkdir -p "$APPS"
for px in 32 64 128 256 512; do
  mkdir -p "$ICONS/${px}x${px}/apps"
  cp "$ROOT/packaging/icons/legolize-it_${px}.png" "$ICONS/${px}x${px}/apps/legolize-it.png"
done
cat > "$APPS/legolize-it.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=Legolize it
GenericName=Parts into bricks
Comment=Voxelize an STL/STEP part into stud-and-tube bricks; export to FreeCAD and as an STL stack for 3D printing
Exec=$BIN/legolize-it %f
Icon=legolize-it
Terminal=false
Categories=Graphics;3DGraphics;Engineering;
MimeType=model/stl;model/step;application/sla;
Keywords=brick;voxel;STL;STEP;FreeCAD;3D printing;
StartupNotify=true
StartupWMClass=Legolize it
DESKTOP
command -v update-desktop-database >/dev/null && update-desktop-database "$APPS" 2>/dev/null || true
command -v gtk-update-icon-cache >/dev/null && gtk-update-icon-cache -q -t "$ICONS" 2>/dev/null || true
case ":$PATH:" in *":$BIN:"*) ;; *) warn "$BIN is not on your PATH: add it, or start $BIN/legolize-it" ;; esac
say "done: legolize-it (or 'Legolize it' in the applications menu); legolize-it check tests the installation"
