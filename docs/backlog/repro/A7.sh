#!/bin/zsh
set -u
V=$(cd "$(dirname "$0")" && pwd); SRC=$(cd "$V/../../.." && pwd); PY=$SRC/.venv/bin/python
run_mut() {  # $1 label, $2 python patch snippet
  C=$V/tmp_A7; rm -rf $C; mkdir -p $C; cp -R $SRC/src $SRC/tests $SRC/pyproject.toml $C/
  $PY - "$C" <<PYEOF
import sys, re; from pathlib import Path
root = Path(sys.argv[1]) / "src/cartographer"
$2
PYEOF
  echo "== $1"
  ( cd $C && PYTHONPATH=$C/src $PY -c "import cartographer,sys;print('  module:',cartographer.__file__)" && PYTHONPATH=$C/src $PY -m pytest -q -p no:cacheprovider 2>&1 | grep -E "passed|failed|error" | tail -1 )
}
run_mut "(i) drop 'chart file not in manifest' guard" '
p = root/"map_loader.py"; s = p.read_text()
old = "    for name in sorted(fact_files - listed):\n        problems.append(f\"chart file not in manifest: {name}\")\n"
assert old in s; p.write_text(s.replace(old, ""))'
run_mut "(ii) drop fact_count check" '
p = root/"map_loader.py"; s = p.read_text()
old = "    if count != manifest.get(\"fact_count\"):\n        problems.append(\n            f\"manifest fact_count {manifest.get(\x27fact_count\x27)} != actual {count}\"\n        )\n"
assert old in s, "pattern"; p.write_text(s.replace(old, ""))'
run_mut "(iii) remove try/except in fetch_remote_file" '
p = root/"remote.py"; s = p.read_text()
old = "    try:\n        proc = subprocess.run(\n            cmd, capture_output=True, text=True, timeout=30, check=False\n        )\n    except Exception:  # noqa: BLE001\n        return None\n"
new = "    proc = subprocess.run(\n        cmd, capture_output=True, text=True, timeout=30, check=False\n    )\n"
assert old in s; p.write_text(s.replace(old, new))'
run_mut "(iv) verify_anchor returns True for missing file" '
p = root/"anchor.py"; s = p.read_text()
old = "    if not p.exists():\n        return False\n    return verify_excerpt(p.read_text(), anchor)"
assert old in s; p.write_text(s.replace(old, "    if not p.exists():\n        return True\n    return verify_excerpt(p.read_text(), anchor)"))'
