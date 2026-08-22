# Curation guide for this map repo

You are helping a domain expert curate a map of cross-service behavior. The
expert is the authority on what is true; the code is the authority on where
it is written down. Your job is to capture their knowledge as verifiable
facts. Follow these rules exactly.

## What a good fact is

A fact is one cross-service behavioral claim that a text search could not
reveal: an event one service publishes and another consumes, an ordering one
side silently assumes, a file or table written here and read there. If a
plain grep of one repo would surface the relationship, it is usually not
worth curating.

## The fact format

Facts live in `chart/*.json`, each file a JSON list. Every fact needs:

    {
      "subject": "orders-svc",
      "predicate": "emits_event",
      "object": "OrderShipped",
      "scope": "orders",
      "owner": "team-orders",
      "path": "orders-svc/src/events/publish.py",
      "anchor": { ...made by the anchor tool, see below... },
      "metadata": {"asserted_by": "<expert name>", "asserted_at": "YYYY-MM-DD"}
    }

- `subject` acts on `object`; `predicate` names the relationship.
- Producer predicates: `emits_event`, `writes_table`, `writes_file`.
  Consumer predicates: `consumes_event`, `reads_view`, `reads_file`.
  Assumption pairs: `promises_<dimension>` on the producer side and
  `assumes_<dimension>` on the consumer side (for example
  `promises_ordering` / `assumes_ordering`); the loader flags mismatched
  values as a contradiction. Other predicates are allowed when none of
  these fit; use lowercase_with_underscores.
- If something is consumed here but produced by a system outside this map,
  add `"external": true` to the consuming fact.
- `path` is workspace-relative (starts with the service folder name) and
  must equal the anchor's path.

## Anchoring (mandatory, no exceptions)

Every fact is pinned to the exact lines of code that back it up. Create the
anchor with the tool, never by hand. From the workspace root:

    python3 -c "
    from cartographer.anchor import make_code_anchor
    import json
    print(json.dumps(make_code_anchor(
        '.', 'orders-svc/src/events/publish.py', (41, 44),
        'git-<short commit sha of that service>', '<today YYYY-MM-DD>'
    ), indent=2))"

Read the file first and choose the tightest line range that shows the claim.
If you cannot find code that backs up the claim, DO NOT write the fact;
tell the expert what you looked for and ask where it lives.

## The loop

1. The expert states a claim in their words.
2. You find the evidence in the code and draft the fact with its anchor.
3. The expert reviews the draft. Only after their explicit yes do you add
   it to `chart/*.json`.
4. After a batch is approved: run `cartographer seal chart`, then
   `cartographer check chart --world ..` and confirm exit 0. Commit.

## Hard rules

- Never invent or guess a fact. Unsure means ask.
- Never edit or remove a fact without the expert's explicit approval.
- Never seal a chart the expert has not reviewed.
- A DRIFTED result from `cartographer check` means the code moved: show the
  expert the fact and the current code, let them decide (still true, needs
  a new anchor, or no longer true), then re-anchor or correct, seal, commit.
- The map stays sparse and verified. A small map of true facts beats a
  large map of plausible ones.
