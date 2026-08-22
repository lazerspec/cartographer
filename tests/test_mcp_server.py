"""MCP server: pure tool behaviors over a minted fixture chart; fail-closed
on tamper; the thin shell registers exactly the five tools (get_derived_facts
alongside the original four)."""

import hashlib
import json
from pathlib import Path

import pytest

from cartographer.anchor import make_code_anchor
from cartographer.map_loader import LintError
from cartographer.mcp_server import (
    chart_index,
    get_scope_facts,
    staleness_check,
    who_mentions,
)


def write_manifest(chart_dir: Path) -> None:
    files = {}
    count = 0
    for p in sorted(chart_dir.glob("*.json")):
        files[p.name] = "sha256:" + hashlib.sha256(p.read_bytes()).hexdigest()
        count += len(json.loads(p.read_text()))
    (chart_dir / "chart.manifest").write_text(
        json.dumps({"files": files, "fact_count": count}, indent=1)
    )


def _mint(tmp_path: Path):
    """A 2-fact, 2-scope chart whose anchors genuinely verify against a
    minted world tree, with a valid manifest."""
    world = tmp_path / "world"
    (world / "svc").mkdir(parents=True)
    (world / "svc/a.py").write_text("amount = order.total\n")
    (world / "svc/b.py").write_text("tax = cart.amount\n")
    chart = tmp_path / "chart"
    chart.mkdir()
    f1 = {
        "subject": "order.total",
        "predicate": "consumed_by",
        "object": "billing.invoice",
        "path": "svc/a.py",
        "scope": "order",
        "owner": "t",
        "anchor": make_code_anchor(
            world, "svc/a.py", (1, 1), "rev-1", "2026-07-11T00:00:00Z"
        ),
    }
    f2 = {
        "subject": "cart.amount",
        "predicate": "read_by",
        "object": "tax.calc",
        "path": "svc/b.py",
        "scope": "cart",
        "owner": "t",
        "anchor": make_code_anchor(
            world, "svc/b.py", (1, 1), "rev-1", "2026-07-11T00:00:00Z"
        ),
    }
    (chart / "order.json").write_text(json.dumps([f1]))
    (chart / "cart.json").write_text(json.dumps([f2]))
    files = {}
    count = 0
    for p in sorted(chart.glob("*.json")):
        files[p.name] = "sha256:" + hashlib.sha256(p.read_bytes()).hexdigest()
        count += len(json.loads(p.read_text()))
    (chart / "chart.manifest").write_text(
        json.dumps({"files": files, "fact_count": count})
    )
    return chart, world


def test_chart_index_scopes_hash_disclaimer(tmp_path):
    chart, _ = _mint(tmp_path)
    out = chart_index(chart)
    assert "sha256:" in out
    assert "cart: 1 facts, 1 subjects" in out
    assert "order: 1 facts, 1 subjects" in out
    assert "absence of a fact is not evidence of absence" in out


def test_get_scope_facts_and_unknown_scope(tmp_path):
    chart, _ = _mint(tmp_path)
    out = get_scope_facts(chart, "order")
    assert "order.total --[consumed_by]--> billing.invoice" in out
    miss = get_scope_facts(chart, "nope")
    assert "known scopes: cart, order" in miss


def test_who_mentions_case_insensitive_and_miss(tmp_path):
    chart, _ = _mint(tmp_path)
    assert "cart.amount" in who_mentions(chart, "AMOUNT")
    assert "order.total" in who_mentions(chart, "invoice")
    assert "no fact mentions" in who_mentions(chart, "zzqx9")


def test_staleness_clean_drifted_and_scope_filter(tmp_path):
    chart, world = _mint(tmp_path)
    assert "verified 2/2" in staleness_check(chart, world)
    (world / "svc/a.py").write_text("amount = order.total_v2  # drifted\n")
    out = staleness_check(chart, world)
    assert "verified 1/2" in out
    assert "DRIFTED" in out
    assert "order.total" in out
    clean = staleness_check(chart, world, scope="cart")
    assert "verified 1/1" in clean
    assert "0 drifted" in clean


