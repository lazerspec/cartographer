# Deterministic chart context renderer. Stable ordering by (scope, subject,
# predicate, object), byte-identical output for identical chart state, no
# LLM anywhere, serving fail-closed via load_sealed_chart.
import hashlib
from pathlib import Path

from .map_loader import load_sealed_chart

_PROVENANCE_KEYS = (
    "asserted_by",
    "asserted_at",
    "asserted_via",
    "pr_ref",
    "decision_ref",
    "issue_ref",
)

HEADER = (
    "CARTOGRAPHER CHART — curated cross-module behavioral facts. Each line: "
    "subject --[predicate]--> object  (scope; evidence: path[:lines]; provenance). "
    "Facts are source-anchored assertions, not derived from this session."
)


def _fact_line(f: dict) -> str:
    a = f["anchor"]
    loc = a["path"]
    if a.get("lines"):
        loc += f":{a['lines'][0]}-{a['lines'][1]}"
    md = f.get("metadata") or {}
    prov = " ".join(f"{k}={md[k]}" for k in _PROVENANCE_KEYS if md.get(k))
    line = (
        f"{f['subject']} --[{f['predicate']}]--> {f['object']}"
        f"  (scope={f['scope']}; evidence={loc}"
    )
    return line + (f"; {prov})" if prov else ")")


def render_chart_context(chart_dir: Path) -> str:
    facts = load_sealed_chart(Path(chart_dir))  # raises LintError — fail closed
    ordered = sorted(
        facts, key=lambda f: (f["scope"], f["subject"], f["predicate"], f["object"])
    )
    return HEADER + "\n" + "\n".join(_fact_line(f) for f in ordered)


def context_hash(block: str) -> str:
    return "sha256:" + hashlib.sha256(block.encode()).hexdigest()


def render_core_context(chart_dir: Path) -> str:
    """Core-tier render: identical to render_chart_context but excluding
    facts tagged tier == "derived". Untagged facts serve as core.
    render_chart_context above is a frozen contract and is NOT modified."""
    facts = [
        f for f in load_sealed_chart(Path(chart_dir)) if f.get("tier") != "derived"
    ]
    ordered = sorted(
        facts, key=lambda f: (f["scope"], f["subject"], f["predicate"], f["object"])
    )
    return HEADER + "\n" + "\n".join(_fact_line(f) for f in ordered)
