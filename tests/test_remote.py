import json
from pathlib import Path

from cartographer.remote import (
    DRIFTED,
    OK,
    UNVERIFIABLE,
    anchor_key,
    chart_status,
    fact_status,
    fetch_remote_file,
)
from tests.test_cli import fact, write_chart, write_world


def _recording_fetch(calls: list):
    def _fetch(source: dict, path_in_repo: str) -> str | None:
        calls.append((source, path_in_repo))
        return None

    return _fetch


def test_local_checkout_wins(tmp_path):
    world = write_world(tmp_path)
    chart = write_chart(tmp_path, world)
    facts = [
        fact(world, "svc-a", "emits_event", "EventA", "svc-a/src/publish.py"),
        fact(world, "svc-b", "consumes_event", "EventA", "svc-b/src/consume.py"),
    ]
    calls: list = []
    status = chart_status(chart, world, facts, fetch=_recording_fetch(calls))
    assert status[anchor_key(facts[0])] == OK
    assert calls == []


def _origin_with_svc_b(tmp_path: Path, text: str) -> Path:
    """A scratch tree (never used as the chart_status `world`) that has
    svc-b checked out, purely so make_code_anchor can compute a real
    anchor for it."""
    origin = tmp_path / "origin"
    (origin / "svc-b" / "src").mkdir(parents=True)
    (origin / "svc-b" / "src" / "consume.py").write_text(text)
    return origin


def test_remote_ok_and_drifted(tmp_path):
    world = tmp_path / "workspace"
    world.mkdir()
    (world / "svc-a" / "src").mkdir(parents=True)
    (world / "svc-a" / "src" / "publish.py").write_text(
        "def publish():\n    emit('EventA')\n    return True\n"
    )
    map_root = tmp_path / "map"
    chart = map_root / "chart"
    chart.mkdir(parents=True)
    f_local = fact(world, "svc-a", "emits_event", "EventA", "svc-a/src/publish.py")
    original_text = "def consume():\n    handle('EventA')\n    return True\n"
    origin = _origin_with_svc_b(tmp_path, original_text)
    f_remote = fact(origin, "svc-b", "consumes_event", "EventA", "svc-b/src/consume.py")
    (map_root / "sources.json").write_text(
        json.dumps({"svc-b": {"repo": "acme/svc-b", "branch": "main"}})
    )

    def fetch_ok(source, path_in_repo):
        return original_text

    # world (unlike origin) has no svc-b checkout, so this exercises the
    # remote path
    status_ok = chart_status(chart, world, [f_local, f_remote], fetch=fetch_ok)
    assert status_ok[anchor_key(f_remote)] == OK

    def fetch_drifted(source, path_in_repo):
        return "def consume():\n    handle('EventB')\n    return True\n"

    status_drifted = chart_status(chart, world, [f_remote], fetch=fetch_drifted)
    assert status_drifted[anchor_key(f_remote)] == DRIFTED


def test_remote_unverifiable_paths(tmp_path):
    world = tmp_path / "workspace"
    world.mkdir()
    (world / "svc-a" / "src").mkdir(parents=True)
    (world / "svc-a" / "src" / "publish.py").write_text(
        "def publish():\n    emit('EventA')\n    return True\n"
    )
    map_root = tmp_path / "map"
    chart = map_root / "chart"
    chart.mkdir(parents=True)
    origin = _origin_with_svc_b(tmp_path, "def x():\n    pass\n")
    (origin / "svc-c").mkdir()
    (origin / "svc-c" / "src").mkdir()
    (origin / "svc-c" / "src" / "x.py").write_text(
        "def x():\n    pass\n    return None\n"
    )
    f_no_source = fact(origin, "svc-c", "consumes_event", "EventA", "svc-c/src/x.py")

    # (a) folder absent from sources entirely
    status = chart_status(chart, world, [f_no_source], fetch=lambda s, p: "text")
    assert status[anchor_key(f_no_source)] == UNVERIFIABLE

    # (b) folder in sources but fetch returns None
    (map_root / "sources.json").write_text(
        json.dumps({"svc-c": {"repo": "acme/svc-c", "branch": "main"}})
    )
    status = chart_status(chart, world, [f_no_source], fetch=lambda s, p: None)
    assert status[anchor_key(f_no_source)] == UNVERIFIABLE


