# System map (cartographer)

This repo is a curated map of how our services actually behave together:
which events cross service boundaries, what each side assumes, and where in
the code each claim is pinned. Coding agents (Claude Code) read it through
the cartographer tools; humans curate it.

Every fact in `chart/` is pinned to a specific file and line range in a
specific revision of a service. If that code changes, the fact gets flagged
until a human re-confirms it. Facts are never edited automatically.

## Setup

1. Install the tool (needs Python 3.10+):

       git clone <cartographer repo url>
       cd cartographer && python3 -m venv .venv && .venv/bin/pip install .

   Put `.venv/bin` on your PATH, or `pipx install <path>` if you use pipx.

2. Make a workspace folder with the service checkouts and this map repo
   side by side:

       workspace/
         service-one/
         service-two/
         this-map-repo/

3. Open Claude Code inside this repo. The `.mcp.json` file starts the map
   server automatically; you get tools like `chart_index` and
   `get_scope_facts`. The server pulls this map repo's latest commit at
   session start when it can; with no network it serves the local copy.

## Day to day

- After pulling newer service code, run from this repo:

       cartographer check chart --world ..

  Exit 0 means every fact still matches the code. A DRIFTED list means those
  facts point at code that changed: re-confirm or correct each one, then run
  `cartographer seal chart`.

- To add or change facts, work with Claude Code in this repo; `CLAUDE.md`
  holds the curation rules. A human reviews every fact before it is sealed.

- If the map tools show a STALE warning, the code moved underneath the
  map. Run `cartographer check chart --world ..`, review each flagged
  fact, then seal and push so everyone gets the refreshed map.

The map is sparse and verified: absence of a fact is not evidence of absence.
