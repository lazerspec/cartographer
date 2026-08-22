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
    STALE_MARK,
    UNVERIFIED_MARK,
    chart_index,
    compute_status,
    get_scope_facts,
    staleness_check,
    who_mentions,
)
from cartographer.remote import DRIFTED, anchor_key, chart_status


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
    chart, world = _mint(tmp_path)
    out = chart_index(chart, world, {})
    assert "sha256:" in out
    assert "cart: 1 facts, 1 subjects" in out
    assert "order: 1 facts, 1 subjects" in out
    assert "absence of a fact is not evidence of absence" in out


def test_get_scope_facts_and_unknown_scope(tmp_path):
    chart, world = _mint(tmp_path)
    out = get_scope_facts(chart, world, {}, "order")
    assert "order.total --[consumed_by]--> billing.invoice" in out
    miss = get_scope_facts(chart, world, {}, "nope")
    assert "known scopes: cart, order" in miss


def test_who_mentions_case_insensitive_and_miss(tmp_path):
    chart, world = _mint(tmp_path)
    assert "cart.amount" in who_mentions(chart, world, {}, "AMOUNT")
    assert "order.total" in who_mentions(chart, world, {}, "invoice")
    assert "no fact mentions" in who_mentions(chart, world, {}, "zzqx9")


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
        lambda: chart_index(chart, world, {}),
        lambda: get_scope_facts(chart, world, {}, "order"),
        lambda: who_mentions(chart, world, {}, "amount"),
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

    world = tmp_path / "tiered_world"
    world.mkdir()
    (world / "p.ts").write_text("const a = y.b;\n")
    d = tmp_path / "v4"
    d.mkdir()
    anchor = make_code_anchor(world, "p.ts", (1, 1), "r", "t")
    core = {
        "subject": "a",
        "predicate": "drives",
        "object": "y.b",
        "path": "p.ts",
        "scope": "x",
        "owner": "c",
        "anchor": anchor,
        "tier": "core",
    }
    derived = dict(core, subject="d", predicate="carries_field", object="x.d.f")
    derived["tier"] = "derived"
    (d / "x.json").write_text(_json.dumps([core, derived]))
    write_manifest(d)
    return d, world


def test_default_tools_exclude_derived_tier(tmp_path):
    from cartographer.mcp_server import chart_index, get_scope_facts, who_mentions

    d, world = _tiered_chart(tmp_path)
    assert "1 facts" in chart_index(d, world, {})
    scoped = get_scope_facts(d, world, {}, "x")
    assert "drives" in scoped and "carries_field" not in scoped
    assert "carries_field" not in who_mentions(d, world, {}, "d.f")


def test_get_derived_facts_serves_on_request(tmp_path):
    from cartographer.mcp_server import get_derived_facts

    d, world = _tiered_chart(tmp_path)
    out = get_derived_facts(d, world, {}, "x")
    assert out.startswith("DERIVED TIER")
    assert "carries_field" in out and "drives" not in out
    missing = get_derived_facts(d, world, {}, "nope")
    assert "no derived facts" in missing


def test_untagged_charts_serve_unchanged(tmp_path):
    import json as _json

    from cartographer.mcp_server import chart_index, staleness_check

    world = tmp_path / "w"
    world.mkdir()
    (world / "p.ts").write_text("const a = y.b;\n")
    d = tmp_path / "v0"
    d.mkdir()
    fact = {
        "subject": "a",
        "predicate": "drives",
        "object": "y.b",
        "path": "p.ts",
        "scope": "x",
        "owner": "c",
        "anchor": make_code_anchor(world, "p.ts", (1, 1), "r", "t"),
    }
    (d / "x.json").write_text(_json.dumps([fact]))
    write_manifest(d)
    assert "1 facts" in chart_index(d, world, {})  # S2: untagged == served
    # S3: staleness covers all facts regardless of tier
    (world / "p.ts").write_text("const a = y.b_v2;\n")
    out = staleness_check(d, world)
    assert "1 DRIFTED" in out


def test_staleness_covers_both_tiers(tmp_path):
    from cartographer.mcp_server import staleness_check

    d, _world = _tiered_chart(tmp_path)
    world = tmp_path / "w"
    world.mkdir()
    out = staleness_check(d, world)
    assert "2 anchors" in out or "verified 0/2" in out


