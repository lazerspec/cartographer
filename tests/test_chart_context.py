import json

from cartographer.chart_context import context_hash, render_chart_context

FACT_A = {
    "subject": "fulfillment",
    "predicate": "assumes_order_paid",
    "object": "order.payment_status=captured",
    "path": "packages/modules/fulfillment/x.ts",
    "scope": "fulfillment",
    "owner": "core",
    "anchor": {
        "kind": "code",
        "path": "packages/modules/fulfillment/x.ts",
        "lines": [3, 5],
        "content_hash": "sha256:aa",
        "revision": "s0",
        "verified_at": "2026-07-04T00:00:00Z",
    },
    "metadata": {"asserted_by": "human:sam", "pr_ref": "example/repo#1"},
}
FACT_B = {
    "subject": "cart",
    "predicate": "carries_currency",
    "object": "currency_code",
    "path": "packages/modules/cart/y.ts",
    "scope": "cart",
    "owner": "core",
    "anchor": {
        "kind": "code",
        "path": "packages/modules/cart/y.ts",
        "lines": None,
        "content_hash": "sha256:bb",
        "revision": "s0",
        "verified_at": "2026-07-04T00:00:00Z",
    },
}


def _chart(tmp_path, facts):
    d = tmp_path / "chart"
    d.mkdir()
    by_scope = {}
    for f in facts:
        by_scope.setdefault(f["scope"], []).append(f)
    import hashlib

    manifest = {"files": {}, "fact_count": len(facts)}
    for scope, fs in by_scope.items():
        p = d / f"{scope}.json"
        p.write_text(json.dumps(fs, indent=1))
        manifest["files"][p.name] = (
            "sha256:" + hashlib.sha256(p.read_bytes()).hexdigest()
        )
    (d / "chart.manifest").write_text(json.dumps(manifest))
    return d


def test_deterministic_and_sorted(tmp_path):
    d = _chart(tmp_path, [FACT_A, FACT_B])
    block1 = render_chart_context(d)
    block2 = render_chart_context(d)
    assert block1 == block2
    # sorted by (scope, subject, predicate, object): cart before fulfillment
    assert block1.index("cart") < block1.index("fulfillment")
    assert "assumes_order_paid" in block1
    assert "packages/modules/fulfillment/x.ts:3-5" in block1
    assert "pr_ref=example/repo#1" in block1
    assert context_hash(block1).startswith("sha256:")


def test_serving_is_fail_closed(tmp_path):
    bad = dict(FACT_A)
    bad = {**FACT_A, "object": ""}  # missing required field
    d = _chart(tmp_path, [bad])
    import pytest

    from cartographer.map_loader import LintError

    with pytest.raises(LintError):
        render_chart_context(d)
