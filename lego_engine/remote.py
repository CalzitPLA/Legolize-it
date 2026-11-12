"""Runs on servers over SSH: the part goes to a server, Legolize it runs there, the results come back.

    legolize-it server list                       the hosts of ~/.ssh/config and the servers set up here
    legolize-it server add HOST PATH [--python P] set up a server: where Legolize it lives on it (a checkout, or the
                                                  folder `server sync` fills) and its Python (default python3)
    legolize-it server sync HOST                  copy this Legolize it (lego_engine, lego_gui) to the server's PATH
    legolize-it server check HOST                 can it run? (Python, numpy, trimesh, igl, manifold3d; FreeCAD, pdflatex)
    legolize-it server remove HOST
    legolize-it cli part.step --server HOST ...   any command line run, on the server (the window: Export > Run on)

What is kept where - and what is not kept at all:
    - hosts are ssh's own aliases (the `Host` lines of ~/.ssh/config, followed through `Include`; wildcard patterns
      left out). Only the alias is read and used - ssh itself resolves the host name, user, port and keys, so none of
      them is read, copied or written by Legolize it;
    - the servers set up here (alias, path, python) live in ~/.config/legolize-it/servers.json ($XDG_CONFIG_HOME,
      or $LEGOLIZE_CONFIG_DIR), readable by the user only - never in the repository or in results;
    - a run works in a fresh `mktemp -d` folder on the server, removed afterwards; the files that come back are
      rewritten so they name the local input and the local folder, not the server's (result.json, manifest.json, the
      colour macro, the FreeCAD document's FileName, README/CSV/TeX); the alias is in no result;
    - ssh runs with BatchMode (keys or an agent; no password prompts that would hang) and a connect timeout. Files go
      both ways as tar streams through ssh (no scp, no remote shell globbing); what comes back is unpacked with the
      paths checked (nothing outside the results folder).
"""
from __future__ import annotations

import argparse
import glob
import io
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import time
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional

SSH = os.environ.get("LEGOLIZE_SSH", "ssh")          # tests put a stand-in here
SSH_OPTS = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=15", "-o", "ServerAliveInterval=30"]
ALIAS_RE = re.compile(r"^[A-Za-z0-9_.@:%+\[\]-]+$")
ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------------------------- hosts and servers


def ssh_config_hosts(config: Optional[Path] = None, _depth: int = 0) -> List[str]:
    """The concrete host aliases of an ssh config (default ~/.ssh/config), in order, following Include; patterns
    (*, ?, !) and the Match blocks' conditions are left out. Only the aliases are read."""
    config = Path(config) if config else Path.home() / ".ssh" / "config"
    out: List[str] = []
    if _depth > 5 or not config.is_file():
        return out
    try:
        lines = config.read_text(errors="replace").splitlines()
    except OSError:
        return out
    for line in lines:
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        parts = re.split(r"[\s=]+", line, maxsplit=1)
        key, value = parts[0].lower(), (parts[1] if len(parts) > 1 else "")
        if key == "include":
            for pat in shlex.split(value):
                pat = os.path.expanduser(pat)
                if not os.path.isabs(pat):
                    pat = str(Path.home() / ".ssh" / pat)
                for inc in sorted(glob.glob(pat)):
                    out += ssh_config_hosts(Path(inc), _depth + 1)
        elif key == "host":
            for alias in shlex.split(value):
                if not any(ch in alias for ch in "*?!") and ALIAS_RE.match(alias):
                    out.append(alias)
    seen = set()
    return [a for a in out if not (a in seen or seen.add(a))]


def config_dir() -> Path:
    if os.environ.get("LEGOLIZE_CONFIG_DIR"):
        return Path(os.environ["LEGOLIZE_CONFIG_DIR"])
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "legolize-it"


def servers_file() -> Path:
    return config_dir() / "servers.json"


@dataclass
class Server:
    alias: str                    # an ssh alias (ssh resolves the rest)
    path: str = "~/legolize_it"   # where Legolize it lives on the server (lego_engine/ in it)
    python: str = "python3"       # the server's Python (a venv's python, for example)


def check_alias(alias: str) -> str:
    alias = str(alias).strip()
    if not alias or alias.startswith("-") or not ALIAS_RE.match(alias):
        raise ValueError(f"not a usable ssh host alias: {alias!r}")
    return alias


