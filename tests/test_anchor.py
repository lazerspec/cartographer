"""Anchor schema + ladder. All key-free."""

import os
import threading
from pathlib import Path

import pytest

from cartographer.anchor import (
    Disposition,
    _slice,
    dispose,
    excerpt_hash,
    identity,
    make_code_anchor,
    make_external_anchor,
    normalize,
    read_pinned_text,
    run_ladder,
    verify_anchor,
    verify_excerpt,
)

AT = "2026-07-03T00:00:00Z"


def _fact(anchor: dict) -> dict:
    return {
        "subject": "svc",
        "predicate": "reads_field",
        "object": "f",
        "scope": "s",
        "owner": "t",
        "path": anchor["path"],
        "anchor": anchor,
    }


def _world(tmp_path: Path, name: str, files: dict[str, str]) -> Path:
    root = tmp_path / name
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    return root


def test_normalize_strips_trailing_ws_and_crlf_only():
    assert normalize("a  \r\nb\t\n") == "a\nb"
    assert normalize("  indented\n") == "  indented"  # leading ws preserved


def test_excerpt_hash_stable_under_trailing_ws():
    assert excerpt_hash("x: 1  \n") == excerpt_hash("x: 1\n")
    assert excerpt_hash("x: 1") != excerpt_hash("x: 2")


def test_make_and_verify_code_anchor(tmp_path):
    w = _world(tmp_path, "w0", {"r/f.yaml": "a: 1\nb: 2\nc: 3\n"})
    a = make_code_anchor(w, "r/f.yaml", (2, 3), "step-00", AT)
    assert a["kind"] == "code" and a["lines"] == [2, 3]
    assert a["revision"] == "step-00" and a["verified_at"] == AT
    assert verify_anchor(w, a)


def test_whole_file_anchor_lines_null(tmp_path):
    w = _world(tmp_path, "w0", {"r/f.yaml": "a: 1\n"})
    a = make_code_anchor(w, "r/f.yaml", None, "step-00", AT)
    assert a["lines"] is None
    assert verify_anchor(w, a)


def test_external_anchor_has_retrieved_at(tmp_path):
    w = _world(tmp_path, "w0", {"partner-specs/spec.md": "v1\n"})
    a = make_external_anchor(w, "partner-specs/spec.md", "step-00", AT)
    assert a["kind"] == "external" and a["lines"] is None
    assert a["retrieved_at"] == AT
    assert verify_anchor(w, a)


def test_untouched_file_is_l1_without_bump(tmp_path):
    w0 = _world(tmp_path, "w0", {"r/f.yaml": "a: 1\nb: 2\n"})
    w1 = _world(tmp_path, "w1", {"r/f.yaml": "a: 1\nb: 2\n"})
    f = _fact(make_code_anchor(w0, "r/f.yaml", (1, 1), "step-00", AT))
    d = dispose(
        w0,
        w1,
        f,
        changed=set(),
        deleted=set(),
        created=set(),
        revision="step-01",
        at=AT,
    )
    assert d.tier == "L1" and d.new_anchor is None


def test_intact_excerpt_in_changed_file_is_l1_with_bump(tmp_path):
    w0 = _world(tmp_path, "w0", {"r/f.yaml": "a: 1\nb: 2\n"})
    w1 = _world(tmp_path, "w1", {"r/f.yaml": "a: 1\nb: 2\nc: 3\n"})
    f = _fact(make_code_anchor(w0, "r/f.yaml", (1, 2), "step-00", AT))
    d = dispose(w0, w1, f, {"r/f.yaml"}, set(), set(), "step-01", AT)
    assert d.tier == "L1"
    assert d.new_anchor is not None and d.new_anchor["revision"] == "step-01"


def test_unique_relocation_is_l2(tmp_path):
    w0 = _world(tmp_path, "w0", {"r/f.yaml": "a: 1\nb: 2\n"})
    w1 = _world(tmp_path, "w1", {"r/f.yaml": "new: 0\na: 1\nb: 2\n"})
    f = _fact(make_code_anchor(w0, "r/f.yaml", (1, 2), "step-00", AT))
    d = dispose(w0, w1, f, {"r/f.yaml"}, set(), set(), "step-01", AT)
    assert d.tier == "L2" and d.new_anchor["lines"] == [2, 3]


