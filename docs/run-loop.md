# enge run loop contract

The contract for `enge run` (`src/enge/run/`), the supervised rerun
loop, referenced from CLAUDE.md. The loop was designed as run-loop
contract v3, which was drafted outside this repository: §1 (retry
accounting) and §4 (the loop), plus rulings Q-R6-A..M from 2026-10-12/13.
This file is the in-repo record of what shipped, and the code cites its
rulings by those IDs. Contract v3 §5a, the lineage view the loop renders
and grades from, is recorded in `docs/compare-consolidation.md`, under
"Lineage mode" (rulings Q-L5-1..4).

## Shape

```
gen 0:  dispatch -> wait -> harvest (+D6b) -> [every task unparseable? -> CRITICAL, exit 3]
gen G:  candidates -> [none? -> FINAL] -> rerun -> wait -> harvest (+D6b) -> gen G+1
FINAL:  enge compare --lineage --run <root> --retry-count N  (the only stdout) -> exit code
```

`RunLoop` (`run/__main__.py`) owns the generations. Everything outside
the loop is a `Phases` call (`run/phases.py`), which the loop tests
replace with a fake.

- **Gen 0** is `submit_dispatch` with `enge test`'s own namespace: the
  `run` subparser reuses `_add_dispatch_args` and `_add_tagging_args`,
  and every value the loop was given for an argument `enge test` also
  has is copied over, so `enge test`'s validation runs unchanged (Q-R6-A,
  Q-R6-G).
- **The other phases** parse real argv (`report --run <id>`,
  `rerun --run <id>`, `compare --lineage --run <root>`) with the loop's
  global flags first. Every phase context reuses a copy of the config
  the invocation loaded once, so load-time WARNINGs print once (Q-RN-3)
  and a config file edited while a loop waits changes nothing.
- **State (R7-4).** `enge run` writes no manifest or state file of its
  own. Every manifest comes from the reused `submit_dispatch` and
  `submit_rerun`, and every `results.json` from report's cache writer.

## Output (Q-R6-B, Q-R6-H)

The final `enge compare --lineage` table is the only stdout. Phases run
quiet: the shared console prints nothing, anything a phase `print()`s is
discarded, and the phase loggers (`enge.dispatch`, `enge.rerun`,
`enge.report`, `enge.utils`, `enge.compare`) are held at WARNING (compare
at ERROR during in-loop index loads), unless `-v` or `-d` is given.

Progress goes to stderr on the `enge.run.__main__` logger. The lines are
pinned verbatim in `tests/test_run_loop.py`: the root run id with the
inspect and resume commands, each generation's run id, a line whenever a
generation's finished count changes, a heartbeat after 15 minutes
without a change, each generation's verdict counts, and the rerun
breakdown.

## Waiting (R7-1, Q-R6-E′, Q-R6-I)

`run/poller.py` polls every unfinished task concurrently: one Testing
Farm GET per task every 30 s. A failed poll is retried after 60 s. Three
failed polls in a row make a task *unreachable*. A poll counts as failed
when the request fails, or when the answer has no non-empty string
`state` (audit B3, 2026-10-21). An unknown but well-formed state name
counts as a live task, so a state Testing Farm adds later does not make
tasks unreachable; the timeout bounds the wait.

The wait for a generation ends when every task is terminal or
unreachable, or after `--timeout HOURS` (default 24, per generation).
Tasks still running then are *timed out*. Nothing is ever canceled.

## Harvest (D6b, Q-R6-M)

A harvest is `enge report --run <id>` without the table:
`parse_request_xunit_concurrent(..., raise_on_unparseable_input=True)`,
then `cache_report_results`.

- A task is *unparseable* when it is cached as `ERROR` with no plans (no
  xunit). Before any task counts as unparseable, the run is harvested
  once more with `refresh=True`, after 60 s (D6b).
- A harvest that raises `NetworkError` stops the loop (Q-R6-M).
- Report never fails on a caching error: it logs a WARNING and returns.
  So after every harvest, including the D6b refresh, the loop checks that
  every task of the run has a cache entry. A task without one stops the
  loop (audit B1, 2026-10-21).

## Retry accounting (contract v3 §1)

- The budget is `--retry-count N` (default 2) reruns per *counter key*:
  a test's last `::` segment (what a rerun's `tests` filter records), or
  the plan name for a failing plan without failed tests. Two tests that
  share a last segment share a counter.
- Attempts spent are re-derived from the cache through `enge.lineage`
  every generation. The loop keeps no counter of its own, and the loop
  and the table read the same lineage, so they cannot disagree (Q-L5-3).
