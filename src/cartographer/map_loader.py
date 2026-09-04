# src/cartographer/map_loader.py
#
# Union-of-scopes map loader with load-time lints. Stdlib only.
# Lint failure ⇒ no facts served — a severed or self-contradicting map must
# never silently answer.
import hashlib
import json
import re
from pathlib import Path

from cartographer.anchor import excerpt_hash

PRODUCER_PREDICATES = frozenset({"emits_event", "writes_table", "writes_file"})
CONSUMER_PREDICATES = frozenset({"consumes_event", "reads_view", "reads_file"})
_REQUIRED_FIELDS = ("subject", "predicate", "object", "path", "scope", "owner")

_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_EMPTY_HASH = excerpt_hash("")


def _valid_lines(lines: object) -> bool:
    return (
        isinstance(lines, list)
        and len(lines) == 2
        and all(isinstance(x, int) and not isinstance(x, bool) for x in lines)
        and 1 <= lines[0] <= lines[1]
    )


def _anchor_problems(f: dict) -> list[str]:
    """Structural checks on a fact's anchor. Serving code indexes
    anchor["path"], anchor["lines"] and anchor["content_hash"] directly, so
    a fact that fails here would crash a tool call instead of failing
    closed with a lint verdict."""
    who = f.get("subject", "?")
    anchor = f.get("anchor")
    if not isinstance(anchor, dict):
        return [f"fact missing anchor: {who}"]
    out: list[str] = []
    if not isinstance(anchor.get("path"), str) or not anchor.get("path"):
        out.append(f"anchor missing path: {who}")
    h = anchor.get("content_hash")
    if h is None:
        out.append(f"anchor missing content_hash: {who}")
    elif not isinstance(h, str) or not _SHA256_RE.fullmatch(h):
        out.append(f"anchor content_hash malformed (expected sha256:<64 hex>): {who}")
    elif h == _EMPTY_HASH:
        out.append(f"anchor pins an empty excerpt (nothing to verify): {who}")
    if "lines" not in anchor:
        out.append(f"anchor missing lines (use null for a whole-file anchor): {who}")
    elif anchor["lines"] is not None and not _valid_lines(anchor["lines"]):
        out.append(
            f"anchor lines must be [lo, hi] with 1 <= lo <= hi, "
            f"got {anchor['lines']!r}: {who}"
        )
    return out


class LintError(Exception):
    def __init__(self, problems: list[str]):
        self.problems = problems
        super().__init__("map lint failed:\n" + "\n".join(problems))


def lint_facts(facts: list[dict]) -> list[str]:
    problems: list[str] = []

    for f in facts:
        if not isinstance(f, dict):
            problems.append(
                f"fact must be a JSON object, got {type(f).__name__}: {f!r}"
            )
            continue
        missing = [
            k for k in _REQUIRED_FIELDS if not isinstance(f.get(k), str) or not f[k]
        ]
        if missing:
            problems.append(f"fact missing or non-string fields {missing}: {f}")
        problems += _anchor_problems(f)
    if problems:
        # Structurally invalid facts preclude the semantic lints below (they
        # index required keys directly). Fail closed with what we know.
        return problems

    for f in facts:
        for p in {f["path"], (f.get("anchor") or {}).get("path") or f["path"]}:
            parts = Path(p).parts
            if not parts or Path(p).is_absolute() or ".." in parts:
                problems.append(f"unsafe path {p!r} (absolute or ..): {f['subject']}")

    seen: dict[tuple, str] = {}
    for f in facts:
        ident = (f["subject"], f["predicate"], f["object"], f["scope"])
        if ident in seen:
            problems.append(f"duplicate identity: {ident}")
        seen[ident] = f["path"]
        anchor = f.get("anchor")
        if anchor is not None and anchor.get("path") != f["path"]:
            problems.append(
                f"anchor path mismatch: fact path {f['path']!r} vs "
                f"anchor path {anchor.get('path')!r} on {ident}"
            )

    # Dangling boundary: an artifact consumed somewhere but produced/derived
    # nowhere. facts marked "external": true are declared tier-2 edges.
    produced = {f["object"] for f in facts if f["predicate"] in PRODUCER_PREDICATES}
    produced |= {f["subject"] for f in facts if f["predicate"] == "derived_from"}
    for f in facts:
        if (
            f["predicate"] in CONSUMER_PREDICATES
            and f["object"] not in produced
            and not f.get("external")
        ):
            problems.append(
                f"dangling boundary: {f['subject']} consumes {f['object']!r} "
                "but no scope produces it (mark external: true if tier-2)"
            )

    # Contradiction: a promises_<dim> on an artifact vs an assumes_<dim> held
    # by one of that artifact's direct consumers, with different values.
    promises = [
        (f["subject"], f["predicate"].removeprefix("promises_"), f["object"])
        for f in facts
        if f["predicate"].startswith("promises_")
    ]
    consumers_of: dict[str, set[str]] = {}
    for f in facts:
        if f["predicate"] in CONSUMER_PREDICATES:
            consumers_of.setdefault(f["object"], set()).add(f["subject"])
    for artifact, dim, promised in promises:
        for f in facts:
            if (
                f["predicate"] == f"assumes_{dim}"
                and f["subject"] in consumers_of.get(artifact, set())
                and f["object"] != promised
            ):
                problems.append(
                    f"contradiction on {artifact!r} ({dim}): promised "
                    f"{promised!r} but {f['subject']} assumes {f['object']!r} "
                    "— live treaty violation"
                )
    return problems


