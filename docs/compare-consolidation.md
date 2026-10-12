# enge compare consolidation policy

Full contract for enge compare's grouping/consolidation/exit-code rules,
referenced from CLAUDE.md. See CLAUDE.md's "Compare consolidation policy"
pointer for the load-bearing summary; this file has the full two-stage
algorithm, descriptor-sourcing fallback rules, and dated rulings.

## Compare consolidation policy

`enge compare` (`src/enge/compare/`) is a **read-only** consumer of the
`results.json` contract in `docs/results-json-schema.md` — it never
parses xunit, never calls Testing Farm, never writes a cache.
`compare/engine.py` is pure (no I/O);
`compare/loader.py` resolves manifests via the shared
`resolve_manifests_for_invocation` and loads each matched run's
`results.json`; `compare/__main__.py` renders. It replaced `enge report
--compare`'s `build_table_comparison` (deleted) and `--unify` (deleted;
no replacement — plan names in `results.json` are always verbatim).

`-s/--short` (fix/short-name-rendering, 2026-07-27; shortened names
became the default and `-l/--long` opts out, RULING F4, maintainer
2026-07-27) is render-time only: it shortens the displayed plan/test
name for `enge report` and `enge compare` alike — split on `::` and
keep everything after the first separator, otherwise split on `/` and
keep the last two segments — and never mutates the stored or grouped
name backing it;
`results.json` values and the plan-header dedup sentinel stay on the
full verbatim name, consistent with `results.json` plan names always
being verbatim.

**Unified view** (compare-redesign, 2026-07-22): there is no mode split.
Every invocation renders one or more tables, each with one column per
matching execution plus an always-present `Consolidated` column. The
prior `--flakiness` flag and its separate no-consolidation table shape
are gone outright — there is nothing left called "flakiness mode."
`flaky` is still computed per row (AMENDMENT-2 semantics: flagged iff
>=2 present, non-absent outcomes differ) but is never rendered as a
column; it exists for future consumers, not for this branch's UI.