def load_servers() -> Dict[str, Server]:
    try:
        data = json.loads(servers_file().read_text())
    except (OSError, ValueError):
        return {}
    out = {}
    for alias, d in (data.get("servers") or {}).items():
        try:
            out[check_alias(alias)] = Server(alias, str(d.get("path", "~/legolize_it")), str(d.get("python", "python3")))
        except ValueError:
            continue
    return out


def save_servers(servers: Dict[str, Server]) -> Path:
    path = servers_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"servers": {a: {"path": s.path, "python": s.python} for a, s in servers.items()}},
                              indent=1))
    os.chmod(tmp, 0o600)
    tmp.replace(path)
    return path


def add_server(alias: str, path: str = "~/legolize_it", python: str = "python3") -> Server:
    alias = check_alias(alias)
    if not str(path).strip() or "\n" in path or "\n" in python:
        raise ValueError("the server's path must be one line")
    servers = load_servers()
    servers[alias] = Server(alias, str(path).strip(), str(python).strip() or "python3")
    save_servers(servers)
    return servers[alias]


def remove_server(alias: str) -> bool:
    servers = load_servers()
    if servers.pop(alias, None) is None:
        return False
    save_servers(servers)
    return True


def known_hosts(config: Optional[Path] = None) -> List[str]:
    """The servers set up here first, then the other hosts of the ssh config."""
    configured = list(load_servers())
    return configured + [h for h in ssh_config_hosts(config) if h not in configured]


# ---------------------------------------------------------------------------------------------- ssh plumbing


def remote_path(path: str) -> str:
    """A path for the server's shell: quoted, with a leading ~/ kept for the server's home."""
    path = str(path)
    if path == "~":
        return '"$HOME"'
    if path.startswith("~/"):
        return '"$HOME"/' + shlex.quote(path[2:])
    return shlex.quote(path)


def ssh(alias: str, command: str, stdin: Optional[bytes] = None, timeout: float = 120,
        capture: bool = True) -> subprocess.CompletedProcess:
    """Run `command` (a shell line, quoted by the caller) on `alias`."""
    return subprocess.run([SSH, *SSH_OPTS, check_alias(alias), command], input=stdin, capture_output=capture,
                          timeout=timeout)


def _err(proc) -> str:
    text = (proc.stderr or b"").decode(errors="replace").strip().splitlines()
    return text[-1] if text else f"exit code {proc.returncode}"


