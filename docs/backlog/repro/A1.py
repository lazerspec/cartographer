import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent)); from helper import *
from cartographer.map_loader import lint_facts, load_sealed_chart
from cartographer import mcp_server
T = Path(__file__).parent / "tmp_A1"

# (a) fact with NO anchor key
chart, world = setup(T)
(chart / "facts.json").write_text(json.dumps([fact("svc/a.py")]))  # no anchor
print("lint_facts(no anchor):", lint_facts(json.loads((chart/"facts.json").read_text())))
run("seal", lambda: seal(chart))
run("cli.check no-anchor", lambda: check(chart, world))
run("mcp chart_index no-anchor", lambda: mcp_server.chart_index(chart, world, mcp_server.compute_status(chart, world)))

# (b) anchor with lines=[1]
a = make_code_anchor(world, "svc/a.py", (1, 2), "r", "t"); a["lines"] = [1]
(chart / "facts.json").write_text(json.dumps([fact("svc/a.py", a)]))
print("lint_facts(lines=[1]):", lint_facts([fact("svc/a.py", a)]))
run("seal", lambda: seal(chart))
run("cli.check lines=[1]", lambda: check(chart, world))

# (c) anchor missing content_hash
a = make_code_anchor(world, "svc/a.py", (1, 2), "r", "t"); del a["content_hash"]
(chart / "facts.json").write_text(json.dumps([fact("svc/a.py", a)]))
print("lint_facts(no content_hash):", lint_facts([fact("svc/a.py", a)]))
run("seal", lambda: seal(chart))
run("cli.check no content_hash", lambda: check(chart, world))
run("mcp chart_index no content_hash", lambda: mcp_server.chart_index(chart, world, mcp_server.compute_status(chart, world)))
