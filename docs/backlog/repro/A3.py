import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent)); from helper import *
from cartographer import mcp_server
T = Path(__file__).parent / "tmp_A3"
chart, world = setup(T)
status = mcp_server.compute_status(chart, world)   # startup snapshot
print("snapshot:", status)
(world / "svc/a.py").unlink()
print("deleted file; exists:", (world/"svc/a.py").exists())
print("staleness_check:", mcp_server.staleness_check(chart, world, status))
print("get_scope_facts:", mcp_server.get_scope_facts(chart, world, status, "svc"))
print("chart_index:", mcp_server.chart_index(chart, world, status))
rc = run("cli.check same state (no sources.json)", lambda: check(chart, world))
# with sources.json but unreachable remote
(chart.parent / "sources.json").write_text('{"svc": {"repo": "org/svc"}}')
rc = run("cli.check with sources.json, fetch->None", lambda: check(chart, world, fetch=lambda s, p: None))
