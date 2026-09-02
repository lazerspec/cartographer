import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent)); from helper import *
from cartographer.anchor import _slice, excerpt_hash, verify_excerpt
T = Path(__file__).parent / "tmp_A2"
three = "l1\nl2\nl3\n"
print("hash(''):", excerpt_hash(""))
for lines in [(5, 9), (3, 1), (0, 0), (-2, -1), (0, 2), (-1, 1)]:
    run(f"_slice{lines}", lambda: _slice(three, lines))
forged = {"kind": "code", "path": "svc/a.py", "lines": [5, 9], "content_hash": excerpt_hash(""), "revision": "r", "verified_at": "t"}
print("verify_excerpt(forged empty-hash anchor vs anything):", verify_excerpt("COMPLETELY DIFFERENT\nstuff\n", forged))
for lines in [(5, 9), (3, 1), (0, 0)]:
    run(f"make_code_anchor{lines}", lambda: setup(T, file_text=three, lines=lines))
r = run("setup forged", lambda: setup(T, file_text=three, lines=(1, 3), anchor_override=forged))
if not isinstance(r, BaseException):
    chart, world = r
    run("cli.check on a forged empty-hash anchor", lambda: check(chart, world))