def test_fetch_cache_one_call_per_file(tmp_path):
    world = tmp_path / "workspace"
    world.mkdir()
    map_root = tmp_path / "map"
    chart = map_root / "chart"
    chart.mkdir(parents=True)
    (map_root / "sources.json").write_text(
        json.dumps({"svc-b": {"repo": "acme/svc-b", "branch": "main"}})
    )
    origin = _origin_with_svc_b(
        tmp_path, "def consume():\n    handle('EventA')\n    return True\n"
    )
    f1 = fact(origin, "svc-b", "consumes_event", "EventA", "svc-b/src/consume.py")
    f2 = fact(origin, "svc-b", "also_reads", "EventA", "svc-b/src/consume.py")
    calls: list = []

    def fetch(source, path_in_repo):
        calls.append((source, path_in_repo))

    chart_status(chart, world, [f1, f2], fetch=fetch)
    assert len(calls) == 1


def test_fetch_remote_file_command_shape(monkeypatch):
    captured = {}

    class FakeProc:
        def __init__(self, returncode, stdout):
            self.returncode = returncode
            self.stdout = stdout

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return FakeProc(0, "file text")

    monkeypatch.setattr("cartographer.remote.subprocess.run", fake_run)
    source = {"repo": "acme/x", "branch": "dev", "host": "gh.acme.com"}
    result = fetch_remote_file(source, "src/a.py")
    assert captured["cmd"] == [
        "gh",
        "api",
        "repos/acme/x/contents/src/a.py?ref=dev",
        "-H",
        "Accept: application/vnd.github.raw",
        "--hostname",
        "gh.acme.com",
    ]
    assert result == "file text"

    def fake_run_fail(cmd, **kwargs):
        return FakeProc(1, "")

    monkeypatch.setattr("cartographer.remote.subprocess.run", fake_run_fail)
    assert fetch_remote_file(source, "src/a.py") is None


# --- Fix 2: per-file local/remote decision, sources as fallback ------------


def test_partial_local_checkout_uses_remote(tmp_path):
    """The service's folder exists locally (partial checkout) but the
    pinned file itself does not; sources.json maps the folder. This used
    to be a false DRIFTED (folder present => never consult remote)."""
    world = tmp_path / "workspace"
    (world / "svc-b" / "other").mkdir(parents=True)
    (world / "svc-b" / "other" / "unrelated.py").write_text("x\n")
    origin = _origin_with_svc_b(
        tmp_path, "def consume():\n    handle('EventA')\n    return True\n"
    )
    original_text = (origin / "svc-b" / "src" / "consume.py").read_text()
    f_remote = fact(origin, "svc-b", "consumes_event", "EventA", "svc-b/src/consume.py")
    sources = {"svc-b": {"repo": "acme/svc-b", "branch": "main"}}

    assert fact_status(world, sources, f_remote, fetch=lambda s, p: original_text) == OK
    assert (
        fact_status(
            world, sources, f_remote, fetch=lambda s, p: "def consume():\n    pass\n"
        )
        == DRIFTED
    )


def test_folder_name_collision_with_file_uses_remote(tmp_path):
    """The workspace has a FILE named like the service's folder (not a
    directory); the pinned path under it can never exist locally, so the
    remote path must be taken."""
    world = tmp_path / "workspace"
    world.mkdir()
    (world / "svc-b").write_text("i am a file, not the svc-b checkout\n")
    origin = _origin_with_svc_b(
        tmp_path, "def consume():\n    handle('EventA')\n    return True\n"
    )
    original_text = (origin / "svc-b" / "src" / "consume.py").read_text()
    f_remote = fact(origin, "svc-b", "consumes_event", "EventA", "svc-b/src/consume.py")
    sources = {"svc-b": {"repo": "acme/svc-b", "branch": "main"}}
    calls: list = []

    def fetch(source, path_in_repo):
        calls.append((source, path_in_repo))
        return original_text

    assert fact_status(world, sources, f_remote, fetch=fetch) == OK
    assert calls == [({"repo": "acme/svc-b", "branch": "main"}, "src/consume.py")]


