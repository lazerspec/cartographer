"""Remote staleness checking: verify pinned excerpts against the git host
without cloning anything. Single-file fetches via the GitHub CLI (gh),
in memory only. Facts are never modified here."""

import json
import subprocess
from pathlib import Path

from cartographer.anchor import identity, verify_excerpt

SOURCES_NAME = "sources.json"

# Status values per fact identity
OK = "ok"
DRIFTED = "drifted"
UNVERIFIABLE = "unverifiable"


def load_sources(map_root: Path) -> dict:
    p = Path(map_root) / SOURCES_NAME
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text())
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def fetch_remote_file(source: dict, path_in_repo: str) -> str | None:
    """Fetch one file's current text at the branch tip via `gh api`.
    Returns None on any failure (gh missing, not signed in, offline,
    file absent). Never raises, never writes to disk or stdout."""
    repo = source.get("repo")
    branch = source.get("branch", "main")
    if not repo:
        return None
    cmd = [
        "gh",
        "api",
        f"repos/{repo}/contents/{path_in_repo}?ref={branch}",
        "-H",
        "Accept: application/vnd.github.raw",
    ]
    host = source.get("host")
    if host:
        cmd += ["--hostname", host]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=30, check=False
        )
    except Exception:  # noqa: BLE001
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout


def fact_status(
    world: Path,
    sources: dict,
    fact: dict,
    fetch=fetch_remote_file,
    _cache: dict | None = None,
) -> str:
    anchor = fact["anchor"]
    rel = Path(anchor["path"])
    folder = rel.parts[0] if rel.parts else ""
    local = Path(world) / folder
    if local.exists():
        p = Path(world) / anchor["path"]
        if p.exists():
            return OK if verify_excerpt(p.read_text(), anchor) else DRIFTED
        return DRIFTED
    src = sources.get(folder)
    if not src:
        return UNVERIFIABLE
    path_in_repo = str(Path(*rel.parts[1:])) if len(rel.parts) > 1 else ""
    key = (folder, path_in_repo)
    if _cache is not None and key in _cache:
        text = _cache[key]
    else:
        text = fetch(src, path_in_repo)
        if _cache is not None:
            _cache[key] = text
    if text is None:
        return UNVERIFIABLE
    return OK if verify_excerpt(text, anchor) else DRIFTED


def chart_status(
    chart_dir: Path, world: Path, facts: list[dict], fetch=fetch_remote_file
) -> dict[tuple, str]:
    """Status per fact identity for a whole chart. One fetch per unique
    remote file (cached within the call)."""
    sources = load_sources(Path(chart_dir).resolve().parent)
    cache: dict = {}
    return {
        identity(f): fact_status(world, sources, f, fetch=fetch, _cache=cache)
        for f in facts
    }
