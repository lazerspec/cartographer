import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent)); from helper import *
from cartographer.anchor import _slice, excerpt_hash, verify_excerpt
T = Path(__file__).parent / "tmp_A2"
three = "l1\nl2\nl3\n"
print("hash(''):", excerpt_hash(""))
for lines in [(5, 9), (3, 1), (0, 0), (-2, -1)]:
    print(lines, "slice=", repr(_slice(three, lines)), "hash==empty:", excerpt_hash(_slice(three, lines)) == excerpt_hash(""))
# lo<=0: (0,2) -> body[-1:2] -> "" (negative index); (-1,1) -> body[-2:1]
for lines in [(0, 2), (-1, 1)]:
    print(lines, "slice=", repr(_slice(three, lines)))
for lines in [(5, 9), (3, 1), (0, 0)]:
    chart, world = setup(T, file_text=three, lines=lines)
    a = make_code_anchor(world, "svc/a.py", lines, "r", "t")
    print("anchor", lines, a["content_hash"][:20])
    print("  verify_excerpt vs 'COMPLETELY DIFFERENT':", verify_excerpt("COMPLETELY DIFFERENT\nstuff\n", a))
    (world / "svc/a.py").write_text("totally rewritten\nx\ny\nz\nw\nv\nu\n")
    rc = run(f"cli.check after rewrite lines={lines}", lambda: check(chart, world))
