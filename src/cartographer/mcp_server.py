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

from cartographer.anchor import identity, verify_anchor
from cartographer.chart_context import _fact_line, context_hash, render_core_context
from cartographer.map_loader import load_sealed_chart

DISCLAIMER = (
    "The map is sparse and verified: absence of a fact is not evidence of absence."
)

STALE_MARK = " [STALE? code changed]"


def _drifted_ids(world: Path, facts: list[dict]) -> set[tuple]:
    return {identity(f) for f in facts if not verify_anchor(world, f["anchor"])}


def _banner(n_drifted: int, n_total: int) -> str:
    return (
        f"WARNING: {n_drifted} of {n_total} facts point at code that has "
        "changed since the map was last verified. Treat marked facts as "
        "possibly stale. A human must run 'cartographer check' and review "
        "before these facts are trusted."
    )


def _sorted_lines(facts: list[dict], drifted: set[tuple] | None = None) -> list[str]:
    ordered = sorted(facts, key=lambda f: (f["subject"], f["predicate"], f["object"]))
    lines = []
    for f in ordered:
        line = _fact_line(f)
        if drifted is not None and identity(f) in drifted:
            line += STALE_MARK
        lines.append(line)
    return lines


def _core(facts: list[dict]) -> list[dict]:
    """S2: serving default excludes only tier == 'derived'; untagged
    charts are unaffected."""
    return [f for f in facts if f.get("tier") != "derived"]


def chart_index(chart_dir: Path, world: Path) -> str:
    facts = _core(load_sealed_chart(chart_dir))
    drifted = _drifted_ids(world, facts)
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
    out = "\n".join(lines)
    if drifted:
        out = _banner(len(drifted), len(facts)) + "\n" + out
    return out


def get_scope_facts(chart_dir: Path, world: Path, scope: str) -> str:
    facts = _core(load_sealed_chart(chart_dir))
    hits = [f for f in facts if f["scope"] == scope]
    if not hits:
        known = ", ".join(sorted({f["scope"] for f in facts}))
        return f"no facts for scope {scope!r}; known scopes: {known}"
    drifted = _drifted_ids(world, hits)
    out = "\n".join(_sorted_lines(hits, drifted))
    if drifted:
        out = _banner(len(drifted), len(hits)) + "\n" + out
    return out


def who_mentions(chart_dir: Path, world: Path, token: str) -> str:
    facts = _core(load_sealed_chart(chart_dir))
    t = token.lower()
    hits = [f for f in facts if t in f["subject"].lower() or t in f["object"].lower()]
    if not hits:
        return f"no fact mentions {token!r} (literal string match over subject/object)"
    drifted = _drifted_ids(world, hits)
    out = "\n".join(_sorted_lines(hits, drifted))
    if drifted:
        out = _banner(len(drifted), len(hits)) + "\n" + out
    return out


DERIVED_CAVEAT = "DERIVED TIER (statically re-derivable; served on request only)"


def get_derived_facts(chart_dir: Path, world: Path, scope: str) -> str:
    facts = [f for f in load_sealed_chart(chart_dir) if f.get("tier") == "derived"]
    hits = [f for f in facts if f["scope"] == scope]
    if not hits:
        known = ", ".join(sorted({f["scope"] for f in facts})) or "(none)"
        return f"no derived facts for scope {scope!r}; scopes with derived: {known}"
    drifted = _drifted_ids(world, hits)
    out = DERIVED_CAVEAT + "\n" + "\n".join(_sorted_lines(hits, drifted))
    if drifted:
        out = _banner(len(drifted), len(hits)) + "\n" + out
    return out


def staleness_check(chart_dir: Path, world: Path, scope: str | None = None) -> str:
    facts = load_sealed_chart(chart_dir)
    if scope is not None:
        facts = [f for f in facts if f["scope"] == scope]
    drifted = [f for f in facts if not verify_anchor(world, f["anchor"])]
    head = f"verified {len(facts) - len(drifted)}/{len(facts)} anchors against {world}"
    if not drifted:
        return head + "; 0 drifted"
    return head + f"; {len(drifted)} DRIFTED:\n" + "\n".join(_sorted_lines(drifted))


def build_server(chart_dir: Path, world: Path):
    from mcp.server.fastmcp import FastMCP

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
        return chart_index(chart_dir, world)

    @app.tool(
        name="get_scope_facts",
        description=(
            "All verified facts for one scope, rendered as "
            "'subject --[predicate]--> object (scope; evidence: path:lines)'. "
            + DISCLAIMER
        ),
    )
    def _scope(scope: str) -> str:
        return get_scope_facts(chart_dir, world, scope)

    @app.tool(
        name="who_mentions",
        description=(
            "Facts whose subject or object contains the given token — "
            "literal case-insensitive string match, NOT semantic relevance. "
            + DISCLAIMER
        ),
    )
    def _mentions(token: str) -> str:
        return who_mentions(chart_dir, world, token)

    @app.tool(
        name="staleness_check",
        description=(
            "Re-verify every fact's source anchor against the live world "
            "tree; returns drifted facts (optionally one scope). " + DISCLAIMER
        ),
    )
    def _staleness(scope: str | None = None) -> str:
        return staleness_check(chart_dir, world, scope)

    @app.tool(
        name="get_derived_facts",
        description=(
            "Derived-tier facts for one scope (statically re-derivable "
            "from the module's own checkout; excluded from default "
            "serving). " + DISCLAIMER
        ),
    )
    def _derived(scope: str) -> str:
        return get_derived_facts(chart_dir, world, scope)

    return app


def main() -> None:
    ap = argparse.ArgumentParser(description="Cartographer MCP server v0")
    ap.add_argument("--chart", required=True, help="chart directory")
    ap.add_argument("--world", required=True, help="source tree anchors verify against")
    args = ap.parse_args()
    build_server(Path(args.chart), Path(args.world)).run()


if __name__ == "__main__":
    main()
