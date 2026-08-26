# enge compare consolidation policy

Full contract for enge compare's grouping/consolidation/exit-code rules,
referenced from CLAUDE.md. See CLAUDE.md's "Compare consolidation policy"
pointer for the load-bearing summary; this file has the full two-stage
algorithm, descriptor-sourcing fallback rules, and dated rulings.

## Compare consolidation policy

`enge compare` (`src/enge/compare/`) is a **read-only** consumer of the
`results.json` contract above — it never parses xunit, never calls
Testing Farm, never writes a cache. `compare/engine.py` is pure (no I/O);
`compare/loader.py` resolves manifests via the shared
`resolve_manifests_for_invocation` and loads each matched run's
`results.json`; `compare/__main__.py` renders. It replaced `enge report
--compare`'s `build_table_comparison` (deleted) and `--unify` (deleted;
no replacement — plan names in `results.json` are always verbatim).

`-s/--short` (fix/short-name-rendering, 2026-07-27) is render-time
only: it shortens the displayed plan/test name for `enge report` and
`enge compare` alike — split on `::` and keep everything after the
first separator, otherwise split on `/` and keep the last two segments
— and never mutates the stored or grouped name backing it;
`results.json` values and the plan-header dedup sentinel stay on the
full verbatim name, consistent with the invariant above.

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
  the coldstore note below).
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
comparable columns. Bare `--since`/`--until` with no manifest selector
select no runs via the legacy-archive path rather than the selector path,
so they reach the `CONFIG_ERROR` floor (not `ValidationError`); `compare`
logs a WARNING there, since unlike `report`/`rerun`/`cancel` it has no
legacy archive to read.

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
and are slated for DELETION when the schema-staleness-warning +
`--refresh` work ships (ledgered as F7).

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
