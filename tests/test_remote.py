"""Runs on servers: the ssh config's hosts, the servers file, and a whole run through a stand-in for ssh that runs the
commands here, in a fake server home - no network, no real host (nothing of the user's ssh setup is read)."""
import json
import os
import stat
import sys
from pathlib import Path

import pytest

from lego_engine import remote
from lego_engine.cli import main as cli_main

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A private config folder and a stand-in `ssh`: `ssh [-o X]... ALIAS COMMAND` runs COMMAND with bash, its temp
    folder in a fake server home; it logs the aliases it was asked for."""
    monkeypatch.setenv("LEGOLIZE_CONFIG_DIR", str(tmp_path / "config"))
    server_home = tmp_path / "server_home"
    server_home.mkdir()
    fake = tmp_path / "fake_ssh"
    fake.write_text('#!/usr/bin/env bash\n'
                    'while [ $# -gt 0 ]; do case "$1" in -o) shift 2 ;; --) shift ;; *) break ;; esac; done\n'
                    f'echo "$1" >> {tmp_path / "ssh_calls"}\n'
                    'shift\n'
                    f'export TMPDIR={server_home}\n'
                    'exec bash -c "$*"\n')
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setattr(remote, "SSH", str(fake))
    return tmp_path


def test_hosts_come_from_the_ssh_config(tmp_path):
    cfg = tmp_path / "config"
    inc = tmp_path / "more.conf"
    inc.write_text("Host lab-c\n  HostName 10.0.0.3\n")
    cfg.write_text("# a comment\nHost lab-a lab-b\n  HostName example.invalid\n  User someone\n"
                   "Host *\n  ServerAliveInterval 30\nHost !lab-x *.inner ?x\n"
                   f"Include {inc}\nHost=lab-d\nMatch host lab-e\n")
    hosts = remote.ssh_config_hosts(cfg)
    assert hosts == ["lab-a", "lab-b", "lab-c", "lab-d"]           # aliases only: no host names, users, patterns
    assert remote.ssh_config_hosts(tmp_path / "missing") == []


def test_servers_are_kept_outside_the_repository(home):
    s = remote.add_server("lab-a", "~/legolize", "python3")
    path = remote.servers_file()
    assert path.is_file() and ROOT not in path.parents and str(home) in str(path)
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert remote.load_servers()["lab-a"] == s
    for bad in ("-oProxyCommand=x", "a b", ""):
        with pytest.raises(ValueError):
            remote.add_server(bad, "~/x")
    assert remote.remove_server("lab-a") and not remote.load_servers()


def test_remote_paths_are_quoted():
    assert remote.remote_path("~/my dir/x") == '"$HOME"/\'my dir/x\''
    assert remote.remote_path("/opt/a;rm -rf b") == "'/opt/a;rm -rf b'"


def test_a_run_on_a_server_comes_back_with_local_names(home, parts, capsys):
    remote.add_server("lab-a", str(ROOT), sys.executable)
    assert remote.check("lab-a")["ok"]
    out = home / "results"
    code = cli_main([str(parts["L"]), "--scale", "1", "--stl", "layers", "--server", "lab-a", "--out", str(out),
                     "--quiet"])
    assert code == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["bricks"] == 3 and Path(summary["stl"]).parent == out
    res = json.loads((out / "result.json").read_text())
    assert res["settings"]["input"] == str(parts["L"].resolve())
    for f in out.rglob("*"):                    # nothing names the server's work folder or the server
        if f.is_file() and f.suffix in (".json", ".txt", ".csv"):
            text = f.read_text()
            assert "legolize." not in text and "server_home" not in text and "lab-a" not in text, f
    assert not list((home / "server_home").glob("legolize.*"))          # the work folder is gone
    assert set((home / "ssh_calls").read_text().split()) == {"lab-a"}


def test_downloads_stay_inside_the_results_folder(tmp_path):
    import io
    import tarfile
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        data = b"x"
        info = tarfile.TarInfo("../escape.txt")
        info.size = 1
        tf.addfile(info, io.BytesIO(data))
    with pytest.raises(RuntimeError):
        remote._untar(buf.getvalue(), tmp_path / "out")
    assert not (tmp_path / "escape.txt").exists()
