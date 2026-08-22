# Evaluating a map: is it accurate, and does it help?

A map is an investment. Before your team spends weeks curating one, decide
how you will judge it, and decide that before the results exist. This guide
gives you a small set of checks that produce honest answers. Two different
questions need judging, and they have different judges.

## Question 1: is the map accurate?

The judge here is the code plus a human. Never grade truth by asking an AI
whether the facts look right.

### Spot-check for wrong facts

Every fact is pinned to specific lines of code, so checking one is cheap:
open the pinned code and ask "does this code really show what the fact
claims?" Once a month, sample 10 to 15 facts at random and check each one.
Best done by a second person who knows the flow, or by the original curator
a few weeks later with fresh eyes.

Track one number: wrong or unsupported facts found, out of facts checked.
The target is zero. A single invented fact costs more trust than fifty true
facts earn, and the curation rules in the scaffolded `CLAUDE.md` (a human
approves every fact, no fact without evidence) exist to keep this number at
zero.

### Flag quality

When `cartographer check` flags facts after the code changes, record what
each flag turned out to be:

- **Good catch**: the fact was genuinely outdated.
- **Noise**: the fact was still true, the code just moved. Re-anchor it.
- **Miss**: a fact turned out stale and was never flagged. Investigate
  immediately; the check is deterministic and a miss usually means the
  workspace checkouts were stale when it ran.

Frequent noise is a tuning problem. A miss is a serious problem.

## Question 2: does the map help?

This is the question that decides whether to keep investing. The trap is
asking people "was it helpful?" because the answers will be kind. Use answer keys
that already exist.

### The replay test (strongest evidence)

1. Collect 5 to 10 real past changes where a change in one service ended up
   requiring changes, or causing problems, in another service. Merged pull
   requests and incident write-ups are ready-made sources. You already know
   the true answer for each: what was actually affected.
2. For each case, ask a coding agent that can only see the changed service:
   "what else in the system is affected by this change?" Run it twice, once
   with the map connected and once without.
3. Score both runs against what actually happened. The judge is history,
   not opinion.

The map earns its keep if the with-map runs name affected services the
without-map runs miss. Pick the replay cases after the map is built, or
have someone else pick them; if you curate exactly the cases you plan to
test, you have proven nothing.

### The diary (cheapest evidence)

For a few weeks, every time you or an agent consults the map during real
work, write one line: did it change what happened?

- Caught a cross-service effect that would have been missed.
- Saved a hunt through other repos.
- Contributed nothing.

At the end you have a count instead of an impression.

## Three rules that keep the test honest

1. **Set the bar before you start.** For example: "if after four weeks and
   one fully mapped flow the map has influenced zero real decisions, we
   stop." A test you cannot fail is not a test, and a cheap, early "no" is
   a successful outcome; it saves the months you would have spent finding
   out slowly.
2. **Do not curate to the test.** Keep the replay cases out of the
   curation sessions.
3. **Log the misses too.** Cases where the map knew nothing tell you
   whether the problem is coverage (curate more) or the idea itself (stop).

## What a fair comparison looks like

When you report results, report all of it: the number of cases, the wins,
the losses, and the cases the map knew nothing about. "The map caught 3 of
8 cross-service effects the agent otherwise missed, contributed nothing on
4, and was wrong on 1" is a result a team can act on. A highlight reel is
not.
