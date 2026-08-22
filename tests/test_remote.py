import json
from pathlib import Path

from cartographer.remote import (
    DRIFTED,
    OK,
    UNVERIFIABLE,
    chart_status,
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
    from cartographer.anchor import identity

    assert status[identity(facts[0])] == OK
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

    from cartographer.anchor import identity

    # world (unlike origin) has no svc-b checkout, so this exercises the
    # remote path
    status_ok = chart_status(chart, world, [f_local, f_remote], fetch=fetch_ok)
    assert status_ok[identity(f_remote)] == OK

    def fetch_drifted(source, path_in_repo):
        return "def consume():\n    handle('EventB')\n    return True\n"

    status_drifted = chart_status(chart, world, [f_remote], fetch=fetch_drifted)
    assert status_drifted[identity(f_remote)] == DRIFTED


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
    (origin / "svc-c" / "src" / "x.py").write_text("def x():\n    pass\n")
    f_no_source = fact(origin, "svc-c", "consumes_event", "EventA", "svc-c/src/x.py")
    from cartographer.anchor import identity

    # (a) folder absent from sources entirely
    status = chart_status(chart, world, [f_no_source], fetch=lambda s, p: "text")
    assert status[identity(f_no_source)] == UNVERIFIABLE

    # (b) folder in sources but fetch returns None
    (map_root / "sources.json").write_text(
        json.dumps({"svc-c": {"repo": "acme/svc-c", "branch": "main"}})
    )
    status = chart_status(chart, world, [f_no_source], fetch=lambda s, p: None)
    assert status[identity(f_no_source)] == UNVERIFIABLE


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
