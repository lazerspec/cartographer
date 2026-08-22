"""Cartographer MCP server v0 — the honest read layer.

Four pull tools over one chart directory. Every call loads through the
fail-closed loader (LintError propagates as a tool error — never a partial
serve); anchors re-verify on demand against --world. Fact lines reuse the
renderer's _fact_line so the serve format never forks.
The FastMCP import stays inside build_server() so the pure functions are
importable and testable without the SDK installed.

Derived-tier facts serve only via get_derived_facts.
"""

import argparse
from pathlib import Path

from cartographer.anchor import verify_anchor
from cartographer.chart_context import _fact_line, context_hash, render_core_context
from cartographer.map_loader import load_sealed_chart
from cartographer.remote import (
    DRIFTED,
    UNVERIFIABLE,
    anchor_key,
    chart_status,
    fetch_remote_file,
)

DISCLAIMER = (
    "The map is sparse and verified: absence of a fact is not evidence of absence."
)

STALE_MARK = " [STALE? code changed]"
UNVERIFIED_MARK = " [UNVERIFIED: no local checkout, remote unreachable]"


def _split(
    world: Path, status: dict, facts: list[dict]
) -> tuple[set[tuple], set[tuple]]:
    """Per served fact: the FILE's presence decides local vs snapshot (a
    file gone from a partially-checked-out folder must not shadow a
    remote source). A local file re-verifies live (as 0.2.0); with no
    local file, the fact's status from the startup snapshot (keyed by
    anchor, not fact identity, so a re-anchored fact misses and defaults
    to unverifiable) decides drifted vs unverifiable."""
    drifted: set[tuple] = set()
    unverifiable: set[tuple] = set()
    for f in facts:
        anchor = f["anchor"]
        akey = anchor_key(f)
        if (Path(world) / anchor["path"]).exists():
            if not verify_anchor(world, anchor):
                drifted.add(akey)
        else:
            s = status.get(akey, UNVERIFIABLE)
            if s == DRIFTED:
                drifted.add(akey)
            elif s == UNVERIFIABLE:
                unverifiable.add(akey)
    return drifted, unverifiable


def _banner(n_drifted: int, n_total: int) -> str:
    return (
        f"WARNING: {n_drifted} of {n_total} facts point at code that has "
        "changed since the map was last verified. Treat marked facts as "
        "possibly stale. A human must run 'cartographer check' and review "
        "before these facts are trusted."
    )


def _unverifiable_note(n: int, n_total: int) -> str:
    return (
        f"NOTE: {n} of {n_total} facts could not be verified (service not "
        "checked out locally and not reachable remotely). They are served "
        "as last sealed."
    )


def _fact_counts(
    facts: list[dict], drifted: set[tuple], unverifiable: set[tuple]
) -> tuple[int, int]:
    """Counts of FACTS (not distinct anchor keys) whose anchor_key falls in
    each set — two facts sharing one anchor must both count."""
    n_drifted = sum(1 for f in facts if anchor_key(f) in drifted)
    n_unverifiable = sum(1 for f in facts if anchor_key(f) in unverifiable)
    return n_drifted, n_unverifiable


def _sorted_lines(
    facts: list[dict],
    drifted: set[tuple] | None = None,
    unverifiable: set[tuple] | None = None,
) -> list[str]:
    ordered = sorted(facts, key=lambda f: (f["subject"], f["predicate"], f["object"]))
    lines = []
    for f in ordered:
        line = _fact_line(f)
        akey = anchor_key(f)
        if drifted is not None and akey in drifted:
            line += STALE_MARK
        elif unverifiable is not None and akey in unverifiable:
            line += UNVERIFIED_MARK
        lines.append(line)
    return lines


def _core(facts: list[dict]) -> list[dict]:
    """S2: serving default excludes only tier == 'derived'; untagged
    charts are unaffected."""
    return [f for f in facts if f.get("tier") != "derived"]


def chart_index(chart_dir: Path, world: Path, status: dict) -> str:
    facts = _core(load_sealed_chart(chart_dir))
    drifted, unverifiable = _split(world, status, facts)
    by_scope: dict[str, list[dict]] = {}
    for f in facts:
        by_scope.setdefault(f["scope"], []).append(f)
    head = (
        f"CARTOGRAPHER CHART INDEX "
        f"({context_hash(render_core_context(chart_dir))}) — {DISCLAIMER}"
    )
    lines = [head]
    for scope in sorted(by_scope):
        subjects = sorted({f["subject"] for f in by_scope[scope]})
        lines.append(
            f"{scope}: {len(by_scope[scope])} facts, {len(subjects)} subjects"
            f" — e.g. {', '.join(subjects[:3])}"
        )
    n_drifted, n_unverifiable = _fact_counts(facts, drifted, unverifiable)
    out = "\n".join(lines)
    if n_unverifiable:
        out = _unverifiable_note(n_unverifiable, len(facts)) + "\n" + out
    if n_drifted:
        out = _banner(n_drifted, len(facts)) + "\n" + out
    return out


def get_scope_facts(chart_dir: Path, world: Path, status: dict, scope: str) -> str:
    facts = _core(load_sealed_chart(chart_dir))
    hits = [f for f in facts if f["scope"] == scope]
    if not hits:
        known = ", ".join(sorted({f["scope"] for f in facts}))
        return f"no facts for scope {scope!r}; known scopes: {known}"
    drifted, unverifiable = _split(world, status, hits)
    n_drifted, n_unverifiable = _fact_counts(hits, drifted, unverifiable)
    out = "\n".join(_sorted_lines(hits, drifted, unverifiable))
    if n_unverifiable:
        out = _unverifiable_note(n_unverifiable, len(hits)) + "\n" + out
    if n_drifted:
        out = _banner(n_drifted, len(hits)) + "\n" + out
    return out


