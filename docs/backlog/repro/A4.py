import pathlib
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent)); from helper import *
T = Path(__file__).parent / "tmp_A4"
chart, world = setup(T)
nowhere = T / "does_not_exist"
print("world exists:", nowhere.exists())
rc = run("cli.check --world nonexistent", lambda: check(chart, nowhere))
rc = run("cli.check --world nonexistent --strict", lambda: check(chart, nowhere, strict=True))
import subprocess
p = subprocess.run([sys.executable, "-m", "cartographer.cli", "check", str(chart), "--world", str(nowhere)],
                   capture_output=True, text=True, env={"PYTHONPATH": str(pathlib.Path(__file__).resolve().parents[3] / "src")})
print("subprocess rc:", p.returncode, "| stdout:", p.stdout.strip().replace("\n", " / "), "| stderr:", p.stderr.strip()[:200])
