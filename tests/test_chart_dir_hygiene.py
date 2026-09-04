"""CAR-37 / CAR-38: an unlistable or empty chart directory is a refusal,
never an empty chart, and seal never clobbers a manifest."""

import json
import os

import pytest

from cartographer.cli import check, init, seal
from cartographer.map_loader import (
    LintError,
    load_chart,
    load_sealed_chart,
    verify_manifest,
)
from cartographer.mcp_server import chart_index, staleness_check
from tests.test_cli import write_chart, write_world
from tests.test_map_loader import _anchored_fact, _write_manifest
from tests.test_mcp_server import _mint

not_root = pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory modes")


@not_root
def test_unlistable_chart_dir_is_a_lint_problem(tmp_path):
    (tmp_path / "s.json").write_text(json.dumps([_anchored_fact()]))
    _write_manifest(tmp_path)
    assert not any("not listable" in p for p in load_chart(tmp_path)[1])
    os.chmod(tmp_path, 0o111)
    try:
        with pytest.raises(LintError) as exc:
            load_sealed_chart(tmp_path)
        assert any("not listable" in p for p in exc.value.problems)
        assert any("not listable" in p for p in load_chart(tmp_path)[1])
    finally:
        os.chmod(tmp_path, 0o755)
    assert not any("not listable" in p for p in load_chart(tmp_path)[1])


def test_manifest_with_no_files_is_a_lint_problem(tmp_path):
    (tmp_path / "chart.manifest").write_text(json.dumps({"files": {}, "fact_count": 0}))
    assert any("no fact files" in p for p in verify_manifest(tmp_path))
    with pytest.raises(LintError):
        load_sealed_chart(tmp_path)


def test_hint_fires_despite_stray_manifest_at_root(tmp_path):
    (tmp_path / "chart").mkdir()
    (tmp_path / "chart.manifest").write_text(json.dumps({"files": {}, "fact_count": 0}))
    with pytest.raises(LintError) as exc:
        load_sealed_chart(tmp_path)
    assert any("did you mean" in p and "chart" in p for p in exc.value.problems)


def test_seal_refuses_directory_without_fact_files(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(SystemExit) as exc:
        seal(empty)
    assert "nothing to seal" in str(exc.value)
    assert not (empty / "chart.manifest").exists()


def test_init_scaffold_is_an_empty_chart_that_loads(tmp_path):
    target = tmp_path / "m"
    init(target)
    assert load_sealed_chart(target / "chart") == []


@not_root
def test_unlistable_chart_is_refused_and_manifest_survives(tmp_path, capsys):
    world = write_world(tmp_path)
    chart = write_chart(tmp_path, world)
    seal(chart)
    before = (chart / "chart.manifest").read_bytes()
    os.chmod(chart, 0o111)
    try:
        assert check(chart, world) == 2
        assert "not listable" in capsys.readouterr().err
        with pytest.raises(SystemExit) as exc:
            seal(chart)
        assert "not listable" in str(exc.value)
    finally:
        os.chmod(chart, 0o755)
    assert (chart / "chart.manifest").read_bytes() == before
    assert check(chart, world) == 0


@not_root
def test_tools_refuse_unlistable_chart(tmp_path):
    chart, world = _mint(tmp_path)
    os.chmod(chart, 0o111)
    try:
        with pytest.raises(LintError):
            chart_index(chart, world, {})
        with pytest.raises(LintError):
            staleness_check(chart, world, {})
    finally:
        os.chmod(chart, 0o755)