def test_duplicate_excerpt_is_ambiguous_l3(tmp_path):
    w0 = _world(tmp_path, "w0", {"r/f.yaml": "x: 9\na: 1\n"})
    w1 = _world(tmp_path, "w1", {"r/f.yaml": "a: 1\npad: 0\na: 1\n"})
    f = _fact(make_code_anchor(w0, "r/f.yaml", (2, 2), "step-00", AT))
    d = dispose(w0, w1, f, {"r/f.yaml"}, set(), set(), "step-01", AT)
    assert d.tier == "L3" and d.ambiguous


def test_changed_excerpt_is_l3(tmp_path):
    w0 = _world(tmp_path, "w0", {"r/f.yaml": "a: 1\n"})
    w1 = _world(tmp_path, "w1", {"r/f.yaml": "a: 2\n"})
    f = _fact(make_code_anchor(w0, "r/f.yaml", (1, 1), "step-00", AT))
    d = dispose(w0, w1, f, {"r/f.yaml"}, set(), set(), "step-01", AT)
    assert d.tier == "L3" and not d.ambiguous


def test_rename_follow_is_l2_with_new_path(tmp_path):
    body = "a: 1\nb: 2\n"
    w0 = _world(tmp_path, "w0", {"r/old.py": body})
    w1 = _world(tmp_path, "w1", {"r/new.py": body})
    f = _fact(make_code_anchor(w0, "r/old.py", (1, 1), "step-00", AT))
    d = dispose(w0, w1, f, set(), {"r/old.py"}, {"r/new.py"}, "step-01", AT)
    assert d.tier == "L2" and d.new_anchor["path"] == "r/new.py"


def test_rename_follow_evolved_excerpt_is_l3_carrying_twin(tmp_path):
    """The anchor predates the step: the file evolved (stale excerpt), then
    got renamed. Rename-follow finds the twin by identical whole-file hash
    but cannot place the excerpt in it -> L3 carrying relocate_to, so any
    widened re-anchor is computed against the twin, not the deleted path."""
    seed = _world(tmp_path, "seed", {"r/old.py": "a = 1\nb = 2\n"})
    f = _fact(make_code_anchor(seed, "r/old.py", (1, 1), "step-00", AT))
    evolved = "a = 99\nb = 2\n"  # the anchored excerpt "a = 1" is gone
    w0 = _world(tmp_path, "w0", {"r/old.py": evolved})
    w1 = _world(tmp_path, "w1", {"r/new.py": evolved})
    d = dispose(w0, w1, f, set(), {"r/old.py"}, {"r/new.py"}, "step-01", AT)
    assert d.tier == "L3" and not d.ambiguous
    assert d.relocate_to == "r/new.py"


def test_rename_follow_ignores_unreadable_previous_file(tmp_path):
    seed = _world(tmp_path, "seed", {"svc/old.py": "a = 1\n"})
    f = _fact(make_code_anchor(seed, "svc/old.py", (1, 1), "step-00", AT))
    w0 = tmp_path / "w0"
    (w0 / "svc" / "old.py").mkdir(parents=True)
    w1 = _world(tmp_path, "w1", {"svc/empty.py": ""})
    d = dispose(w0, w1, f, set(), {"svc/old.py"}, {"svc/empty.py"}, "step-01", AT)
    assert d.tier != "L2"
    assert not any("renamed" in r for r in d.reasons)


def test_deleted_without_twin_is_l4(tmp_path):
    w0 = _world(tmp_path, "w0", {"r/old.py": "a: 1\n"})
    w1 = _world(tmp_path, "w1", {"r/keep.py": "other\n"})
    f = _fact(make_code_anchor(w0, "r/old.py", (1, 1), "step-00", AT))
    d = dispose(w0, w1, f, set(), {"r/old.py"}, set(), "step-01", AT)
    assert d.tier == "L4"


