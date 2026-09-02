import json, shutil, sys, tempfile, traceback
from pathlib import Path
import pathlib; sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3] / "src"))
from cartographer.anchor import make_code_anchor
from cartographer.cli import seal, check

def fact(path, anchor=None, **kw):
    f = {"subject": "svc.A", "predicate": "emits_event", "object": "evt.X",
         "path": path, "scope": "svc", "owner": "team"}
    if anchor is not None:
        f["anchor"] = anchor
    f.update(kw)
    return f

def setup(root: Path, file_text="line1\nline2\nline3\n", lines=(1, 2), anchor_override=None, rel="svc/a.py"):
    """map_root/chart with one fact anchored at world/<rel>. Returns (chart, world)."""
    root = Path(root); shutil.rmtree(root, ignore_errors=True)
    world = root / "world"; (world / Path(rel).parent).mkdir(parents=True)
    (world / rel).write_text(file_text)
    maproot = root / "map"; chart = maproot / "chart"; chart.mkdir(parents=True)
    a = make_code_anchor(world, rel, lines, "rev1", "2026-01-01") if anchor_override is None else anchor_override
    (chart / "facts.json").write_text(json.dumps([fact(rel, a)]))
    print("seal:", seal(chart))
    return chart, world

def run(label, fn):
    print(f"--- {label}")
    try:
        r = fn()
        print("returned:", r)
        return r
    except BaseException as e:
        print(f"RAISED {type(e).__name__}: {e}")
        return e
