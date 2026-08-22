import json

import pytest

from cartographer.map_loader import LintError, lint_facts, load_sealed_chart


def _fact(subject, predicate, object_, **kw):
    return {
        "subject": subject,
        "predicate": predicate,
        "object": object_,
        "path": kw.get("path", "x/y.py"),
        "scope": kw.get("scope", "x"),
        "owner": kw.get("owner", "x-team"),
        **{k: v for k, v in kw.items() if k not in ("path", "scope", "owner")},
    }


def test_dangling_boundary_lint_fires():
    facts = [_fact("svc-a", "consumes_event", "ghost.event")]
    problems = lint_facts(facts)
    assert any("ghost.event" in p and "dangling" in p for p in problems)


def test_external_marker_suppresses_dangling_lint():
    facts = [_fact("svc-a", "consumes_event", "partner.file", external=True)]
    assert lint_facts(facts) == []


def test_contradiction_lint_fires():
    facts = [
        _fact("producer-svc", "emits_event", "pay.event"),
        _fact("pay.event", "promises_denomination", "minor_units"),
        _fact("consumer-svc", "consumes_event", "pay.event"),
        _fact("consumer-svc", "assumes_denomination", "major_units"),
    ]
    problems = lint_facts(facts)
    assert any("denomination" in p and "contradiction" in p for p in problems)


def test_agreeing_promise_and_assumption_pass():
    facts = [
        _fact("producer-svc", "emits_event", "pay.event"),
        _fact("pay.event", "promises_denomination", "minor_units"),
        _fact("consumer-svc", "consumes_event", "pay.event"),
        _fact("consumer-svc", "assumes_denomination", "minor_units"),
    ]
    assert lint_facts(facts) == []


def test_load_sealed_chart_raises_on_lint_failure(tmp_path):
    (tmp_path / "bad.json").write_text(
        json.dumps([_fact("svc-a", "consumes_event", "ghost.event")])
    )
    with pytest.raises(LintError) as exc:
        load_sealed_chart(tmp_path)
    assert exc.value.problems  # a severed map never silently answers


def test_missing_predicate_key_reports_problem_not_crash(tmp_path):
    (tmp_path / "bad.json").write_text(
        json.dumps(
            [{"subject": "a", "object": "e", "path": "x/y", "scope": "s", "owner": "o"}]
        )
    )
    with pytest.raises(LintError) as exc:
        load_sealed_chart(tmp_path)
    assert any("predicate" in p and "missing" in p for p in exc.value.problems)


def test_missing_scope_field_is_a_problem(tmp_path):
    (tmp_path / "bad.json").write_text(
        json.dumps(
            [{"subject": "a", "predicate": "emits_event", "object": "e", "path": "x/y"}]
        )
    )
    with pytest.raises(LintError):
        load_sealed_chart(tmp_path)


# --- Phase M extensions (anchor lints, manifest guard, maintenance load) ---


def _anchored_fact(**over):
    f = {
        "subject": "svc",
        "predicate": "reads_field",
        "object": "x",
        "path": "r/f.py",
        "scope": "s",
        "owner": "t",
        "anchor": {
            "kind": "code",
            "path": "r/f.py",
            "lines": [1, 1],
            "content_hash": "sha256:0",
            "revision": "step-00",
            "verified_at": "2026-07-03T00:00:00Z",
        },
    }
    f.update(over)
    return f


def test_anchor_path_mismatch_is_a_lint():
    from cartographer.map_loader import lint_facts

    bad = _anchored_fact()
    bad["anchor"] = dict(bad["anchor"], path="r/other.py")
    assert any("anchor path mismatch" in p for p in lint_facts([bad]))


def test_duplicate_identity_is_a_lint():
    from cartographer.map_loader import lint_facts

    a = _anchored_fact()
    b = _anchored_fact(path="r/g.py")
    b["anchor"] = dict(b["anchor"], path="r/g.py")
    assert any("duplicate identity" in p for p in lint_facts([a, b]))


def test_verify_manifest_detects_tamper(tmp_path):
    import hashlib
    import json as _json

    from cartographer.map_loader import verify_manifest

    chart = tmp_path / "chart"
    chart.mkdir()
    (chart / "s.json").write_text(_json.dumps([_anchored_fact()]))
    digest = "sha256:" + hashlib.sha256((chart / "s.json").read_bytes()).hexdigest()
    (chart / "chart.manifest").write_text(
        _json.dumps({"files": {"s.json": digest}, "fact_count": 1})
    )
    assert verify_manifest(chart) == []
    (chart / "s.json").write_text(_json.dumps([_anchored_fact(object="tampered")]))
    assert verify_manifest(chart)  # non-empty problems


def test_load_sealed_chart_fails_closed_on_manifest_mismatch(tmp_path):
    import json as _json

    import pytest as _pytest

    from cartographer.map_loader import LintError, load_sealed_chart

    chart = tmp_path / "chart"
    chart.mkdir()
    (chart / "s.json").write_text(_json.dumps([_anchored_fact()]))
    (chart / "chart.manifest").write_text(
        _json.dumps({"files": {"s.json": "sha256:wrong"}, "fact_count": 1})
    )
    with _pytest.raises(LintError):
        load_sealed_chart(chart)


def test_load_chart_returns_problems_without_raising(tmp_path):
    import json as _json

    from cartographer.map_loader import load_chart

    chart = tmp_path / "chart"
    chart.mkdir()
    bad = _anchored_fact()
    bad["anchor"] = dict(bad["anchor"], path="r/other.py")
    (chart / "s.json").write_text(_json.dumps([bad]))
    facts, problems = load_chart(chart, require_manifest=False)
    assert len(facts) == 1 and problems


def test_load_sealed_chart_fails_closed_on_manifest_absence(tmp_path):
    """Serving with NO manifest at all must fail closed (MCP v1 hardening
    2026-07-14): absence would otherwise silently bypass verification."""
    chart = tmp_path / "chart"
    chart.mkdir()
    (chart / "a.json").write_text("[]")
    with pytest.raises(LintError) as exc:
        load_sealed_chart(chart)
    assert any("manifest missing" in p for p in exc.value.problems)
