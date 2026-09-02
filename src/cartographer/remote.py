"""Remote staleness checking: verify pinned excerpts against the git host
without cloning anything. Single-file fetches via the GitHub CLI (gh),
in memory only. Facts are never modified here."""

import json
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote

from cartographer.anchor import verify_excerpt

SOURCES_NAME = "sources.json"
_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")

# Status values per fact anchor
OK = "ok"
DRIFTED = "drifted"
UNVERIFIABLE = "unverifiable"


def anchor_key(fact: dict) -> tuple:
    a = fact["anchor"]
    return (a["path"], tuple(a["lines"] or ()), a["content_hash"])


def _warn(msg: str) -> None:
    print(f"cartographer: warning: {msg}", file=sys.stderr)


def load_sources(map_root: Path) -> dict:
    """sources.json as {service_folder: {"repo": "owner/name", ...}}.
    Malformed files or entries are reported once on stderr and treated as
    absent, so the affected facts become UNVERIFIABLE instead of crashing."""
    p = Path(map_root) / SOURCES_NAME
    if not p.exists():
        return {}
    if not p.is_file():
        _warn(f"{p}: not a regular file, ignoring")
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as e:
        _warn(f"{p}: unreadable or invalid JSON, ignoring all entries ({e})")
        return {}
    if not isinstance(data, dict):
        _warn(f"{p}: expected a JSON object mapping service folder to entry")
        return {}
    out: dict = {}
    for key, entry in data.items():
        reason: str | None = None
        if not (
            isinstance(key, str)
            and key
            and key == key.strip()
            and "/" not in key
            and "\\" not in key
            and key not in (".", "..")
        ):
            reason = "key must be a single folder name"
        elif not isinstance(entry, dict):
            reason = 'expected {"repo": "owner/name", "branch": "main"}'
        elif not (
            isinstance(entry.get("repo"), str) and _REPO_RE.fullmatch(entry["repo"])
        ):
            reason = "repo must look like owner/name"
        elif "branch" in entry and not (
            isinstance(entry.get("branch"), str)
            and entry["branch"]
            and not any(c.isspace() for c in entry["branch"])
            and not any(c in entry["branch"] for c in "?#&")
        ):
            reason = "branch must be a non-empty branch name"
        if reason is None:
            out[key] = entry
        else:
            _warn(f"{p}: entry {key!r} ignored ({reason})")
    return out


def fetch_remote_file(source: dict, path_in_repo: str) -> str | None:
    """Fetch one file's current text at the branch tip via `gh api`.
    Returns None on any failure (gh missing, not signed in, offline,
    file absent). Never raises, never writes to disk or stdout."""
    if not isinstance(source, dict) or not path_in_repo:
        return None
    repo = source.get("repo")
    branch = source.get("branch", "main")
    if not repo:
        return None
    endpoint = (
        f"repos/{repo}/contents/{quote(path_in_repo, safe='/')}"
        f"?ref={quote(branch, safe='')}"
    )
    cmd = [
        "gh",
        "api",
        endpoint,
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
    p = Path(world) / anchor["path"]
    if p.exists():
        return OK if verify_excerpt(p.read_text(), anchor) else DRIFTED
    if len(rel.parts) < 2:
        return UNVERIFIABLE
    src = sources.get(folder)
    if src:
        path_in_repo = str(Path(*rel.parts[1:]))
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
    local = Path(world) / folder
    if local.exists():
        return DRIFTED
    return UNVERIFIABLE


def chart_status(
    chart_dir: Path, world: Path, facts: list[dict], fetch=fetch_remote_file
) -> dict[tuple, str]:
    """Status per fact anchor for a whole chart. One fetch per unique
    remote file (cached within the call)."""
    sources = load_sources(Path(chart_dir).resolve().parent)
    cache: dict = {}
    return {
        anchor_key(f): fact_status(world, sources, f, fetch=fetch, _cache=cache)
        for f in facts
    }