**Grouping**: `set` is never a grouping coordinate (display-only
provenance). Tier is a hard partition — one table never spans two tiers.
By default arch and upgrade-path (`source`/`target`, the run-envelope-
or-per-task upgrade-path values, e.g. `"9.9"`/`"10.3"` — see "Descriptor
sourcing" below) fold into columns within one table per tier.
`--splitarch` adds arch to the table key (one table per `(tier, arch)`,
columns per upgrade-path); `--splitpath` adds `(source, target)` (one
table per `(tier, source, target)`, columns per arch); both together
yield one table per `(tier, arch, source, target)` — the old
consolidation-mode grouping, now reachable via explicit flags rather
than being the only shape available.

**Test-row identity** (RULING G1, 2026-09-11): a `--show-tests` row is keyed
on `(plan name, discover-phase-normalized test name)`. tmt prefixes every
test node ID with the name of the discover phase that produced it, and an
*unnamed* phase gets the POSITIONAL name `default-<N>`. A plan whose phases
are guarded by mutually-exclusive `when:` conditions therefore emits the same
test under a different prefix per arch — the observed case was
`/default-0/…::TestBasic` on aarch64/s390x/x86_64 and `/default-1/…::TestBasic`
on ppc64le, from a plan with `when: arch != ppc64le` at phase index 0 and
`when: arch == ppc64le` at index 1. Keyed verbatim, that split one test into
two half-empty rows whose `-s/--short` labels rendered identically, giving no
way to tell them apart. `_strip_discover_phase` removes a leading
`/default-<N>/` before keying.

Deliberately narrow — only the generated `default-<N>` form is stripped:
- A **named** phase (`/tests/…`) may carry real meaning and is left alone.
- A node ID with **no** phase prefix (`/upgrades/…`) must not lose its first
  real path segment, which a blanket first-segment strip would do.
- Row identity still includes the plan, so the same node ID under two
  different plans stays two rows.

**Collision guard**: normalization is applied per plan only when it is
injective in *every* column (`_normalizable_plans`). Mutually-exclusive
`when:` guards — the case this exists for — enable one phase per run, so they
never collide. A plan that genuinely runs the same node ID under two
concurrently-enabled phases would collapse to one key, and `_build_test_rows`
resolves a duplicate key by last-match-wins, silently dropping a verdict; such
a plan keeps verbatim names in all columns instead. An unwanted split row is
recoverable, a wrong cell is not. Measured at ruling time across 101 local
results.json caches: zero collisions under either the narrow or the blanket
rule.

Rendering is unaffected by this rule — `_short_name` keeps only what follows
`::`, so the displayed label is identical either way; `--long` shows the
normalized name, since a merged row spans both phases and printing one
phase's prefix would misattribute it.

**Consolidation policy is TWO-STAGE** (R1, replaces the old single-stage
PASS-wins-else-latest-wins rule; fence-critical, do not "align" with the
`results_parser`/`results_cache` Verdict severity-rank table — that table
ranks `ERROR > FAILED > CANCELED > PASSED > SKIPPED` for a different
purpose (deriving ONE representative verdict for an entire run) and is
explicitly off-limits here):
- **Stage 1**, within each `(arch, source, target)` coordinate present in
  a row, over that coordinate's chronologically-ordered columns: any
  `PASSED` wins; otherwise the latest REAL result wins. `SKIPPED`,
  `CANCELED`, and absent (`—`, the em dash — the sole absence marker
  after this branch; the old consolidation-only `-` marker is gone) are
  all excluded from this scan. `CANCELED` is grouped with `SKIPPED` as an
  absence-class verdict for consolidation purposes only (maintainer
  ruling, 2026-07-22 — the ratified R1 stage-2 rank only defined three
  tiers, `ERROR > FAILED > PASSED`, and was silent on `CANCELED`, which
  is a schema-legal plan/test-level verdict per this file's own
  "documented gaps"; excluding it from both stages was the ruling, so it
  is excluded from stage 1's scan too, not just stage 2's rank).
- Coordinate identity requires known-and-matching source+target (bugfix,
  2026-07-23): two columns are only the same stage-1 coordinate when
  both are non-None and equal. A multi-set manifest predating the
  dispatch-context-schema fields (see "Descriptor sourcing" below)
  leaves both None for every column — in that case each column is its
  own singleton coordinate (keyed on position), never assumed to share
  rerun history with another None/None column just because they share
  an arch. This closes a real bug (two genuinely different upgrade
  paths on the same arch, both undescribed, collapsing into one
  coordinate and letting a PASSED mask an unrelated ERROR) at a known,
  accepted cost: a genuine rerun of the same undescribed coordinate no
  longer gets PASS-wins collapsing either — it falls through to stage
  2's severity-max instead. A stable coordinate-identity signal that
  survives missing descriptors (e.g. `rerun_of` chains) would close
  that gap; not yet plumbed into results.json/`ExecutionColumn` (see
  the coldstore-hyperlink note in `docs/results-json-schema.md`).
- **Stage 2**, across the coordinates present in that row: severity-max
  `ERROR > FAILED > PASSED`. A coordinate with no real result (stage 1
  found nothing to report) contributes nothing to stage 2.
- This is the one behavioral delta from the old rule: a `PASSED` on one
  coordinate no longer masks a `FAILED`/`ERROR` on another — PASS-wins
  only applies *within* a coordinate's own rerun history, not across
  different architectures or upgrade paths sharing a row.
- **All-excluded rows**: if no coordinate in a row produces a real
  result (every cell is `SKIPPED`, `CANCELED`, or absent), the row
  consolidates to `SKIPPED` when >=1 cell is literally `SKIPPED`, else
  to `CANCELED` when >=1 cell is literally `CANCELED`. The verdict enum
  is exhaustive over `{PASSED, FAILED, SKIPPED, ERROR, CANCELED}` and a
  row only exists because it appeared somewhere (non-absent), so this
  fallback always resolves — it never fabricates a `PASSED`/`FAILED`/
  `ERROR` that did not occur.
- l0 (plan) rows consolidate on plan verdicts directly, never derived
  from rolled-up test verdicts (unchanged).

**Exit codes**: `enge compare` always returns `ExitCode.SUCCESS` once the
floor below is met (R4, maintainer-ratified 2026-07-22) — it is a
comparison/reporting view, not a grading command, so table content
(including `FAILED`/`ERROR` consolidated rows) never changes the retval.

Before that floor is even consulted, run selection can fail: a selector
combination (`--run`/`--set`/`--tier`/`--arch`/`--tag`/`--since`/`--until`)
that matches no runs raises `ValidationError` (exit 2, F2,
maintainer-ratified 2026-07-29), distinct from the floor's `CONFIG_ERROR`
(99) which governs a selection that *matched* runs but yielded no
comparable columns. A bare `--since`/`--until` with no other selector
is an ordinary selector too (Q-L30'f, 2026-09-29), so a window matching
no runs raises `ValidationError` before the floor is reached, exactly
like every other selector.

The old consolidation-mode worst-mapped-`ExitCode` reduction (and its
2026-07-17 `CANCELED`→`MISSING_RESULTS` ratification) is deleted along
with the mode it governed — that ruling applied to a retval that no
longer exists, not to anything table-content-derived that survives. The
floor itself is unified to >=1 comparable column (the old mode-aware
split — consolidation's >=2-matched-manifest floor vs flakiness's
>=1-column floor, AMENDMENT-1 — is gone along with the two-mode split);
fewer is a usage error: `ExitCode.CONFIG_ERROR` (99), the same
"invocation cannot be serviced as given" code used elsewhere, since no
result-grading has happened yet at that point. A single manifest fanned
across multiple arches (one `enge dispatch` invocation) is always
sufficient on its own, regardless of table partitioning. Either way,
each missing/corrupt cache logs an ERROR naming the run and the exact
fix (`enge report --run <run_id>`).

**Descriptor sourcing**: `ExecutionColumn.source`/`.target` (the
upgrade-path values used for grouping, column headers, and table
titles) come from the PER-TASK `TaskEntry.source`/`.target` fields
(`utils/results_parser.py`, populated by the dispatch-context-schema
harvest), not unconditionally from the run envelope — the read-side half
of the M4 multi-set descriptor fix. Fallback to the envelope's
`source`/`target` applies ONLY when the per-task value is `None` AND the
**results.json cache** is single-set (`len({t.set for t in
schema.results}) <= 1`) — re-sourced from the cache itself rather than
the manifest's `requests[]` (RULING D-2/item 2(b),
fix/compare-manifest-decoupling, 2026-07-29): `compare/loader.py` is a
downstream reader of the cache and must not re-derive anything from the
manifest to agree with it. This mirrors the same `<= 1` rule
`report/results_cache.py`'s own `is_single_set` harvest-time fallback
applies, expressed there in terms of the manifest because that module is
the one writing the cache in the first place. A multi-set cache with
no per-task value gets no fallback and stays `None`; `compare/__main__.py`
renders a `None` descriptor as the em dash, never the literal string
"None" — the em dash is always a render-time substitution, never a
stored value. **Ratified sunset (RULING D-2, 2026-07-29)**: this
envelope fallback and its single-set gate are legacy-cache support for
results.json caches written before per-task `source`/`target` existed,
and are slated for DELETION. The precondition is met: the
schema-staleness-warning + `--refresh` work (ledgered as F7) has
shipped, so a stale cache now has a corrective path
(`enge report --run <run_id> --refresh`) and `compare` warns once per
invocation when one is selected. The deletion itself is queued as its
own PR and is deliberately NOT part of F7 — removing the fallback
changes what existing unrefreshed caches render, which is a separate
user-visible behaviour change deserving its own review and CHANGELOG
entry.

**Artifacts URL sourcing**: `ExecutionColumn.artifacts_url` is sourced
in `compare/loader.py` from the results.json cache exclusively — never
from the manifest, which by this point in the pipeline is used for run
selection only. Layering (RULING D-1, fix/compare-manifest-decoupling,
2026-07-29): the stored `TaskEntry.artifacts_url` wins whenever it is
truthy (verbatim historical value); a falsy value (missing, or `""` — a
legacy cache predating the field or a harvest that populated it) falls
back to a constructed
`f"{ctx.testing_farm_endpoint.log_artifact_baseurl}/{task.task_id}"`,
mirroring how dispatch derives the same URL at request time.

**Deprecation alias**: `enge report --compare` delegates to `enge compare`
for one release, emitting a WARNING. Unlike every other manifest-backed
`enge report` invocation, the alias does **not** gap-fill the results
cache while delegating (maintainer ruling, 2026-07-17) — run `enge report
--run <run_id>` first if the cache needs populating. `--splitarch`/
`--splitpath` are compare-only flags; the alias path's `report` action
parser doesn't define them, so `getattr(ctx.cli_args, "splitarch",
False)` (and the `splitpath` equivalent) default the alias to the
unsplit, one-table-per-tier shape.

**Lineage mode** (item 5.2, `enge compare --lineage`, run-loop contract v3
§5a, rulings Q-L5-1..4, 2026-10-11): one column per rerun **lineage** instead
of one per task. Lineage structure (root, generation, coverage, attempts
spent, trajectory, outcome) belongs to the pure `src/enge/lineage.py`
(Q-L5-3); `compare/engine.py` (`build_lineage_tables`) only chooses rows and
cells, `compare/loader.py` (`load_lineage_columns`) only loads, and the
markers are render-layer — never written to `results.json`.

- *Selection*: `--run` only, or the default (the latest run) when no
  `--run` is given. Manifests contribute `run_id` and nothing else; every
  task of each selected run's cache is a selected task, and lineages are
  discovered by walking every cached `<run_id>.json` under `results_dir`
  via `rerun_of` (Q-L5-1). Each selected task resolves to its lineage
  root, so selecting a rerun's run shows the same column as selecting its
  root's run. Validation runs before anything loads, and each failure is
  exit 2: `--set`/`--tier`/`--arch`/`--tag`/`--since`/`--until` with
  `--lineage` (`--lineage selects runs with --run only; drop <flags>`),
  `--retry-count` without `--lineage`, and `--retry-count` below 0.
  Unreadable caches, a task id cached twice (the first file in name order
  wins), reruns whose parent was never cached (shown as their own root)
  and stale caches contributing to the shown lineages each give one
  aggregated WARNING per invocation; no selected task is the usual floor
  (`CONFIG_ERROR`).
- *Columns and generations*: roots are grouped and sorted with the same
  `group_columns` as normal mode, so tier stays a partition and
  `--splitarch`/`--splitpath` behave identically. A cell lists the key's
  per-generation values in lineage order (generation, then dispatch time),
  one per task that covers the key: `P F E S C` for the cached verdict,
  `E` for an unparseable task (`ERROR` with no plans), `C` for a task
  cached as `CANCELED` with no plans, `.` for a covering task with no
  result for the key; then the terminal marker. A cell that no task of
  the lineage covers renders `—`.
- *Outcome* (`LineageIndex.outcome`): (1) empty trajectory → no marker;
  (2) the last two entries unparseable → `[?]`; (3) only the last one
  unparseable → no marker (its rerun is still due and uncharged); (4) last
  value `C` → `[-]`; (5) last value `F` or `E`, a retry count given and
  attempts spent ≥ it → `[X]`; (6) otherwise no marker. `--lineage` has no
  default retry count (Q-L5-2′): without `--retry-count N`, `[X]` is never
  evaluated, while `[?]` and `[-]` still render.
- *Plan-row markers* (Q-L5-4): the plan view stays the default. A plan
  row's cell is the plan-verdict trajectory, and its marker is the most
  severe outcome among the plan key and every test key of that plan in
  that column, `[?]` > `[X]` > `[-]`. With `--show-tests`, every test row
  carries its own marker, and a plan with no tests in any generation keeps
  one plan-level row.
- *Coverage is parent-relative*: native dispatch records `plan` as a tmt
  name REGEX, not exact names, so a child whose recorded plans (split on
  `", "`) are all plans its parent parsed covers exactly those, and any
  other child inherits its parent's coverage. A regex containing `", "`
  therefore never narrows — the safe direction: it can only over-cover.
  The child's `tests` filter then applies.
- *Row key vs. counter key*: rows are keyed like normal mode, by `(plan,
  G1-normalized test name)`, with the G1 collision guard run over the
  union of every generation. Retry accounting is keyed by the test's last
  `::` segment, because that is what a rerun's `tests` filter records:
  two rows sharing a last segment share one counter.
- *Consolidated* is the unchanged two-stage `consolidate_row` over every
  generation of every column, each contributing its actual cached verdict
  or `—` (a task with no plans contributes `—`). It equals what normal
  compare computes over the same tasks; parent and child share a
  coordinate, so a pass on rerun wins within it.
- *Rendering*: the table caption states the retry policy
  (`lineage view; retry policy: --retry-count N`, or `lineage view; no
  retry policy given, so [X] (exhausted) is not evaluated`); one legend
  line follows every table; under `~~~ TASK REFERENCE` each column gets
  `(i) <arch> <source>→<target>` then `gen<G>=<artifacts_url>` per task
  (two tasks of one generation both appear; there is no `run=` field),
  and, when the lineage's source composes differ, ` composes: ` with
  `gen<G>=<source_compose>` per task. A cell is colored by its last
  verdict; a cell ending in `.` stays uncolored. `-o json` prints nothing,
  as in normal mode.