def _tar(paths: Dict[str, Path]) -> bytes:
    """{name in the archive: local file or folder} -> tar bytes (no __pycache__)."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        for name, path in paths.items():
            tf.add(str(path), arcname=name, filter=lambda ti: None if "__pycache__" in ti.name else ti)
    return buf.getvalue()


def _untar(data: bytes, dest: Path) -> List[Path]:
    """Unpack tar bytes into `dest`: regular files and folders only, every path inside `dest`."""
    dest = dest.resolve()
    dest.mkdir(parents=True, exist_ok=True)
    out = []
    with tarfile.open(fileobj=io.BytesIO(data), mode="r") as tf:
        for m in tf.getmembers():
            target = (dest / m.name).resolve()
            if dest != target and dest not in target.parents:
                raise RuntimeError(f"refusing a path outside the results folder: {m.name!r}")
            if m.isdir():
                target.mkdir(parents=True, exist_ok=True)
            elif m.isfile():
                target.parent.mkdir(parents=True, exist_ok=True)
                with tf.extractfile(m) as src, open(target, "wb") as fh:
                    shutil.copyfileobj(src, fh)
                out.append(target)
    return out


def sync_code(alias: str, path: Optional[str] = None) -> str:
    """Copy this Legolize it's packages (lego_engine, lego_gui) into the server's path."""
    server = load_servers().get(alias) or Server(alias)
    path = path or server.path
    data = _tar({"lego_engine": ROOT / "lego_engine", "lego_gui": ROOT / "lego_gui"})
    dst = remote_path(path)
    proc = ssh(alias, f"mkdir -p {dst} && tar xf - -C {dst}", stdin=data, timeout=300)
    if proc.returncode != 0:
        raise RuntimeError(f"copying to the server failed: {_err(proc)}")
    return path


def check(alias: str) -> Dict:
    """Can the server run Legolize it? {"ok", "python", "packages": {...}, "freecad", "pdflatex", "message"}."""
    server = load_servers().get(alias) or Server(alias)
    probe = ("import importlib, json, sys\n"
             "out = {'python': sys.version.split()[0], 'packages': {}}\n"
             "for m in ('numpy', 'scipy', 'trimesh', 'igl', 'manifold3d', 'lego_engine', 'vtkmodules'):\n"
             "    try:\n        importlib.import_module(m); out['packages'][m] = True\n"
             "    except Exception:\n        out['packages'][m] = False\n"
             "print('LEGOLIZE_CHECK ' + json.dumps(out))\n")
    cmd = (f"cd {remote_path(server.path)} 2>/dev/null || {{ echo 'LEGOLIZE_NOPATH'; exit 3; }}; "
           f"{remote_path(server.python)} -c {shlex.quote(probe)}; "
           "command -v freecadcmd >/dev/null && echo LEGOLIZE_FREECAD; "
           "command -v pdflatex >/dev/null && echo LEGOLIZE_PDFLATEX")
    try:
        proc = ssh(alias, cmd, timeout=60)
    except subprocess.TimeoutExpired:
        return {"ok": False, "message": "no answer (timeout)"}
    out = (proc.stdout or b"").decode(errors="replace")
    if "LEGOLIZE_NOPATH" in out:
        return {"ok": False, "message": f"the path {server.path} is not on the server (server sync copies it)"}
    line = [x for x in out.splitlines() if x.startswith("LEGOLIZE_CHECK ")]
    if not line:
        return {"ok": False, "message": f"no Python answer: {_err(proc)}"}
    info = json.loads(line[0].split(" ", 1)[1])
    need = ("numpy", "scipy", "trimesh", "igl", "manifold3d", "lego_engine")
    missing = [m for m in need if not info["packages"].get(m)]
    info.update(ok=not missing, freecad="LEGOLIZE_FREECAD" in out, pdflatex="LEGOLIZE_PDFLATEX" in out,
                message="ready" if not missing else "missing: " + ", ".join(missing))
    return info


# ---------------------------------------------------------------------------------------------- a run


TEXT_SUFFIXES = {".json", ".txt", ".csv", ".tex", ".fcmacro", ".log", ".aux", ".out"}


def _scrub(out_dir: Path, replace: Dict[str, str]):
    """Name the local files in what came back: every server path in the text files and in the FreeCAD documents'
    Document.xml replaced (longest first)."""
    pairs = sorted(((a, b) for a, b in replace.items() if a), key=lambda ab: -len(ab[0]))

    def fix(text: str) -> str:
        for a, b in pairs:
            text = text.replace(a, b)
        return text

    for f in out_dir.rglob("*"):
        if not f.is_file():
            continue
        if f.suffix.lower() in TEXT_SUFFIXES:
            text = f.read_text(errors="replace")
            new = fix(text)
            if new != text:
                f.write_text(new)
        elif f.suffix == ".FCStd":
            with zipfile.ZipFile(f) as z:
                entries = [(i, z.read(i.filename)) for i in z.infolist()]
            if not any(i.filename == "Document.xml" for i, _ in entries):
                continue
            tmp = f.with_suffix(".tmp")
            with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:       # same entries, same order
                for info, data in entries:
                    if info.filename == "Document.xml":
                        data = fix(data.decode("utf-8", "replace")).encode()
                    z.writestr(info, data)
            tmp.replace(f)


def run_cli(alias: str, input_path, cli_args: List[str], out_dir, progress: Optional[Callable[[str], None]] = None,
            timeout: float = 4 * 3600) -> Dict:
    """`python3 -m lego_engine <input> <cli_args>` on the server; the results into `out_dir` (local). `cli_args`
    without the input, --out and --server. Returns the run's summary with local paths."""
    say = progress or (lambda _t: None)
    server = load_servers().get(alias)
    if server is None:
        raise RuntimeError(f"{alias} is not set up as a server (legolize-it server add {alias} PATH)")
    src = Path(input_path).resolve()
    out_dir = Path(out_dir).resolve()
    if out_dir.exists() and any(out_dir.iterdir()):
        raise RuntimeError(f"the results folder is not empty: {out_dir}")
    bad = [a for a in cli_args if a in ("--out", "--server") or a.startswith(("--out=", "--server="))]
    if bad:
        raise ValueError(f"leave {bad} out of the server's arguments")
    say(f"server {alias}: a work folder ...")
    proc = ssh(alias, 'mktemp -d "${TMPDIR:-/tmp}/legolize.XXXXXX"', timeout=60)
    tmp = (proc.stdout or b"").decode().strip()
    if proc.returncode != 0 or not re.match(r"^/[^\s'\"]*/legolize\.[A-Za-z0-9]+$", tmp):
        raise RuntimeError(f"no work folder on the server: {_err(proc)}")
    qtmp = shlex.quote(tmp)
    try:
        say(f"server {alias}: sending {src.name} ...")
        proc = ssh(alias, f"tar xf - -C {qtmp}", stdin=_tar({src.name: src}), timeout=600)
        if proc.returncode != 0:
            raise RuntimeError(f"sending the part failed: {_err(proc)}")
        remote_in, remote_out = f"{tmp}/{src.name}", f"{tmp}/out"
        cmd = (f"cd {remote_path(server.path)} && {remote_path(server.python)} -m lego_engine "
               f"{shlex.quote(remote_in)} {' '.join(shlex.quote(a) for a in cli_args)} --out {shlex.quote(remote_out)}")
        say(f"server {alias}: legolizing ...")
        t = time.time()
        p = subprocess.Popen([SSH, *SSH_OPTS, alias, cmd], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        lines = []
        for raw in p.stderr:                                       # the run's progress, as it goes
            line = raw.decode(errors="replace").rstrip()
            lines.append(line)
            if line and not line.startswith("picture of layer"):
                say(f"[{alias}] " + line.replace(tmp, "<work>"))
        stdout = p.stdout.read().decode(errors="replace")
        code = p.wait(timeout=timeout)
        if code not in (0, 2):
            raise RuntimeError(f"the run on the server failed (exit {code}): " + "\n".join(lines[-12:]).replace(tmp, "<work>"))
        say(f"server {alias}: done in {time.time() - t:.1f} s, fetching the results ...")
        proc = ssh(alias, f"tar cf - -C {shlex.quote(remote_out)} .", timeout=1800)
        if proc.returncode != 0:
            raise RuntimeError(f"fetching the results failed: {_err(proc)}")
        files = _untar(proc.stdout, out_dir)
    finally:
        ssh(alias, f"rm -rf {qtmp}", timeout=60)
    _scrub(out_dir, {remote_out: str(out_dir), remote_in: str(src), tmp: str(out_dir)})
    try:
        summary = json.loads(stdout.replace(remote_out, str(out_dir)).replace(remote_in, str(src)))
    except ValueError:
        summary = {}
    summary["server_files"] = len(files)
    summary["result"] = str(out_dir / "result.json")
    say(f"results from {alias}: {out_dir} ({len(files)} files)")
    return summary


# ---------------------------------------------------------------------------------------------- command line


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="legolize-it server", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="what", required=True)
    sub.add_parser("list", help="the hosts of ~/.ssh/config and the servers set up here")
    a = sub.add_parser("add", help="set up a server: an ssh alias and where Legolize it lives on it")
    a.add_argument("host")
    a.add_argument("path", nargs="?", default="~/legolize_it")
    a.add_argument("--python", default="python3", help="the server's Python (default python3)")
    for name, text in (("remove", "forget a server"), ("check", "can the server run Legolize it?"),
                       ("sync", "copy this Legolize it to the server's path")):
        sub.add_parser(name, help=text).add_argument("host")
    args = ap.parse_args(argv)
    if args.what == "list":
        servers = load_servers()
        for h in known_hosts():
            s = servers.get(h)
            print(f"{h:24s} " + (f"{s.path}  ({s.python})" if s else "-  (legolize-it server add HOST PATH)"))
        print(f"settings: {servers_file()}", file=sys.stderr)
        return 0
    if args.what == "add":
        s = add_server(args.host, args.path, args.python)
        print(f"{s.alias}: {s.path} ({s.python}) - saved in {servers_file()}")
        return 0
    if args.what == "remove":
        return 0 if remove_server(args.host) else 1
    if args.what == "sync":
        print(f"{args.host}: copied to {sync_code(args.host)}")
        return 0
    info = check(args.host)
    print(json.dumps(info, indent=1))
    return 0 if info.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