- CANCELED results and SKIPPED plans are terminal: never rerun.
- An unparseable task gets one rerun with its original filter that does
  not count against the budget, so it is due even at `--retry-count 0`.
  A second unparseable result in a row ends the lineage `[?]`.

Candidates (`run/candidates.py`) are built once per task of the
generation just harvested:

- A key is rerun only while `spent < retry_count` and that task is the
  key's newest covering task. A rerun made outside the loop is left
  alone, so no key is planned twice.
- A FAILED or ERROR plan with failed tests reruns those tests, by last
  `::` segment. A failing plan without failed tests is rerun plan-only.
  No plan is ever both.
- Plan-only plans travel in one payload (L68; Q-R6-J).
- A rejected rerun request is never retried. Its keys end at their last
  result, with a WARNING (Q-R6-C).

## Stopping early (Q-R6-E′, Q-R6-I, Q-R6-K, Q-R6-M)

A generation ends with tasks that have no result when they timed out,
were unreachable, hit a harvest `NetworkError`, or were left out of the
cache. The loop then stops. It renders what landed, logs one WARNING
with the resume command (`<n> task(s) of run <id> have no result yet;
the loop stopped. Resume with: enge run --resume <root>`), and folds 4
into the exit code.

- When every gen-0 task is unparseable, the loop stops before rerunning
  anything: CRITICAL, exit 3.
- Gen-0 requests that failed to submit have no result and fold in 4
  (Q-R6-K). When nothing was submitted, exit 1.
- `KeyboardInterrupt` restores the console and logger state and exits
  130. Nothing is canceled on Testing Farm.

## Resume (Q-R6-D, Q-R6-D′)

`enge run --resume <root>` follows `parent_run_id` down from the root's
manifest. It harvests every run that has no finalized cache, logging
`resume: waiting on gen <G> run <id> (<n> task(s))` for each one it has
to wait on, then continues from the newest. A root that has a parent,
or a fork in the chain, is a `ValidationError`. `--resume` with any
dispatch or tagging flag is a `ValidationError`.

The loop keeps its policy (Q-R6-D′, 2026-10-13). `--retry-count` and
`--timeout` come from the root manifest's `argv`, the root invocation's
own flags, unless given again:

- A flag given on resume wins, with an INFO when it differs from the
  root's.
- A root dispatched by `enge test` has no policy: the flags or the
  defaults apply, with an INFO.
- An argv that does not parse, or holds an out-of-bounds value, falls
  back to the flags or defaults with a WARNING.

With the root's policy, resuming a settled loop dispatches nothing and
goes straight to FINAL.

## Exit code (Q-R6-F, Q-R6-K, Q-R6-L)

The exit code is the most severe final state over every key, in both
table views, by precedence 3 > 2 > 4 > 0, the same order as
`enge report`:

| Last value in a cell | Code |
|---|---|
| `E` (ERROR, or unparseable; `[?]` ends on `E`) | 3 |
| `F` (FAILED) | 2 |
| `C` (CANCELED), or `.` (covered without a result) | 4 |
| `P`, `S` | 0 |

Tasks with no result at all (timed out, unreachable, never harvested or
cached, or rejected at gen 0) also fold in 4.

A lineage whose tasks never produced a plan result gives its table no
row when it is alone in its tier. Such a lineage is graded by its last
task's verdict (ERROR 3, FAILED 2, CANCELED or no verdict 4,
PASSED/SKIPPED 0, any other verdict 3). The loop logs one WARNING naming
its root task, set, tier and arch, since the table cannot show it
(audit B2, 2026-10-21). Rendering a row for it in `compare --lineage` is
a compare-contract change, left for its own item.

Exit 2 is shared with `ValidationError` and argparse usage errors, as it
is for every other subcommand. Config validation runs before `enge run`'s
own argument checks, so an invalid config exits 99 first.

## Open items (not contract)

- SIGTERM (for example a CI job timeout) is not handled, only
  `KeyboardInterrupt`. Keep `--timeout` below the CI job's timeout.
- A finalized run holding `ERROR` with no plans logs report's own
  "refresh with `--refresh`" WARNING just before the D6b refresh does
  exactly that.
- After a harvest `NetworkError`, the WARNING counts every task of the
  run, including tasks an earlier partial harvest had already cached.
- A cache holding every task entry but no root verdict (report logs and
  swallows a finalization failure) does not stop the loop: grading reads
  the task entries only, so the result is complete and correct. A later
  `--resume` re-harvests that run, since it is not finalized
  (re-review R1, 2026-10-21, declined: stopping would grade complete
  results as missing).