def test_fail_closed_on_tamper(tmp_path):
    chart, world = _mint(tmp_path)
    p = chart / "order.json"
    facts = json.loads(p.read_text())
    facts[0]["object"] = "tampered"
    p.write_text(json.dumps(facts))
    calls = [
        lambda: chart_index(chart),
        lambda: get_scope_facts(chart, "order"),
        lambda: who_mentions(chart, "amount"),
        lambda: staleness_check(chart, world),
    ]
    for call in calls:
        with pytest.raises(LintError):
            call()


def test_shell_registers_exactly_five_tools(tmp_path):
    pytest.importorskip("mcp")
    import asyncio

    from cartographer.mcp_server import build_server

    chart, world = _mint(tmp_path)
    app = build_server(chart, world)
    tools = asyncio.run(app.list_tools())
    assert sorted(t.name for t in tools) == [
        "chart_index",
        "get_derived_facts",
        "get_scope_facts",
        "staleness_check",
        "who_mentions",
    ]


# --- Stage-3 tier-aware serving (contract 2026-07-19, S2/S3/S7) -------------


def _tiered_chart(tmp_path):
    import json as _json

    d = tmp_path / "v4"
    d.mkdir()
    core = {
        "subject": "a",
        "predicate": "drives",
        "object": "y.b",
        "path": "p.ts",
        "scope": "x",
        "owner": "c",
        "anchor": {
            "kind": "code",
            "path": "p.ts",
            "lines": [1, 1],
            "content_hash": "sha256:0",
            "revision": "r",
            "verified_at": "t",
        },
        "tier": "core",
    }
    derived = dict(core, subject="d", predicate="carries_field", object="x.d.f")
    derived["tier"] = "derived"
    (d / "x.json").write_text(_json.dumps([core, derived]))
    write_manifest(d)
    return d


def test_default_tools_exclude_derived_tier(tmp_path):
    from cartographer.mcp_server import chart_index, get_scope_facts, who_mentions

    d = _tiered_chart(tmp_path)
    assert "1 facts" in chart_index(d)
    scoped = get_scope_facts(d, "x")
    assert "drives" in scoped and "carries_field" not in scoped
    assert "carries_field" not in who_mentions(d, "d.f")


def test_get_derived_facts_serves_on_request(tmp_path):
    from cartographer.mcp_server import get_derived_facts

    d = _tiered_chart(tmp_path)
    out = get_derived_facts(d, "x")
    assert out.startswith("DERIVED TIER")
    assert "carries_field" in out and "drives" not in out
    missing = get_derived_facts(d, "nope")
    assert "no derived facts" in missing


def test_untagged_charts_serve_unchanged(tmp_path):
    import json as _json

    from cartographer.mcp_server import chart_index, staleness_check

    d = tmp_path / "v0"
    d.mkdir()
    fact = {
        "subject": "a",
        "predicate": "drives",
        "object": "y.b",
        "path": "p.ts",
        "scope": "x",
        "owner": "c",
        "anchor": {
            "kind": "code",
            "path": "p.ts",
            "lines": [1, 1],
            "content_hash": "sha256:0",
            "revision": "r",
            "verified_at": "t",
        },
    }
    (d / "x.json").write_text(_json.dumps([fact]))
    write_manifest(d)
    assert "1 facts" in chart_index(d)  # S2: untagged == served
    # S3: staleness covers all facts regardless of tier
    world = tmp_path / "w"
    world.mkdir()
    out = staleness_check(d, world)
    assert "1 DRIFTED" in out


def test_staleness_covers_both_tiers(tmp_path):
    from cartographer.mcp_server import staleness_check

    d = _tiered_chart(tmp_path)
    world = tmp_path / "w"
    world.mkdir()
    out = staleness_check(d, world)
    assert "2 anchors" in out or "verified 0/2" in out


def test_render_core_context_filters_derived(tmp_path):
    from cartographer.chart_context import render_chart_context, render_core_context

    d = _tiered_chart(tmp_path)
    full = render_chart_context(d)
    core = render_core_context(d)
    assert "carries_field" in full and "carries_field" not in core
    assert "drives" in core
