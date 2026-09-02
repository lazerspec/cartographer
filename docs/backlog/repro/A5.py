import pathlib
import json, shutil, sys, subprocess
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent)); from helper import *
from cartographer.remote import fetch_remote_file
T = Path(__file__).parent / "tmp_A5"
PY = sys.executable; ENV = {"PYTHONPATH": str(pathlib.Path(__file__).resolve().parents[3] / "src")}
def cli(*args):
    p = subprocess.run([PY, "-m", "cartographer.cli", *map(str, args)], capture_output=True, text=True, env=ENV)
    tail = p.stderr.strip().splitlines()[-1:] if p.stderr.strip() else []
    print(f"  subprocess rc={p.returncode} stdout={p.stdout.strip()[:80]!r} stderr_last={tail}")
    return p.returncode

# (a) non-UTF8 bytes
chart, world = setup(T / "a")
(world / "svc/a.py").write_bytes(b"\xff\xfe\x00bad bytes\n")
run("(a) check non-UTF8", lambda: check(chart, world)); cli("check", chart, "--world", world)

# (b) pinned path is a directory
chart, world = setup(T / "b")
(world / "svc/a.py").unlink(); (world / "svc/a.py").mkdir()
run("(b) check path-is-dir", lambda: check(chart, world)); cli("check", chart, "--world", world)

# (c) invalid JSON manifest
chart, world = setup(T / "c")
(chart / "chart.manifest").write_text("{not json")
run("(c) check bad manifest", lambda: check(chart, world)); cli("check", chart, "--world", world)

# (d) sources.json entry is a string
chart, world = setup(T / "d")
(world / "svc/a.py").unlink()
(chart.parent / "sources.json").write_text('{"svc": "org/svc"}')
run("(d) fetch_remote_file('org/svc', 'a.py') direct", lambda: fetch_remote_file("org/svc", "a.py"))
run("(d) check with string source", lambda: check(chart, world)); cli("check", chart, "--world", world)

# (e) check against map root containing .mcp.json and chart/
chart, world = setup(T / "e")
maproot = chart.parent
(maproot / ".mcp.json").write_text(json.dumps({"mcpServers": {"cartographer": {"command": "x"}}}))
(maproot / "sources.json").write_text("{}")
print("  glob('*.json') at map root:", sorted(p.name for p in maproot.glob("*.json")))
run("(e) check map root", lambda: check(maproot, world)); cli("check", maproot, "--world", world)