def test_render_core_context_filters_derived(tmp_path):
    from cartographer.chart_context import render_chart_context, render_core_context

    d, _world = _tiered_chart(tmp_path)
    full = render_chart_context(d)
    core = render_core_context(d)
    assert "carries_field" in full and "carries_field" not in core
    assert "drives" in core


# --- stale-aware serving (contract amendment 2026-08-21) -------------------


def test_banner_and_mark_when_world_drifts(tmp_path):
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
        "scope": "shared",
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
        "scope": "shared",
        "owner": "t",
        "anchor": make_code_anchor(
            world, "svc/b.py", (1, 1), "rev-1", "2026-07-11T00:00:00Z"
        ),
    }
    (chart / "shared.json").write_text(json.dumps([f1, f2]))
    write_manifest(chart)

    # drift one of the two anchored files
    (world / "svc/a.py").write_text("amount = order.total_v2  # drifted\n")

    out = get_scope_facts(chart, world, {}, "shared")
    assert out.startswith("WARNING: 1 of")
    lines = out.splitlines()
    order_line = next(ln for ln in lines if "order.total" in ln)
    cart_line = next(ln for ln in lines if "cart.amount" in ln)
    assert order_line.endswith(STALE_MARK)
    assert not cart_line.endswith(STALE_MARK)

    idx = chart_index(chart, world, {})
    assert idx.startswith("WARNING: 1 of")


def test_no_banner_when_world_clean(tmp_path):
    chart, world = _mint(tmp_path)
    idx = chart_index(chart, world, {})
    scoped = get_scope_facts(chart, world, {}, "order")
    assert "WARNING:" not in idx and STALE_MARK not in idx
    assert "WARNING:" not in scoped and STALE_MARK not in scoped


def _remote_split_chart(tmp_path):
    """One fact anchored to a locally checked-out service (svc-a) and one
    anchored to a service that is not checked out (svc-b), sharing a
    scope, with a sources.json entry for svc-b so chart_status has
    somewhere to look."""
    world = tmp_path / "world"
    (world / "svc-a").mkdir(parents=True)
    (world / "svc-a" / "a.py").write_text("amount = order.total\n")
    origin = tmp_path / "origin"
    (origin / "svc-b").mkdir(parents=True)
    original_text = "tax = cart.amount\n"
    (origin / "svc-b" / "b.py").write_text(original_text)

    chart = tmp_path / "chart"
    chart.mkdir()
    f1 = {
        "subject": "order.total",
        "predicate": "consumed_by",
        "object": "billing.invoice",
        "path": "svc-a/a.py",
        "scope": "shared",
        "owner": "t",
        "anchor": make_code_anchor(
            world, "svc-a/a.py", (1, 1), "rev-1", "2026-07-11T00:00:00Z"
        ),
    }
    f2 = {
        "subject": "cart.amount",
        "predicate": "read_by",
        "object": "tax.calc",
        "path": "svc-b/b.py",
        "scope": "shared",
        "owner": "t",
        "anchor": make_code_anchor(
            origin, "svc-b/b.py", (1, 1), "rev-1", "2026-07-11T00:00:00Z"
        ),
    }
    (chart / "shared.json").write_text(json.dumps([f1, f2]))
    write_manifest(chart)
    (chart.parent / "sources.json").write_text(
        json.dumps({"svc-b": {"repo": "acme/svc-b", "branch": "main"}})
    )
    return chart, world, original_text


def test_mcp_banner_remote(tmp_path):
    chart, world, _original_text = _remote_split_chart(tmp_path)

    def fetch_drifted(source, path_in_repo):
        return "tax = cart.amount_v2  # drifted\n"

    facts = json.loads((chart / "shared.json").read_text())
    status_drifted = chart_status(chart, world, facts, fetch=fetch_drifted)
    out = get_scope_facts(chart, world, status_drifted, "shared")
    assert out.startswith("WARNING")
    lines = out.splitlines()
    cart_line = next(ln for ln in lines if "cart.amount" in ln)
    assert cart_line.endswith(STALE_MARK)

    def fetch_unreachable(source, path_in_repo):
        return None

    status_unreachable = chart_status(chart, world, facts, fetch=fetch_unreachable)
    out2 = get_scope_facts(chart, world, status_unreachable, "shared")
    assert "NOTE:" in out2
    lines2 = out2.splitlines()
    cart_line2 = next(ln for ln in lines2 if "cart.amount" in ln)
    assert cart_line2.endswith(UNVERIFIED_MARK)


