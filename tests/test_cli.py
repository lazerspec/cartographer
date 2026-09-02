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
        "sources.json",
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


def _git(cwd, *args):
    import subprocess

    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def test_pull_map_repo_pulls_new_commit(tmp_path):
    from cartographer.cli import pull_map_repo

    origin = tmp_path / "origin.git"
    origin.mkdir()
    _git(origin, "init", "--bare", "-b", "main")
    seed = tmp_path / "seed"
    seed.mkdir()
    _git(seed, "init", "-b", "main")
    _git(seed, "config", "user.email", "t@example.com")
    _git(seed, "config", "user.name", "t")
    (seed / "chart").mkdir()
    (seed / "chart" / "facts.json").write_text("[]\n")
    _git(seed, "add", "-A")
    _git(seed, "commit", "-m", "one")
    _git(seed, "remote", "add", "origin", str(origin))
    _git(seed, "push", "-u", "origin", "main")
    clone = tmp_path / "clone"
    _git(tmp_path, "clone", str(origin), "clone")
    (seed / "chart" / "facts.json").write_text("[]\n\n")
    _git(seed, "add", "-A")
    _git(seed, "commit", "-m", "two")
    _git(seed, "push")
    assert pull_map_repo(clone / "chart") is True
    assert (clone / "chart" / "facts.json").read_text() == "[]\n\n"


def test_pull_map_repo_fails_soft_without_remote(tmp_path, capsys):
    from cartographer.cli import pull_map_repo

    repo = tmp_path / "solo"
    (repo / "chart").mkdir(parents=True)
    _git(repo, "init", "-b", "main")
    assert pull_map_repo(repo / "chart") is False
    assert "serving local copy" in capsys.readouterr().err


def test_pull_map_repo_fails_soft_outside_git(tmp_path, capsys):
    from cartographer.cli import pull_map_repo

    (tmp_path / "chart").mkdir()
    assert pull_map_repo(tmp_path / "chart") is False
    assert "serving local copy" in capsys.readouterr().err


def test_serve_startup_notice_on_stderr(tmp_path, capsys):
    from cartographer.cli import startup_staleness_notice

    world = write_world(tmp_path)
    chart = write_chart(tmp_path, world)
    seal(chart)

    startup_staleness_notice(chart, world)
    assert capsys.readouterr().err == ""

    target = world / "svc-b" / "src" / "consume.py"
    target.write_text("def consume():\n    handle('EventB')\n    return True\n")
    startup_staleness_notice(chart, world)
    assert "WARNING: 1 of 2" in capsys.readouterr().err

    startup_staleness_notice(tmp_path / "nope", world)
    assert "startup staleness check skipped" in capsys.readouterr().err


def _write_remote_chart(tmp_path: Path):
    """A map with one locally-checked-out service (svc-a) and one that is
    not (svc-b); svc-b's original file text is captured before the local
    copy is removed, so a fake fetch can return it (or a modified version)
    without any network."""
    world = write_world(tmp_path)
    chart = write_chart(tmp_path, world)
    seal(chart)
    original_text = (world / "svc-b" / "src" / "consume.py").read_text()
    import shutil

    shutil.rmtree(world / "svc-b")
    map_root = chart.parent
    (map_root / "sources.json").write_text(
        json.dumps({"svc-b": {"repo": "acme/svc-b", "branch": "main"}})
    )
    return chart, world, original_text


def test_check_exit_codes_remote(tmp_path, capsys):
    chart, world, original_text = _write_remote_chart(tmp_path)

    def fetch_clean(source, path_in_repo):
        return original_text

    assert check(chart, world, fetch=fetch_clean) == 0

    def fetch_drifted(source, path_in_repo):
        return "def consume():\n    handle('EventB')\n    return True\n"

    assert check(chart, world, fetch=fetch_drifted) == 1
    out = capsys.readouterr().out
    assert "svc-b" in out and "DRIFTED" in out


def test_check_exit_codes_unverifiable(tmp_path, capsys):
    chart, world, _original_text = _write_remote_chart(tmp_path)
    # remove the sources.json entry entirely: svc-b becomes unverifiable
    map_root = chart.parent
    (map_root / "sources.json").write_text("{}\n")

    def fetch_never(source, path_in_repo):
        raise AssertionError("fetch should not be called: no sources entry")

    assert check(chart, world, strict=False, fetch=fetch_never) == 0
    out = capsys.readouterr().out
    assert "UNVERIFIABLE" in out

    assert check(chart, world, strict=True, fetch=fetch_never) == 1


