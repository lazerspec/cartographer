import sys, subprocess as sp
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent)); from helper import *
import cartographer.remote as remote
from cartographer import cli
T = Path(__file__).parent / "tmp_A6"
chart, world = setup(T)
(world / "svc/a.py").unlink()                       # not checked out locally
(chart.parent / "sources.json").write_text('{"svc": {"repo": "org/svc"}}')
calls = []
class P: returncode = 1; stdout = ""; stderr = "stub"
def fake_run(cmd, **kw):
    if cmd[:2] == ["gh", "api"]: calls.append(cmd[2])
    return P()
remote.subprocess.run = fake_run                    # fetch_remote_file -> gh api -> counted
from mcp.server.fastmcp import FastMCP
FastMCP.run = lambda self, *a, **k: print("  [stub] app.run() called")
cli.pull_map_repo = lambda c: True
print("static: serve() calls startup_staleness_notice -> chart_status(fetch=default) and build_server -> compute_status(fetch=default)")
rc = cli.serve(chart, world)
print("gh api calls during serve():", len(calls), calls)