def test_staleness_check_unchanged_by_feature(tmp_path):
    chart, world = _mint(tmp_path)
    out = staleness_check(chart, world)
    assert out.startswith("verified ")


# --- Fix 1/2/4: anchor-keyed snapshot, per-file local check, safe startup --


def _remote_only_chart(tmp_path, text: str):
    """A chart with one remote fact (svc-b not checked out in `world`),
    sources.json pointing at it, sealed via cartographer.cli.seal."""
    from cartographer.cli import seal

    tmp = tmp_path
    world = tmp / "workspace"
    world.mkdir()
    origin = tmp / "origin"
    (origin / "svc-b" / "src").mkdir(parents=True)
    (origin / "svc-b" / "src" / "consume.py").write_text(text)
    map_root = tmp / "map"
    chart = map_root / "chart"
    chart.mkdir(parents=True)
    (map_root / "sources.json").write_text(
        json.dumps({"svc-b": {"repo": "acme/svc-b"}})
    )
    f = {
        "subject": "svc-b",
        "predicate": "consumes_event",
        "object": "EventA",
        "scope": "svc-b",
        "owner": "t",
        "path": "svc-b/src/consume.py",
        "external": True,
        "anchor": make_code_anchor(
            origin, "svc-b/src/consume.py", (1, 3), "r1", "2026-08-21"
        ),
    }
    (chart / "flow.json").write_text(json.dumps([f], indent=1))
    seal(chart)
    return chart, world, origin, f


def test_reanchored_remote_fact_is_unverified_not_clean(tmp_path):
    from cartographer.cli import seal

    text = "def consume():\n    handle('EventA')\n    return True\n"
    chart, world, origin, f = _remote_only_chart(tmp_path, text)

    # startup snapshot: remote fetch matches -> OK, keyed by the OLD anchor
    facts = json.loads((chart / "flow.json").read_text())
    status = chart_status(chart, world, facts, fetch=lambda s, p: text)
    assert status[anchor_key(f)] == "ok"

    # mid-session: curator re-anchors the fact (new lines/hash), re-seals
    f2 = dict(f)
    f2["anchor"] = make_code_anchor(
        origin, "svc-b/src/consume.py", (2, 2), "r2", "2026-08-22"
    )
    (chart / "flow.json").write_text(json.dumps([f2], indent=1))
    seal(chart)

    # tool call reuses the OLD (frozen) status: the new anchor key misses
    out = get_scope_facts(chart, world, status, "svc-b")
    assert UNVERIFIED_MARK in out
    assert "NOTE:" in out
    assert STALE_MARK not in out


def test_split_uses_live_local_file_over_snapshot(tmp_path):
    world = tmp_path / "workspace"
    (world / "svc-b" / "src").mkdir(parents=True)
    text = "def consume():\n    handle('EventA')\n    return True\n"
    (world / "svc-b" / "src" / "consume.py").write_text(text)
    chart = tmp_path / "chart"
    chart.mkdir()
    f = {
        "subject": "svc-b",
        "predicate": "consumes_event",
        "object": "EventA",
        "scope": "svc-b",
        "owner": "t",
        "path": "svc-b/src/consume.py",
        "external": True,
        "anchor": make_code_anchor(
            world, "svc-b/src/consume.py", (1, 3), "r1", "2026-08-21"
        ),
    }
    (chart / "flow.json").write_text(json.dumps([f]))
    write_manifest(chart)

    # status dict falsely claims this exact anchor is DRIFTED; the live
    # local file matches, so live local must win over the snapshot.
    bad_status = {anchor_key(f): DRIFTED}
    out = get_scope_facts(chart, world, bad_status, "svc-b")
    assert STALE_MARK not in out
    assert UNVERIFIED_MARK not in out


def test_compute_status_returns_empty_on_broken_chart(tmp_path):
    chart = tmp_path / "chart"
    chart.mkdir()
    (chart / "bad.json").write_text(json.dumps([{"subject": "x"}]))  # fails lint
    world = tmp_path / "world"
    world.mkdir()
    assert compute_status(chart, world) == {}
