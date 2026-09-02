"""Cartographer command line: init, seal, check, serve.

Humans approve, tools verify: `seal` refuses charts that fail lint, `check`
never modifies anything, and nothing here edits facts automatically.
"""

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

from cartographer.chart_context import _fact_line
from cartographer.map_loader import (
    MANIFEST_NAME,
    LintError,
    _fact_files,
    _not_a_chart_hint,
    _read_fact_files,
    lint_facts,
    load_sealed_chart,
)
from cartographer.remote import anchor_key, chart_status, fetch_remote_file


def seal(chart_dir: Path) -> str:
    """Recompute chart.manifest after human-approved edits. Refuses to seal
    a chart that fails lint (a sealed chart must always serve)."""
    chart_dir = Path(chart_dir)
    if not chart_dir.is_dir():
        raise SystemExit(f"not a directory: {chart_dir}")
    facts, problems = _read_fact_files(chart_dir)
    problems = _not_a_chart_hint(chart_dir) + problems
    files = {
        p.name: "sha256:" + hashlib.sha256(p.read_bytes()).hexdigest()
        for p in _fact_files(chart_dir)
    }
    problems += lint_facts(facts)
    if problems:
        raise SystemExit(
            "refusing to seal a chart that fails lint:\n" + "\n".join(problems)
        )
    manifest = {"files": files, "fact_count": len(facts)}
    (chart_dir / MANIFEST_NAME).write_text(json.dumps(manifest, indent=1) + "\n")
    return f"sealed {chart_dir}: {len(files)} files, {len(facts)} facts"


def _by_fact_order(facts: list[dict]) -> list[dict]:
    return sorted(facts, key=lambda f: (f["subject"], f["predicate"], f["object"]))


def check(
    chart_dir: Path, world: Path, strict: bool = False, fetch=fetch_remote_file
) -> int:
    """Verify every fact's anchor against the live code, or (when the
    service is not checked out) against the git host via sources.json.
    Read-only. Exit codes: 0 clean, 1 drifted facts need review (or zero
    facts verified, or with --strict any unverifiable fact), 2 chart
    refused or world directory missing."""
    from cartographer.mcp_server import _split

    try:
        facts = load_sealed_chart(Path(chart_dir))
    except LintError as e:
        print(
            "CHART REFUSED (fail-closed). Fix the problems below, then run "
            "`cartographer seal <chart>`:",
            file=sys.stderr,
        )
        for problem in e.problems:
            print(f"  {problem}", file=sys.stderr)
        return 2
    world = Path(world)
    if not world.is_dir():
        print(f"world directory not found: {world}", file=sys.stderr)
        return 2
    status = chart_status(Path(chart_dir), world, facts, fetch=fetch)
    drifted, unverifiable = _split(world, status, facts)
    drifted_facts = [f for f in facts if anchor_key(f) in drifted]
    unverifiable_facts = [f for f in facts if anchor_key(f) in unverifiable]
    n_verified = len(facts) - len(drifted_facts) - len(unverifiable_facts)
    print(f"verified {n_verified}/{len(facts)} anchors against {world}")
    if not drifted_facts:
        print("0 drifted")
    else:
        print(f"{len(drifted_facts)} DRIFTED (re-confirm each fact, then re-seal):")
        for f in _by_fact_order(drifted_facts):
            print("  " + _fact_line(f))
    if unverifiable_facts:
        print(
            f"{len(unverifiable_facts)} UNVERIFIABLE (no local checkout; add the "
            "service to sources.json or sign in with gh):"
        )
        for f in _by_fact_order(unverifiable_facts):
            print("  " + _fact_line(f))
    if facts and n_verified == 0 and not drifted_facts:
        print(
            "0 facts verified: nothing could be checked (wrong --world, or no "
            "checkout and no sources.json entry). Treating as failure."
        )
        return 1
    if drifted_facts:
        return 1
    if strict and unverifiable_facts:
        return 1
    return 0


_TEMPLATE_FILES = {
    "README.md": "README.md",
    "CLAUDE.md": "CLAUDE.md",
    "mcp.json": ".mcp.json",
    "gitignore": ".gitignore",
}