def test_external_mismatch_is_l3_never_l2(tmp_path):
    w0 = _world(tmp_path, "w0", {"partner-specs/spec.md": "seconds\n"})
    w1 = _world(tmp_path, "w1", {"partner-specs/spec.md": "millis\n"})
    f = _fact(make_external_anchor(w0, "partner-specs/spec.md", "step-00", AT))
    d = dispose(w0, w1, f, {"partner-specs/spec.md"}, set(), set(), "step-01", AT)
    assert d.tier == "L3"


def test_run_ladder_keys_by_identity(tmp_path):
    w0 = _world(tmp_path, "w0", {"r/f.yaml": "a: 1\n"})
    w1 = _world(tmp_path, "w1", {"r/f.yaml": "a: 1\n"})
    f = _fact(make_code_anchor(w0, "r/f.yaml", (1, 1), "step-00", AT))
    out = run_ladder(w0, w1, [f], set(), set(), set(), "step-01", AT)
    assert identity(f) in out
    assert isinstance(out[identity(f)], Disposition)


def test_slice_rejects_ranges_outside_the_file():
    three = "l1\nl2\nl3\n"
    for bad in [(5, 9), (3, 1), (0, 0), (0, 2), (-1, 1), (1, 4)]:
        with pytest.raises(ValueError):
            _slice(three, bad)
    assert _slice(three, (1, 3)) == "l1\nl2\nl3"
    assert _slice(three, (3, 3)) == "l3"


def test_anchor_past_eof_never_verifies(tmp_path):
    (tmp_path / "a.py").write_text("l1\nl2\nl3\n")
    with pytest.raises(ValueError):
        make_code_anchor(tmp_path, "a.py", (5, 9), "r", "t")
    forged = {
        "kind": "code",
        "path": "a.py",
        "lines": [5, 9],
        "content_hash": excerpt_hash(""),
        "revision": "r",
        "verified_at": "t",
    }
    assert verify_excerpt("l1\nl2\nl3\n", forged) is False
    assert verify_excerpt("COMPLETELY DIFFERENT\nstuff\n", forged) is False
    assert verify_anchor(tmp_path, forged) is False


def test_read_pinned_text_handles_dir_and_bytes(tmp_path):
    (tmp_path / "d").mkdir()
    assert read_pinned_text(tmp_path / "d") is None
    assert read_pinned_text(tmp_path / "missing") is None
    (tmp_path / "bin.py").write_bytes(b"\xff\xfe\x00bad bytes\n")
    text = read_pinned_text(tmp_path / "bin.py")
    assert text is not None
    assert excerpt_hash(text).startswith("sha256:")  # hashing surrogates never raises


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="platform has no FIFOs")
def test_read_pinned_text_returns_none_for_fifo_without_blocking(tmp_path):
    fifo = tmp_path / "a.py"
    os.mkfifo(fifo)
    result: list = []
    t = threading.Thread(
        target=lambda: result.append(read_pinned_text(fifo)), daemon=True
    )
    t.start()
    t.join(timeout=2)
    assert not t.is_alive(), "read_pinned_text blocked on a FIFO"
    assert result == [None]


def test_verify_anchor_false_for_dir_and_non_utf8(tmp_path):
    (tmp_path / "a.py").write_text("l1\nl2\n")
    a = make_code_anchor(tmp_path, "a.py", (1, 2), "r", "t")
    (tmp_path / "a.py").unlink()
    (tmp_path / "a.py").mkdir()
    assert verify_anchor(tmp_path, a) is False
    (tmp_path / "a.py").rmdir()
    (tmp_path / "a.py").write_bytes(b"\xff\xfe\x00")
    assert verify_anchor(tmp_path, a) is False


def test_empty_excerpt_cannot_be_anchored(tmp_path):
    (tmp_path / "empty.py").write_text("")
    (tmp_path / "blank.py").write_text("\n\n   \n")
    with pytest.raises(ValueError):
        make_code_anchor(tmp_path, "empty.py", None, "r", "t")
    with pytest.raises(ValueError):
        make_code_anchor(tmp_path, "blank.py", (1, 2), "r", "t")