def test_check_counts_facts_not_anchor_keys(tmp_path, capsys):
    """Two facts sharing one anchor, both drifted: `check` must print
    'verified 0/2' and '2 DRIFTED', not 'verified 1/2' / '1 DRIFTED'."""
    world = write_world(tmp_path)
    anchor_fact = fact(world, "svc-a", "emits_event", "EventA", "svc-a/src/publish.py")
    shared_anchor = dict(anchor_fact["anchor"])
    f1 = dict(anchor_fact)
    f2 = dict(
        anchor_fact, predicate="writes_table", object="tbl_a", anchor=shared_anchor
    )
    chart = tmp_path / "map" / "chart"
    chart.mkdir(parents=True)
    (chart / "flow.json").write_text(json.dumps([f1, f2], indent=1))
    seal(chart)

    target = world / "svc-a" / "src" / "publish.py"
    target.write_text("def publish():\n    emit('EventB')\n    return True\n")

    assert check(chart, world) == 1
    out = capsys.readouterr().out
    assert "verified 0/2" in out
    assert "2 DRIFTED" in out


def test_init_mcp_json_uses_pull():
    from importlib import resources

    text = resources.files("cartographer").joinpath("templates/mcp.json").read_text()
    args = json.loads(text)["mcpServers"]["cartographer"]["args"]
    assert args[:2] == ["serve", "--pull"]


def test_check_missing_world_exit_2(tmp_path, capsys):
    world = write_world(tmp_path)
    chart = write_chart(tmp_path, world)
    seal(chart)
    assert check(chart, tmp_path / "does-not-exist") == 2
    err = capsys.readouterr().err
    assert "world directory not found" in err
    assert main(["check", str(chart), "--world", str(tmp_path / "nope")]) == 2


def test_check_zero_verified_is_failure(tmp_path, capsys):
    world = write_world(tmp_path)
    chart = write_chart(tmp_path, world)
    seal(chart)
    empty_world = tmp_path / "empty"
    empty_world.mkdir()

    def fetch_never(source, path_in_repo):
        raise AssertionError("no sources.json: fetch must not be called")

    assert check(chart, empty_world, fetch=fetch_never) == 1
    out = capsys.readouterr().out
    assert "verified 0/2" in out
    assert "0 facts verified" in out
    # a genuinely empty chart is not a failure: nothing to verify, nothing wrong
    assert check(chart, world) == 0


def test_check_refused_message_names_seal(tmp_path, capsys):
    world = write_world(tmp_path)
    chart = write_chart(tmp_path, world)
    seal(chart)
    facts = json.loads((chart / "flow.json").read_text())
    facts[0]["object"] = "EventZ"
    (chart / "flow.json").write_text(json.dumps(facts, indent=1))
    assert check(chart, world) == 2
    assert "cartographer seal" in capsys.readouterr().err


def test_drift_template_uses_strict():
    from importlib.resources import files

    text = (files("cartographer") / "templates" / "drift-example.yml").read_text()
    assert "cartographer check map/chart --world . --strict" in text


def test_all_drifted_does_not_claim_nothing_was_checked(tmp_path, capsys):
    world = write_world(tmp_path)
    chart = write_chart(tmp_path, world)
    seal(chart)
    for rel in ("svc-a/src/publish.py", "svc-b/src/consume.py"):
        (world / rel).write_text("completely rewritten\n")
    assert check(chart, world) == 1
    out = capsys.readouterr().out
    assert "2 DRIFTED" in out
    assert "nothing could be checked" not in out


def test_check_exit_2_on_corrupt_manifest_or_fact_json(tmp_path, capsys):
    world = write_world(tmp_path)
    chart = write_chart(tmp_path, world)
    seal(chart)
    good_manifest = (chart / "chart.manifest").read_text()
    (chart / "chart.manifest").write_text("{not json")
    assert check(chart, world) == 2
    assert "CHART REFUSED" in capsys.readouterr().err
    (chart / "chart.manifest").write_text(good_manifest)
    (chart / "flow.json").write_text("[{broken")
    assert check(chart, world) == 2
    assert "invalid JSON in flow.json" in capsys.readouterr().err


def test_check_and_seal_refuse_map_root(tmp_path, capsys):
    world = write_world(tmp_path)
    chart = write_chart(tmp_path, world)
    seal(chart)
    map_root = chart.parent
    (map_root / ".mcp.json").write_text(json.dumps({"mcpServers": {}}))
    (map_root / "sources.json").write_text("{}")
    assert check(map_root, world) == 2
    err = capsys.readouterr().err
    assert "did you mean" in err and str(chart) in err
    with pytest.raises(SystemExit) as exc:
        seal(map_root)
    assert "did you mean" in str(exc.value)
    # the real chart is untouched by the misdirected seal
    assert check(chart, world) == 0


def test_seal_refuses_when_manifest_path_is_a_directory(tmp_path):
    world = write_world(tmp_path)
    chart = write_chart(tmp_path, world)
    (chart / "chart.manifest").mkdir()
    with pytest.raises(SystemExit) as exc:
        seal(chart)
    assert "chart.manifest" in str(exc.value)
    assert (chart / "chart.manifest").is_dir()  # nothing overwritten
