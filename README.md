# cartographer

A hand-curated map of how your services affect each other, pinned to the
code and served to coding agents over MCP.

Coding agents are good at the code in front of them and blind to the system
around it: the event another service silently consumes, the ordering a
downstream job assumes, the file one team writes and another team reads.
Those relationships rarely exist as greppable symbols in any single repo.
Cartographer stores them as curated facts, pins every fact to the exact
lines of code that back it up, flags facts whose pinned code has changed,
and serves the verified ones to agents such as Claude Code.

Three rules the tool never breaks:

1. **Humans curate, tools verify.** Facts are written with a domain expert
   and approved by them. Nothing edits the map automatically.
2. **Every fact is anchored.** Each claim carries a file, a line range, and
   a content hash from a specific revision. `cartographer check` re-verifies
   all of them against the live code in one command.
3. **Fail closed.** A tampered, unsealed, or self-contradicting map serves
   nothing rather than something partial. The map is sparse and verified:
   absence of a fact is not evidence of absence.

## Install

Needs Python 3.10+.

    git clone https://github.com/lazerspec/cartographer
    cd cartographer
    python3 -m venv .venv && .venv/bin/pip install .

The `cartographer` command is then at `.venv/bin/cartographer` (add it to
your PATH, or install with `pipx install .` instead).

## Quickstart

Make a workspace folder that holds checkouts of the services you want to
map, then create the map repo next to them:

    workspace/
      service-one/
      service-two/
      my-map/          <- created below

    cd workspace
    cartographer init my-map

`init` scaffolds everything: an empty sealed chart, a README for your
teammates, a `CLAUDE.md` curation guide that tells Claude Code exactly how
facts get written and approved, and a `.mcp.json` that connects the map
server automatically when Claude Code opens the folder. Push `my-map` to
your own git host to share it.

### Curate

Open Claude Code inside the map repo and work with it as the domain expert:
you state what is true, it finds the evidence in the code and drafts each
fact with its anchor, you approve. After an approved batch:

    cartographer seal my-map/chart

`seal` refuses a chart that fails validation, so a sealed map is always
safe to serve.

### Check (flag what changed)

After pulling newer service code:

    cartographer check my-map/chart --world .

Exit 0: every fact still matches the code it is pinned to. Exit 1: the
listed facts point at code that changed; review each one, re-anchor or
correct it, then seal again. Exit 2: the chart itself fails verification
and is refused. The check is read-only and needs no network and no AI.

### Serve

Claude Code starts the server via the scaffolded `.mcp.json`; nothing to
run by hand. The agent gets five tools: `chart_index`, `get_scope_facts`,
`who_mentions`, `staleness_check`, and `get_derived_facts`. With the
scaffolded config the server also pulls the map repo's latest commit at
session start, and serves the local copy if the pull fails. If code has
changed underneath the map, every answer starts with a stale warning and
the affected facts are marked, so the agent knows to ask for a human
review. Manual start, if you ever need it:

    cartographer serve --chart my-map/chart --world .

## Testing whether the map helps

Decide how you will judge the map before you build it. The short guide in
[docs/evaluating-the-map.md](docs/evaluating-the-map.md) covers accuracy
spot-checks, drift-flag quality, a replay test scored against your own
merged history, and the rules that keep the trial honest.

## What this is not

- Not automatic knowledge extraction. Curation is deliberate, with a human
  approving every fact. That is the point: a small map of true facts beats
  a large map of plausible ones.
- Not a search index or RAG system. It answers from curated facts only.
- Not a hosted service. Everything runs locally; no telemetry, no network
  calls.

## License

MIT.