MANIFEST_NAME = "chart.manifest"


def _fact_files(chart_dir: Path) -> list[Path]:
    """Fact files are chart/*.json; dotfiles such as .mcp.json never are."""
    return sorted(
        p
        for p in Path(chart_dir).glob("*.json")
        if not p.name.startswith(".") and p.is_file()
    )


def _read_fact_files(chart_dir: Path) -> tuple[list[dict], list[str]]:
    """Read every fact file. Parse failures, non-list files and non-object
    entries become lint problems instead of exceptions, so a curator sees a
    verdict and a server never partially loads."""
    facts: list[dict] = []
    problems: list[str] = []
    for p in sorted(
        p for p in Path(chart_dir).glob("*.json") if not p.name.startswith(".")
    ):
        if not p.is_file():
            problems.append(f"{p.name}: not a regular file")
            continue
        try:
            loaded = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError, RecursionError) as e:
            problems.append(f"invalid JSON in {p.name}: {e}")
            continue
        except OSError as e:
            problems.append(f"unreadable fact file {p.name}: {e}")
            continue
        if not isinstance(loaded, list):
            problems.append(f"{p.name}: top level must be a JSON list of facts")
            continue
        for i, item in enumerate(loaded):
            if not isinstance(item, dict):
                problems.append(f"{p.name}[{i}]: fact must be a JSON object")
                continue
            facts.append(item)
    return facts, problems


def _not_a_chart_hint(chart_dir: Path) -> list[str]:
    """Pointed at a map root instead of its chart/ subdirectory."""
    chart_dir = Path(chart_dir)
    nested = chart_dir / "chart"
    if not (chart_dir / MANIFEST_NAME).exists() and nested.is_dir():
        return [
            (
                f"not a chart directory (expected {MANIFEST_NAME} and *.json fact "
                f"files); did you mean {nested}?"
            )
        ]
    return []


def verify_manifest(chart_dir: Path) -> list[str]:
    chart_dir = Path(chart_dir)
    mpath = chart_dir / MANIFEST_NAME
    if not mpath.exists():
        return [f"chart manifest missing: {mpath}"]
    if not mpath.is_file():
        return [f"chart manifest is not a regular file: {mpath}"]
    try:
        manifest = json.loads(mpath.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError, RecursionError) as e:
        return [f"chart manifest unreadable: {e}"]
    if not isinstance(manifest, dict) or not isinstance(manifest.get("files"), dict):
        return ['chart manifest malformed: expected {"files": {...}, "fact_count": N}']
    problems: list[str] = []
    fact_files = {p.name for p in _fact_files(chart_dir)}
    listed = set(manifest["files"])
    for name in sorted(fact_files - listed):
        problems.append(f"chart file not in manifest: {name}")
    count = 0
    for name, digest in manifest["files"].items():
        if (
            not isinstance(name, str)
            or "/" in name
            or "\\" in name
            or name.startswith(".")
            or not name.endswith(".json")
            or not isinstance(digest, str)
        ):
            problems.append(f"manifest lists invalid entry: {name!r}")
            continue
        p = chart_dir / name
        if not p.is_file():
            problems.append(f"manifest lists missing file: {name}")
            continue
        try:
            raw = p.read_bytes()
        except OSError as e:
            problems.append(f"unreadable chart file {name}: {e}")
            continue
        actual = "sha256:" + hashlib.sha256(raw).hexdigest()
        if actual != digest:
            problems.append(f"manifest hash mismatch on {name}")
        try:
            loaded = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError, RecursionError):
            continue  # reported by _read_fact_files
        if isinstance(loaded, list):
            count += len(loaded)
    expected = manifest.get("fact_count")
    if isinstance(expected, bool) or not isinstance(expected, int) or expected != count:
        problems.append(f"manifest fact_count {expected!r} != actual {count}")
    return problems


def load_chart(
    chart_dir: Path, *, require_manifest: bool = True
) -> tuple[list[dict], list[str]]:
    """Maintenance-path load: NEVER raises on lint problems.

    The adjudicator must be able to read a linting chart (e.g. a live
    treaty-window contradiction) in order to fix it; only SERVING is
    fail-closed (load_sealed_chart)."""
    chart_dir = Path(chart_dir)
    problems = _not_a_chart_hint(chart_dir)
    facts, read_problems = _read_fact_files(chart_dir)
    problems += read_problems
    if require_manifest or (chart_dir / MANIFEST_NAME).exists():
        problems += verify_manifest(chart_dir)
    problems += lint_facts(facts)
    return facts, problems


def load_sealed_chart(maps_dir: Path) -> list[dict]:
    maps_dir = Path(maps_dir)
    problems = _not_a_chart_hint(maps_dir)
    facts, read_problems = _read_fact_files(maps_dir)
    problems += read_problems
    # Serving fail-closed includes ABSENCE: a deleted manifest must not
    # silently bypass verification.
    problems += verify_manifest(maps_dir)
    problems += lint_facts(facts)
    if problems:
        raise LintError(problems)
    return facts
