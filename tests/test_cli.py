import json
from pathlib import Path

import pytest

from cartographer.anchor import make_code_anchor
from cartographer.cli import check, main, seal
from cartographer.map_loader import load_sealed_chart


def write_world(tmp_path: Path) -> Path:
    world = tmp_path / "workspace"
    (world / "svc-a" / "src").mkdir(parents=True)
    (world / "svc-b" / "src").mkdir(parents=True)
    (world / "svc-a" / "src" / "publish.py").write_text(
        "def publish():\n    emit('EventA')\n    return True\n"
    )
    (world / "svc-b" / "src" / "consume.py").write_text(
        "def consume():\n    handle('EventA')\n    return True\n"
    )
    return world


def fact(world: Path, subject: str, predicate: str, obj: str, path: str) -> dict:
    return {
        "subject": subject,
        "predicate": predicate,
        "object": obj,
        "scope": subject,
        "owner": "team-x",
        "path": path,
        "anchor": make_code_anchor(world, path, (1, 3), "r1", "2026-08-21"),
    }


def write_chart(tmp_path: Path, world: Path) -> Path:
    chart = tmp_path / "map" / "chart"
    chart.mkdir(parents=True)
    facts = [
        fact(world, "svc-a", "emits_event", "EventA", "svc-a/src/publish.py"),
        fact(world, "svc-b", "consumes_event", "EventA", "svc-b/src/consume.py"),
    ]
    (chart / "flow.json").write_text(json.dumps(facts, indent=1))
    return chart


def test_seal_then_load(tmp_path):
    world = write_world(tmp_path)
    chart = write_chart(tmp_path, world)
    msg = seal(chart)
    assert "2 facts" in msg
    assert load_sealed_chart(chart)  # fail-closed loader accepts the sealed chart


def test_seal_refuses_lint_failure(tmp_path):
    chart = tmp_path / "chart"
    chart.mkdir()
    (chart / "bad.json").write_text(json.dumps([{"subject": "x"}]))
    with pytest.raises(SystemExit):
        seal(chart)


def test_check_clean_then_drift_then_restore(tmp_path, capsys):
    world = write_world(tmp_path)
    chart = write_chart(tmp_path, world)
    seal(chart)
    assert check(chart, world) == 0
    target = world / "svc-b" / "src" / "consume.py"
    original = target.read_text()
    target.write_text("def consume():\n    handle('EventB')\n    return True\n")
    assert check(chart, world) == 1
    out = capsys.readouterr().out
    assert "svc-b" in out and "DRIFTED" in out
    assert "svc-a --[emits_event]" not in out  # only the changed service flags
    target.write_text(original)
    assert check(chart, world) == 0


def test_check_refuses_tampered_chart(tmp_path, capsys):
    world = write_world(tmp_path)
    chart = write_chart(tmp_path, world)
    seal(chart)
    facts = json.loads((chart / "flow.json").read_text())
    facts[0]["object"] = "EventZ"
    (chart / "flow.json").write_text(json.dumps(facts, indent=1))
    assert check(chart, world) == 2


def test_main_seal_and_check(tmp_path):
    world = write_world(tmp_path)
    chart = write_chart(tmp_path, world)
    assert main(["seal", str(chart)]) == 0
    assert main(["check", str(chart), "--world", str(world)]) == 0


def test_init_scaffold(tmp_path):
    from cartographer.cli import init

    target = tmp_path / "my-map"
    assert init(target) == 0
    for rel in [
        "chart/facts.json",
        "chart/chart.manifest",
        "CLAUDE.md",
        "README.md",
        ".mcp.json",
        ".gitignore",
        ".github/workflows/drift-example.yml",
    ]:
        assert (target / rel).exists(), rel
    assert load_sealed_chart(target / "chart") == []  # empty chart serves fail-closed
    mcp = json.loads((target / ".mcp.json").read_text())
    assert mcp["mcpServers"]["cartographer"]["command"] == "cartographer"


def test_init_refuses_non_empty(tmp_path):
    from cartographer.cli import init

    target = tmp_path / "occupied"
    target.mkdir()
    (target / "something.txt").write_text("hi")
    with pytest.raises(SystemExit):
        init(target)


def test_templates_have_no_em_dashes():
    from importlib import resources

    tdir = resources.files("cartographer").joinpath("templates")
    banned = ["—"]
    for name in [
        "README.md",
        "CLAUDE.md",
        "mcp.json",
        "gitignore",
        "drift-example.yml",
    ]:
        text = tdir.joinpath(name).read_text().lower()
        for token in banned:
            assert token not in text, f"{token!r} in template {name}"
