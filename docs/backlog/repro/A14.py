"""A14 (CAR-37) + A15 (CAR-38): seal never writes an empty or clobbering
manifest; an unlistable chart directory is a refusal, not an empty chart."""
import json, os, shutil, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent)); from helper import *
T = Path(__file__).parent / "tmp_A14"
shutil.rmtree(T, ignore_errors=True)
# (a) CAR-37: seal on a directory with no fact files
empty = T / "empty"; empty.mkdir(parents=True)
run("(a) seal on empty dir: expect RAISED SystemExit 'nothing to seal'", lambda: seal(empty))
print("  manifest written:", (empty / "chart.manifest").exists())
# (b) CAR-37: a stray manifest at the map root must not hide the hint
chart, world = setup(T / "b")
(chart.parent / "chart.manifest").write_text(json.dumps({"files": {}, "fact_count": 0}))
run("(b) check at map root with stray manifest: expect returned 2, 'did you mean' on stderr", lambda: check(chart.parent, world))
# (c) CAR-38: chart directory searchable but not listable (mode 0111)
chart, world = setup(T / "c")
before = (chart / "chart.manifest").read_bytes()
os.chmod(chart, 0o111)
try:
    run("(c1) check on 0111 chart: expect returned 2, 'not listable' on stderr", lambda: check(chart, world))
    run("(c2) seal on 0111 chart: expect RAISED SystemExit mentioning 'not listable'", lambda: seal(chart))
finally:
    os.chmod(chart, 0o755)
print("  manifest unchanged:", (chart / "chart.manifest").read_bytes() == before)
run("(c3) check after restoring the mode: expect returned 0", lambda: check(chart, world))
shutil.rmtree(T)