def who_mentions(chart_dir: Path, world: Path, status: dict, token: str) -> str:
    facts = _core(load_sealed_chart(chart_dir))
    t = token.lower()
    hits = [f for f in facts if t in f["subject"].lower() or t in f["object"].lower()]
    if not hits:
        return f"no fact mentions {token!r} (literal string match over subject/object)"
    drifted, unverifiable = _split(world, status, hits)
    n_drifted, n_unverifiable = _fact_counts(hits, drifted, unverifiable)
    out = "\n".join(_sorted_lines(hits, drifted, unverifiable))
    if n_unverifiable:
        out = _unverifiable_note(n_unverifiable, len(hits)) + "\n" + out
    if n_drifted:
        out = _banner(n_drifted, len(hits)) + "\n" + out
    return out


DERIVED_CAVEAT = "DERIVED TIER (statically re-derivable; served on request only)"


def get_derived_facts(chart_dir: Path, world: Path, status: dict, scope: str) -> str:
    facts = [f for f in load_sealed_chart(chart_dir) if f.get("tier") == "derived"]
    hits = [f for f in facts if f["scope"] == scope]
    if not hits:
        known = ", ".join(sorted({f["scope"] for f in facts})) or "(none)"
        return f"no derived facts for scope {scope!r}; scopes with derived: {known}"
    drifted, unverifiable = _split(world, status, hits)
    n_drifted, n_unverifiable = _fact_counts(hits, drifted, unverifiable)
    out = DERIVED_CAVEAT + "\n" + "\n".join(_sorted_lines(hits, drifted, unverifiable))
    if n_unverifiable:
        out = _unverifiable_note(n_unverifiable, len(hits)) + "\n" + out
    if n_drifted:
        out = _banner(n_drifted, len(hits)) + "\n" + out
    return out


def staleness_check(
    chart_dir: Path, world: Path, status: dict, scope: str | None = None
) -> str:
    """Three-state, consistent with the serving tools: a local file
    re-verifies live; with no local file, the startup snapshot decides
    drifted vs unverifiable (via _split)."""
    facts = load_sealed_chart(chart_dir)
    if scope is not None:
        facts = [f for f in facts if f["scope"] == scope]
    drifted, unverifiable = _split(world, status, facts)
    n_drifted, n_unverifiable = _fact_counts(facts, drifted, unverifiable)
    n_verified = len(facts) - n_drifted - n_unverifiable
    out = f"verified {n_verified}/{len(facts)} anchors against {world}"
    drifted_facts = [f for f in facts if anchor_key(f) in drifted]
    unverifiable_facts = [f for f in facts if anchor_key(f) in unverifiable]
    if not drifted_facts and not unverifiable_facts:
        return out + "; 0 drifted"
    if drifted_facts:
        out += f"; {len(drifted_facts)} DRIFTED:\n" + "\n".join(
            _sorted_lines(drifted_facts)
        )
    if unverifiable_facts:
        out += (
            f"; {len(unverifiable_facts)} UNVERIFIABLE "
            "(no local checkout, remote unreachable):\n"
            + "\n".join(_sorted_lines(unverifiable_facts))
        )
    return out


def compute_status(chart_dir: Path, world: Path, fetch=fetch_remote_file) -> dict:
    """Startup verification snapshot. On ANY failure returns {} so remote
    facts default to unverifiable; serving stays fail-closed per call."""
    try:
        return chart_status(chart_dir, world, load_sealed_chart(chart_dir), fetch=fetch)
    except Exception:  # noqa: BLE001
        return {}


def build_server(chart_dir: Path, world: Path, fetch=fetch_remote_file):
    from mcp.server.fastmcp import FastMCP

    status = compute_status(chart_dir, world, fetch=fetch)

    app = FastMCP("cartographer")

    @app.tool(
        name="chart_index",
        description=(
            "One-line-per-scope index of the flow chart (fact counts, "
            "subjects, content hash). Cheap enough to keep in context; use "
            "the other tools to pull detail. " + DISCLAIMER
        ),
    )
    def _index() -> str:
        return chart_index(chart_dir, world, status)

    @app.tool(
        name="get_scope_facts",
        description=(
            "All verified facts for one scope, rendered as "
            "'subject --[predicate]--> object (scope; evidence: path:lines)'. "
            + DISCLAIMER
        ),
    )
    def _scope(scope: str) -> str:
        return get_scope_facts(chart_dir, world, status, scope)

    @app.tool(
        name="who_mentions",
        description=(
            "Facts whose subject or object contains the given token — "
            "literal case-insensitive string match, NOT semantic relevance. "
            + DISCLAIMER
        ),
    )
    def _mentions(token: str) -> str:
        return who_mentions(chart_dir, world, status, token)

    @app.tool(
        name="staleness_check",
        description=(
            "Re-verify every fact's source anchor against the live world "
            "tree; returns drifted facts (optionally one scope). " + DISCLAIMER
        ),
    )
    def _staleness(scope: str | None = None) -> str:
        return staleness_check(chart_dir, world, status, scope)

    @app.tool(
        name="get_derived_facts",
        description=(
            "Derived-tier facts for one scope (statically re-derivable "
            "from the module's own checkout; excluded from default "
            "serving). " + DISCLAIMER
        ),
    )
    def _derived(scope: str) -> str:
        return get_derived_facts(chart_dir, world, status, scope)

    return app


def main() -> None:
    ap = argparse.ArgumentParser(description="Cartographer MCP server v0")
    ap.add_argument("--chart", required=True, help="chart directory")
    ap.add_argument("--world", required=True, help="source tree anchors verify against")
    args = ap.parse_args()
    build_server(Path(args.chart), Path(args.world)).run()


if __name__ == "__main__":
    main()
