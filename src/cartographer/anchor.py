# src/cartographer/anchor.py
#
# Source anchors and the deterministic anchor ladder.
# Pointer + fingerprint, never a copy of source. Stdlib only; no clock —
# timestamps and revision ids are always passed in.
import hashlib
from dataclasses import dataclass
from dataclasses import field as dc_field
from pathlib import Path


def normalize(text: str) -> str:
    """Line endings + trailing whitespace only. YAML indentation is
    semantic, so leading whitespace is untouched (spec §6)."""
    lines = text.replace("\r\n", "\n").split("\n")
    out = "\n".join(ln.rstrip() for ln in lines)
    return out.rstrip("\n")


def excerpt_hash(text: str) -> str:
    digest = hashlib.sha256(normalize(text).encode("utf-8", errors="surrogateescape"))
    return "sha256:" + digest.hexdigest()


def read_pinned_text(path: Path) -> str | None:
    """Text of a pinned file, or None when the path is not a readable
    regular file. Undecodable bytes survive via surrogateescape so a
    non-UTF-8 file gets a verdict (its hash will not match) rather than
    an exception."""
    p = Path(path)
    if not p.is_file():
        return None
    try:
        return p.read_bytes().decode("utf-8", errors="surrogateescape")
    except OSError:
        return None


def _slice(text: str, lines: tuple[int, int]) -> str:
    body = normalize(text).split("\n")
    lo, hi = lines
    if lo < 1 or hi < lo or hi > len(body):
        raise ValueError(
            f"line range {lo}-{hi} is outside the file ({len(body)} lines after "
            "normalization)"
        )
    return "\n".join(body[lo - 1 : hi])


def make_code_anchor(
    world: Path, path: str, lines: tuple[int, int] | None, revision: str, at: str
) -> dict:
    text = read_pinned_text(Path(world) / path)
    if text is None:
        raise FileNotFoundError(f"not a readable file: {Path(world) / path}")
    excerpt = text if lines is None else _slice(text, lines)
    if normalize(excerpt) == "":
        raise ValueError(
            f"refusing to anchor an empty excerpt ({path} lines {lines}): "
            "nothing to pin"
        )
    return {
        "kind": "code",
        "path": path,
        "lines": list(lines) if lines else None,
        "content_hash": excerpt_hash(excerpt),
        "revision": revision,
        "verified_at": at,
    }


def make_external_anchor(world: Path, path: str, revision: str, at: str) -> dict:
    a = make_code_anchor(world, path, None, revision, at)
    a["kind"] = "external"
    a["retrieved_at"] = at
    return a


def verify_excerpt(text: str, anchor: dict) -> bool:
    lines = anchor["lines"]
    try:
        excerpt = text if lines is None else _slice(text, (lines[0], lines[1]))
    except ValueError:
        return False
    if normalize(excerpt) == "":
        return False
    return excerpt_hash(excerpt) == anchor["content_hash"]


def verify_anchor(world: Path, anchor: dict) -> bool:
    text = read_pinned_text(Path(world) / anchor["path"])
    if text is None:
        return False
    return verify_excerpt(text, anchor)


def identity(fact: dict) -> tuple:
    return (fact["subject"], fact["predicate"], fact["object"], fact["scope"])


@dataclass
class Disposition:
    tier: str  # "L1" | "L2" | "L3" | "L4"
    new_anchor: dict | None = None
    ambiguous: bool = False
    # rename-follow found a twin file but could not place the excerpt in it:
    # the fact's own path is gone from the world, so any widened re-anchor
    # must be computed against this twin, never the deleted path.
    relocate_to: str | None = None
    reasons: list[str] = dc_field(default_factory=list)


def _find_excerpt(world: Path, path: str, anchor: dict) -> list[tuple[int, int]]:
    """All line ranges in `path` whose normalized excerpt hashes to the
    anchor's content_hash. Whole-file anchors return [] (nowhere to
    relocate a whole file within itself)."""
    if anchor["lines"] is None:
        return []
    lo, hi = anchor["lines"]
    span = hi - lo + 1
    body = normalize(read_pinned_text(Path(world) / path) or "").split("\n")
    hits: list[tuple[int, int]] = []
    for start in range(1, len(body) - span + 2):
        window = "\n".join(body[start - 1 : start - 1 + span])
        if excerpt_hash(window) == anchor["content_hash"]:
            hits.append((start, start + span - 1))
    return hits


def _rebump(anchor: dict, revision: str, at: str) -> dict:
    out = dict(anchor)
    out["revision"] = revision
    out["verified_at"] = at
    return out


def _relocated(
    anchor: dict, path: str, lines: tuple[int, int], revision: str, at: str
) -> dict:
    out = _rebump(anchor, revision, at)
    out["path"] = path
    out["lines"] = [lines[0], lines[1]]
    return out


def dispose(
    prev_world: Path,
    world: Path,
    fact: dict,
    changed: set[str],
    deleted: set[str],
    created: set[str],
    revision: str,
    at: str,
) -> Disposition:
    anchor = fact["anchor"]
    path = anchor["path"]

    if path in deleted:
        # rename-follow: a created file with an identical whole-file hash
        prev_text = read_pinned_text(Path(prev_world) / path)
        twins: list[str] = []
        if prev_text is not None:
            old_hash = excerpt_hash(prev_text)
            for c in sorted(created):
                text = read_pinned_text(Path(world) / c)
                if text is not None and excerpt_hash(text) == old_hash:
                    twins.append(c)
        if len(twins) == 1:
            twin = twins[0]
            if anchor["lines"] is None:
                return Disposition(
                    "L2",
                    _relocated_whole(anchor, twin, revision, at),
                    reasons=[f"file renamed to {twin}"],
                )
            hits = _find_excerpt(world, twin, anchor)
            if len(hits) == 1:
                return Disposition(
                    "L2",
                    _relocated(anchor, twin, hits[0], revision, at),
                    reasons=[f"file renamed to {twin}"],
                )
            return Disposition(
                "L3",
                ambiguous=len(hits) > 1,
                relocate_to=twin,
                reasons=[f"renamed to {twin} but excerpt not uniquely found"],
            )
        return Disposition("L4", reasons=["anchored file deleted, no identical twin"])

    if path not in changed:
        return Disposition("L1", reasons=["file untouched"])

    if verify_anchor(world, anchor):
        return Disposition("L1", _rebump(anchor, revision, at), reasons=["hash intact"])

    hits = _find_excerpt(world, path, anchor)
    if len(hits) == 1:
        return Disposition(
            "L2", _relocated(anchor, path, hits[0], revision, at), reasons=["relocated"]
        )
    if len(hits) > 1:
        return Disposition(
            "L3", ambiguous=True, reasons=[f"excerpt found at {len(hits)} locations"]
        )
    return Disposition("L3", reasons=["excerpt no longer present"])


def _relocated_whole(anchor: dict, path: str, revision: str, at: str) -> dict:
    out = _rebump(anchor, revision, at)
    out["path"] = path
    return out


def run_ladder(
    prev_world: Path,
    world: Path,
    facts: list[dict],
    changed: set[str],
    deleted: set[str],
    created: set[str],
    revision: str,
    at: str,
) -> dict[tuple, Disposition]:
    return {
        identity(f): dispose(
            prev_world, world, f, changed, deleted, created, revision, at
        )
        for f in facts
    }