def init(target: Path) -> int:
    """Scaffold a new map repo, ready to git-init and push."""
    from importlib import resources

    target = Path(target)
    if target.exists() and any(target.iterdir()):
        raise SystemExit(f"refusing to init into non-empty directory: {target}")
    (target / "chart").mkdir(parents=True, exist_ok=True)
    (target / "chart" / "facts.json").write_text("[]\n")
    seal(target / "chart")
    (target / "sources.json").write_text("{}\n")
    tdir = resources.files("cartographer").joinpath("templates")
    for src_name, dest_name in _TEMPLATE_FILES.items():
        (target / dest_name).write_text(tdir.joinpath(src_name).read_text())
    workflows = target / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "drift-example.yml").write_text(
        tdir.joinpath("drift-example.yml").read_text()
    )
    print(f"initialized map repo at {target}")
    print("next steps:")
    print("  1. keep this folder inside your workspace, next to your service checkouts")
    print("  2. git init && git add -A && git commit -m 'new cartographer map'")
    print("  3. push it to your org's git host")
    print(
        "  4. open Claude Code in this folder; CLAUDE.md explains how facts get added"
    )
    return 0


def pull_map_repo(chart_dir: Path) -> bool:
    """Best-effort `git pull --ff-only` in the map repo (the chart dir's
    parent). Returns True on success. On ANY failure (git missing, not a
    repo, no remote, offline, non-ff) prints one warning line to stderr
    ("map pull failed, serving local copy: <first line of error>") and
    returns False. Never raises."""
    try:
        result = subprocess.run(
            ["git", "pull", "--ff-only", "--quiet"],
            cwd=Path(chart_dir).resolve().parent,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if result.returncode == 0:
            return True
        error = (result.stderr or result.stdout or "").strip().splitlines()
        first_line = error[0] if error else "unknown error"
    except Exception as e:  # noqa: BLE001 - best-effort pull must never raise
        first_line = str(e).splitlines()[0] if str(e) else repr(e)
    print(f"map pull failed, serving local copy: {first_line}", file=sys.stderr)
    return False


def startup_staleness_notice(chart: Path, world: Path) -> None:
    """Best-effort: warn on stderr if any fact's anchor has drifted, or
    could not be verified, since the map was last verified. Never raises;
    never writes to stdout."""
    try:
        from cartographer.map_loader import load_sealed_chart
        from cartographer.mcp_server import _banner, _split, _unverifiable_note

        chart_p = Path(chart)
        world_p = Path(world)
        facts = load_sealed_chart(chart_p)
        status = chart_status(chart_p, world_p, facts)
        drifted, unverifiable = _split(world_p, status, facts)
        if drifted:
            print(_banner(len(drifted), len(facts)), file=sys.stderr)
        if unverifiable:
            print(_unverifiable_note(len(unverifiable), len(facts)), file=sys.stderr)
    except Exception as e:  # noqa: BLE001
        print(f"startup staleness check skipped: {e}", file=sys.stderr)


def serve(chart: Path, world: Path, pull: bool = False) -> int:
    from cartographer.mcp_server import build_server

    if pull:
        pull_map_repo(Path(chart))
    startup_staleness_notice(Path(chart), Path(world))
    build_server(Path(chart), Path(world)).run()
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="cartographer")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_init = sub.add_parser("init", help="scaffold a new map repo")
    p_init.add_argument("target", help="directory to create")

    p_seal = sub.add_parser("seal", help="recompute the chart manifest")
    p_seal.add_argument("chart", help="chart directory")

    p_check = sub.add_parser("check", help="verify anchors against the code")
    p_check.add_argument("chart", help="chart directory")
    p_check.add_argument("--world", required=True, help="workspace root")
    p_check.add_argument(
        "--strict",
        action="store_true",
        help="also fail (exit 1) if any fact is unverifiable",
    )

    p_serve = sub.add_parser("serve", help="run the MCP server")
    p_serve.add_argument("--chart", required=True)
    p_serve.add_argument("--world", required=True)
    p_serve.add_argument("--pull", action="store_true")

    args = ap.parse_args(argv)
    if args.cmd == "init":
        return init(Path(args.target))
    if args.cmd == "seal":
        print(seal(Path(args.chart)))
        return 0
    if args.cmd == "check":
        return check(Path(args.chart), Path(args.world), strict=args.strict)
    return serve(Path(args.chart), Path(args.world), args.pull)


if __name__ == "__main__":
    raise SystemExit(main())