def test_local_file_deleted_no_source_is_drifted(tmp_path):
    """Folder exists locally, the pinned file is gone from it, and there
    is no sources.json entry to fall back on: a locally deleted file is
    drift, as before."""
    world = tmp_path / "workspace"
    (world / "svc-b" / "src").mkdir(parents=True)
    (world / "svc-b" / "src" / "other.py").write_text("y\n")
    origin = _origin_with_svc_b(
        tmp_path, "def consume():\n    handle('EventA')\n    return True\n"
    )
    f_remote = fact(origin, "svc-b", "consumes_event", "EventA", "svc-b/src/consume.py")
    assert fact_status(world, {}, f_remote) == DRIFTED


def test_anchor_key_changes_on_reanchor(tmp_path):
    world = tmp_path / "workspace"
    (world / "svc-b" / "src").mkdir(parents=True)
    (world / "svc-b" / "src" / "consume.py").write_text(
        "def consume():\n    handle('EventA')\n    return True\n"
    )
    f1 = fact(world, "svc-b", "consumes_event", "EventA", "svc-b/src/consume.py")
    f2 = dict(f1)
    f2["anchor"] = dict(f1["anchor"], lines=[2, 2], content_hash="sha256:" + "0" * 64)
    assert anchor_key(f1) != anchor_key(f2)


def test_load_sources_ignores_bad_entries_with_warning(tmp_path, capsys):
    from cartographer.remote import load_sources

    (tmp_path / "sources.json").write_text(
        json.dumps(
            {"svc": "org/svc", "ok": {"repo": "org/ok"}, "norepo": {"branch": "x"}}
        )
    )
    assert load_sources(tmp_path) == {"ok": {"repo": "org/ok"}}
    err = capsys.readouterr().err
    assert "'svc'" in err and "'norepo'" in err and "warning" in err


def test_load_sources_invalid_json_warns(tmp_path, capsys):
    from cartographer.remote import load_sources

    (tmp_path / "sources.json").write_text("{not json")
    assert load_sources(tmp_path) == {}
    assert "invalid JSON" in capsys.readouterr().err
    (tmp_path / "sources.json").write_text("[1, 2]")
    assert load_sources(tmp_path) == {}
    assert "expected a JSON object" in capsys.readouterr().err


def test_string_source_entry_is_unverifiable_not_crash(tmp_path):
    world = write_world(tmp_path)
    chart = write_chart(tmp_path, world)
    facts = json.loads((chart / "flow.json").read_text())
    (world / "svc-b" / "src" / "consume.py").unlink()
    (world / "svc-b").rename(tmp_path / "gone")
    (chart.parent / "sources.json").write_text(json.dumps({"svc-b": "org/svc-b"}))
    status = chart_status(chart, world, facts, fetch=_recording_fetch([]))
    assert status[anchor_key(facts[1])] == UNVERIFIABLE
    assert fetch_remote_file("org/svc-b", "src/consume.py") is None


def test_single_component_path_never_fetches(tmp_path):
    world = tmp_path / "workspace"
    world.mkdir()
    (world / "README.md").write_text("hello\nworld\nagain\n")
    f = fact(world, "root", "reads_file", "README", "README.md")
    (world / "README.md").unlink()
    sources = {"README.md": {"repo": "org/x"}}

    def fetch_never(source, path_in_repo):
        raise AssertionError("must not fetch the repository root")

    assert fact_status(world, sources, f, fetch=fetch_never) == UNVERIFIABLE


def test_fetch_url_encodes_path(monkeypatch):
    import subprocess as _sp

    seen: list = []

    def fake_run(cmd, **kw):
        seen.append(cmd)

        class P:
            returncode = 0
            stdout = "x"

        return P()

    monkeypatch.setattr(_sp, "run", fake_run)
    assert fetch_remote_file({"repo": "o/r"}, "a b/c#d.py") == "x"
    assert "repos/o/r/contents/a%20b/c%23d.py?ref=main" in seen[0][2]
    assert fetch_remote_file({"repo": "o/r"}, "") is None
