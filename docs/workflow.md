# How work happens on this repo

One maintainer, several agents. These rules keep quality independent of who
or what does the typing.

## Branches

- `main` is always releasable. CI must be green on every commit.
- Every change lives on a feature branch named `<type>/<short-slug>`, where
  type is `fix`, `feat`, `docs`, or `chore`. One ticket, one branch.
- A branch merges to `main` only through a pull request, only after the
  checks in "Before merge" below, and only on the maintainer's word.
- Releases are tags `vX.Y.Z` on `main`. A release that changes trust
  behaviour (anchoring, lint, manifest, remote verification, staleness
  serving, `check` exit codes) is not tagged until the trust red team has
  reported VALIDATED on the release diff.

## Tickets

Tickets live on the board (Linear). A ticket moves through these states:

1. **Proposed.** Anyone, including a reviewing agent, can propose one. It
   must state the claim, the expected versus observed behaviour, and where
   in the code it points.
2. **Verified.** Before any work starts, a fresh `ticket-verifier` agent
   reproduces the claim from the ticket text alone and attaches a
   re-runnable script. A ticket that does not reproduce goes back to
   Proposed with the verifier's evidence; it is never worked on by
   assumption.
3. **Briefed.** The orchestrating session writes the brief: exact files,
   signatures, behaviour, tests, and the acceptance script from step 2.
   Every judgment call is made in the brief, not by the implementer.
4. **In progress.** One `implementer` agent, one feature branch.
5. **Reviewed.** A fresh `diff-reviewer` checks the diff against the brief.
   Critical or Important findings go back to step 4.
6. **Done.** Merged to `main` by the maintainer.

## Before merge

- `ruff check`, `ruff format --check`, `mypy src`, `pytest -q` all green.
- The ticket's acceptance script shows the fixed behaviour.
- Diff review CLEAN, or all Critical/Important findings fixed and re-reviewed.
- Trust-path change: `trust-red-team` VALIDATED on the branch.
- Docs, template, install, or CLI change: `newcomer-tester` SMOOTH, or
  every friction point it hit is either fixed or ticketed.

## Agents

Definitions live in `.claude/agents/`. Each one is dispatched fresh, with a
brief, and never inherits session history.

| Agent | When | Model |
|---|---|---|
| `product-critic` | Before a batch is planned; challenges scope and asks who it serves | strongest |
| `ticket-verifier` | Before any ticket is worked; reproduces the claim | sonnet |
| `implementer` | Builds one briefed ticket on its branch | sonnet |
| `diff-reviewer` | After every implementation; checks diff against brief | sonnet |
| `trust-red-team` | Before merging or tagging any trust-path change | strongest |
| `newcomer-tester` | After any docs, template, install, or CLI change | sonnet |

The maintainer is the product manager and the only one who ratifies scope,
merges, and releases. Agents propose and verify; they do not decide.
