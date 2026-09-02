import json

import pytest

from cartographer.map_loader import LintError, lint_facts, load_sealed_chart

_HASH = "sha256:" + "0" * 64


def _fact(subject, predicate, object_, **kw):
    path = kw.get("path", "x/y.py")
    return {
        "subject": subject,
        "predicate": predicate,
        "object": object_,
        "path": path,
        "scope": kw.get("scope", "x"),
        "owner": kw.get("owner", "x-team"),
        "anchor": {
            "kind": "code",
            "path": path,
            "lines": [1, 1],
            "content_hash": _HASH,
            "revision": "r0",
            "verified_at": "2026-01-01T00:00:00Z",
        },
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
            "content_hash": _HASH,
            "revision": "step-00",
            "verified_at": "2026-07-03T00:00:00Z",
        },
    }
    f.update(over)
    return f


def _write_manifest(chart_dir):
    import hashlib as _hashlib
    import json as _json

    files = {}
    count = 0
    for p in sorted(chart_dir.glob("*.json")):
        files[p.name] = "sha256:" + _hashlib.sha256(p.read_bytes()).hexdigest()
        count += len(_json.loads(p.read_text()))
    (chart_dir / "chart.manifest").write_text(
        _json.dumps({"files": files, "fact_count": count})
    )


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


def test_lint_rejects_traversal_and_absolute_paths():
    from cartographer.map_loader import lint_facts

    traversal = _anchored_fact(path="../../../etc/passwd")
    traversal["anchor"] = dict(traversal["anchor"], path="../../../etc/passwd")
    absolute = _anchored_fact(path="/etc/hostname")
    absolute["anchor"] = dict(absolute["anchor"], path="/etc/hostname")
    clean = _anchored_fact()

    trav_problems = lint_facts([traversal])
    assert any("unsafe path" in p for p in trav_problems)

    abs_problems = lint_facts([absolute])
    assert any("unsafe path" in p for p in abs_problems)

    dot = _anchored_fact(path=".")
    dot["anchor"] = dict(dot["anchor"], path=".")
    dot_problems = lint_facts([dot])
    assert any("unsafe path" in p for p in dot_problems)

    # an empty path is caught earlier, by the required-fields lint (it is
    # falsy so `path` is reported missing before the path-shape lint runs)
    empty = _anchored_fact(path="")
    empty_problems = lint_facts([empty])
    assert any("missing" in p and "path" in p for p in empty_problems)

    assert not any("unsafe path" in p for p in lint_facts([clean]))


def test_load_sealed_chart_fails_closed_on_manifest_absence(tmp_path):
    """Serving with NO manifest at all must fail closed (MCP v1 hardening
    2026-07-14): absence would otherwise silently bypass verification."""
    chart = tmp_path / "chart"
    chart.mkdir()
    (chart / "a.json").write_text("[]")
    with pytest.raises(LintError) as exc:
        load_sealed_chart(chart)
    assert any("manifest missing" in p for p in exc.value.problems)


def test_lint_requires_well_formed_anchor():
    from cartographer.map_loader import lint_facts

    no_anchor = _anchored_fact()
    del no_anchor["anchor"]
    assert any("fact missing anchor" in p for p in lint_facts([no_anchor]))

    not_dict = _anchored_fact(anchor="r/f.py")
    assert any("fact missing anchor" in p for p in lint_facts([not_dict]))

    no_hash = _anchored_fact()
    del no_hash["anchor"]["content_hash"]
    assert any("anchor missing content_hash" in p for p in lint_facts([no_hash]))

    bad_hash = _anchored_fact()
    bad_hash["anchor"] = dict(bad_hash["anchor"], content_hash="sha256:0")
    assert any("content_hash malformed" in p for p in lint_facts([bad_hash]))

    no_lines = _anchored_fact()
    del no_lines["anchor"]["lines"]
    assert any("anchor missing lines" in p for p in lint_facts([no_lines]))

    for bad in ([1], [0, 2], [3, 1], [1, "2"], [True, 1], "1-2", [1, 2, 3]):
        f = _anchored_fact()
        f["anchor"] = dict(f["anchor"], lines=bad)
        assert any("anchor lines must be [lo, hi]" in p for p in lint_facts([f])), bad

    whole_file = _anchored_fact()
    whole_file["anchor"] = dict(whole_file["anchor"], lines=None)
    assert lint_facts([whole_file]) == []
    assert lint_facts([_anchored_fact()]) == []


def test_load_sealed_chart_refuses_unanchored_fact(tmp_path):
    import json as _json

    from cartographer.map_loader import LintError, load_sealed_chart

    f = _anchored_fact()
    del f["anchor"]
    (tmp_path / "s.json").write_text(_json.dumps([f]))
    _write_manifest(tmp_path)
    with pytest.raises(LintError) as exc:
        load_sealed_chart(tmp_path)
    assert any("fact missing anchor" in p for p in exc.value.problems)
