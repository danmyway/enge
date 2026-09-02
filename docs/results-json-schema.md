# results.json schema and write policy

Full contract for the results.json cache format, referenced from CLAUDE.md.
See CLAUDE.md's "Results.json format" pointer for the load-bearing summary
every agent needs unconditionally; this file has the complete schema,
validity rules, golden fixture MD5s, and dated rationale.

## Results.json format

**Ownership**: `enge report` writes `results.json` after parsing xunit;
`enge dispatch` never touches it (hard invariant). The schema + gap-fill
write API (`utils/results_parser.py`) is standalone and does not import
manifest modules, by design — callers pass expected counts and all
needed values explicitly. Report-subcommand integration lives in
`report/results_cache.py` (`cache_report_results`, wired into
`report/__main__.main()`), which delegates manifest lookup — which
`results_parser.py` deliberately does not own — to
`utils/manifest_resolution.py`'s `resolve_manifests_for_invocation`
(imported into `results_cache.py` as `_resolve_manifests_for_report`).
`enge compare` (`compare/loader.py`) is a second, read-only consumer of
`resolve_manifests_for_invocation` and `parse_results_json` — it never
writes, gap-fills, or re-derives verdicts; a manifest-backed
`enge report --run <run_id>` remains the only way to populate a run's
cache. `enge report --compare` is the sole read-only exception to `enge
report`'s cache-write ownership; see `docs/compare-consolidation.md` for
the full contract.

**Report write policy (implemented in `report/results_cache.py`)**:
caching is a side effect of `enge report`, never a behavior change to
its table output or exit code.
- **Manifest-backed invocations** (default latest-run, `--run`, or the
  structured filters `--set`/`--tier`/`--arch`/`--tag`) gap-fill
  `results.json` + verbatim xunit for **every** matched run — a filter
  selector matching N runs produces N cache files. Manifest resolution
  is independently re-derived in `utils/manifest_resolution.py`
  (mirroring, not importing, `utils/task_resolver.py`'s precedence)
  because it needs full manifest objects and run_id-per-task attribution
  across possibly-multiple matched runs, not `task_resolver`'s flat
  task_id list. Shared with the planned `enge compare` subcommand so
  that selector precedence isn't reimplemented a third time.
- **Raw-input invocations** (`-f/--file`, `-i/--input`, or the legacy
  `--get-tag`/bare-date archive path) never write a cache — there is no
  resolvable run_id to key on. No flag opts out of caching for
  manifest-backed invocations; there is no cache-disable switch.
- **Terminality predicate** (`_is_task_terminal` in
  `report/results_cache.py`): a task is terminal iff its TF state is
  `COMPLETE`/`ERROR`/`CANCELED` (or `CANCELLED`). This is a **separate**
  predicate from `reportportal/operations.py`'s `_is_tf_task_incomplete`,
  which treats `CANCELED` as *incomplete* (nothing left to poll for RP
  finish/enrich). For the results cache, `CANCELED` **is** terminal: it
  is a valid, final entry (`verdict: CANCELED`, `plans: []`) that must be
  recorded so a later report invocation doesn't keep re-checking it. RP's
  predicate is untouched by this — RP is feature-frozen pending sunset.
  Non-terminal tasks get no entry this invocation; gap-fill picks them up
  on a later `enge report` run.
- **Verdict mapping** goes through `XUNIT_RESULT_MAP` exactly. A
  task_id present in the fetched TF results but absent from the
  manifest's `requests[]` is skipped with a WARNING — metadata is never
  fabricated. An unrecognized xunit verdict value maps to `ERROR` with a
  WARNING naming the value (report-layer policy, not a schema concern).
  Task-level verdict prefers TF's own per-task overall result when it is
  a real, recognized value; otherwise it falls back to severity-max over
  that task's plan verdicts. This fallback is intentional and distinct
  from the exit-code dispatch's unrecognized-overall → MISSING_RESULTS
  mapping in concurrent_parser.py — verdict layer and exit-code layer are
  separate contracts; do not align the two.
- **`total_duration_seconds`** is the sum of per-test durations when
  xunit is present. For `CANCELED` and terminal-no-xunit `ERROR` entries
  it is the `0.0` sentinel (maintainer ruling, 2026-07-15/16): the TF
  fetch layer (`report/concurrent_parser.py`) does not expose a
  finished/updated timestamp to compute elapsed time from, and none of
  the code or fixtures in this repository confirm TF's API even carries
  one — inventing an estimate was rejected in favor of an honest zero.
- **Caching failures never fail the report command.** Re-reporting an
  already-finalized run raises `AlreadyFinalizedError` (a
  `ConflictError` subclass) per task, caught inside
  `cache_report_results` and logged at DEBUG naming the run — the
  expected steady state for finalized runs, not a drift signal. A
  genuine content conflict from a corrupted prior cache (or any other
  unexpected error) is caught per-task/per-run and logged at WARNING;
  `report/__main__.main()` additionally wraps the whole call in a
  broad backstop for defense in depth. The user's table/exit code
  always renders regardless of cache state.
- **Verbatim xunit bytes**: `report/concurrent_parser.py`'s `TaskResult`
  carries both `xunit_content` (decoded `str`, used by the existing
  table-rendering `XMLParser`) and `xunit_bytes` (raw `response.content`,
  populated in the same non-streaming fetch as `xunit_content`). The
  results cache writes `xunit_bytes` verbatim to the `.xml` archive —
  never `xunit_content.encode()` — because `response.text`'s
  charset-guessing decode is not a safe inverse of `str.encode()`; a
  wrong guess would silently corrupt the archived xunit for any
  non-ASCII byte sequence. `results_cache.py` also parses xunit for
  schema purposes independently of `XMLParser` (a small internal lxml
  pass): `XMLParser` truncates plan names for display
  (`.split(":")[-1]`) and never reads per-test `time`/`start-time`/
  `end-time` attributes, neither of which is compatible with the
  results.json contract (verbatim names, real durations/timestamps).
  This also means the existing table-rendering code path is completely
  untouched by this feature.

**Why v3.1 replaces the v1 flat schema**: the v1 flat schema (one
`set`/`tier`/`arch` + flat `tests[]` per `run_id`) was falsified against
real data before it ever shipped. One enge dispatch produces ONE
manifest with N requests — one TF task per set x tier x arch combination
(a real run had 8 requests across 2 upgrade-path sets x 4 arches) — and
one TF task's xunit contains MULTIPLE testsuites/plans, not a flat list
of tests. The flat schema could represent neither a multi-task run nor a
multi-plan task. v3.1 fixes both with a three-level hierarchy: run
envelope -> per-task results -> per-plan -> per-test.

**Schema v3.1 (contract-pinned as of 2026-07-14)**:
```json
{
  "schema_version": 1,
  "run_id": "01KWY2ANGF1TCT6E3X8M7QPJTW",
  "created_at": "2026-07-07T10:35:07Z",
  "event": "preliminary",
  "source": "9.9",
  "target": "10.3",
  "verdict": null,
  "results": [
    {
      "task_id": "5d67eecf-a02d-46b7-aee2-9ffb673f40df",
      "set": "verification_99_103_ctc2-ver-9to10",
      "tier": "tier3",
      "arch": "s390x",
      "source_compose": "RHEL-9.9.0-20260629.0",
      "target_compose": null,
      "dispatched_at": "2026-07-07T10:35:13Z",
      "verdict": "ERROR",
      "total_duration_seconds": 349.0,
      "source": "9.9",
      "target": "10.3",
      "git_ref": "main",
      "event": "preliminary",
      "build_ids": ["12345:centos-stream9-x86_64"],
      "rerun_of": null,
      "artifacts_url": "http://artifacts.osci.redhat.com/testing-farm/5d67eecf-a02d-46b7-aee2-9ffb673f40df",
      "plan": "plans",
      "plan_filter": "tag:verification_99_103_ctc2",
      "plans": [
        {
          "name": "/plans/newstyle/nondestructive/verification_99_103_ctc2",
          "verdict": "FAILED",
          "tests": [
            {
              "name": "/tests/newstyle/upgrades/tests/nondestructive/test_var_run_symlink.py::TestVarRunSymlink",
              "verdict": "FAILED",
              "duration_seconds": 75.0,
              "start_time": "2026-07-07T10:51:27.205058+00:00",
              "end_time": "2026-07-07T10:52:48.504964+00:00"
            }
          ]
        },
        {
          "name": "/plans/newstyle/upgrades/tests/destructive/test_luks_multiple_partitions.py::TestLuksMultiplePartitions",
          "verdict": "ERROR",
          "tests": [
            {
              "name": "/default-0/upgrades/tests/destructive/test_luks_multiple_partitions.py::TestLuksMultiplePartitions",
              "verdict": "ERROR",
              "duration_seconds": 0
            }
          ]
        }
      ]
    }
  ]
}
```

**Run envelope — all keys required, three nullable**: `schema_version`
(int, literal `1` for this contract version; any other value is
rejected), `run_id` (ULID; matches manifest and filename), `created_at`
(ISO 8601; mirrors manifest `created_at` — run creation, NOT file-write
time), `event`, `source`, `target` (nullable strings, 2026-07-24 — see
below), `verdict` (**nullable, write-once**: null = run incomplete; set
exactly once by `finalize_root_verdict` when all manifest requests have
entries), `results` (list of task entries; may be empty for a freshly
initialized file).

**`event`/`source`/`target` nullability**: the envelope holds only
things relevant to the whole run as a single batch. A single-set
manifest has one real answer for all three, copied from the manifest's
`context` (e.g. `event="preliminary"`, `source="9.9"`,
`target="10.3"`). A **multi-set** manifest has no single correct
answer for any of the three and all become `null` — deliberately not
special-cased on whether the sets happen to share an upgrade path:
tier/arch can still diverge between sets even when source/target
coincide (e.g. one set running `tier0`/`x86_64`, another running
`tier1`/`aarch64`, both `9.9`→`10.3`), so a matching path alone
doesn't make the run a coherent batch. `is_single_set` (`len({r.get(
"set") for r in manifest["requests"]}) <= 1`) is the only signal —
the same check already governing the per-task `source`/`target`/
`event`/`git_ref` fallback described below. Per-task values are
unaffected either way; they always carry the real per-request truth
regardless of set count.

**Task entry — required unless noted; keyed by `task_id`**: `task_id`
(TF request UUID; join key to manifest `requests[]`; gap-fill idempotency
key), `arch`, `set`/`tier`/`source_compose`/`target_compose` (required
keys, nullable values — manifest parity; `set: null` = a no-set CLI
invocation, e.g. test-development workflows where the set is fully
defined on the command line; `tier: null` = an untiered dispatch, e.g.
`enge dispatch --plan <plan>` with no `--tier`, or a rerun whose parent
lineage did not resolve), `dispatched_at` (ISO 8601, from manifest
request), `verdict` (task-level, non-null), `total_duration_seconds`
(float; for CANCELED/no-xunit ERROR: elapsed time before terminal
state), `plans` (list; see validity rules below). `source`, `target`
(upgrade-path values, same format as the envelope fields above), `git_ref`,
`event` (nullable strings) and `build_ids` (list of strings, `[]`
default) are **optional** — unlike every other field in this entry, a
missing key is tolerated rather than rejected by `TaskEntry.from_dict`,
so that `results.json` caches written before these fields existed keep
parsing. Copied verbatim from the manifest's matching `requests[]` entry
at harvest time (`report/results_cache.py`); for a manifest predating
this schema (no per-request values to copy) the harvest falls back to
the run envelope's `source`/`target`/`event` **only** when the manifest
has exactly one test set across all its requests — a multi-set legacy
manifest gets no fallback, since the envelope's single value can't be
trusted to belong to any particular request. `git_ref` has no envelope
equivalent to fall back to (the envelope never carried it, before or
after this schema revision), so it stays `null` on every legacy entry.
`build_ids` never backfills.

`rerun_of`, `artifacts_url`, `plan`, `plan_filter` (nullable strings,
2026-07-24) are likewise **optional** — a missing key defaults to
`null` rather than being rejected, same tolerance as the five fields
above. Unlike `source`/`target`/`git_ref`/`event`, these four have **no
run-envelope fallback at all**, single-set or not: there is no such
thing as a run's "envelope plan" or "envelope rerun lineage." `rerun_of`
and `artifacts_url` are copied verbatim from the manifest's matching
`requests[]` entry (already written there by
`dispatch/set_flow.py::add_request` — this extension only stops
discarding them at harvest time, it adds no new manifest field). See
`docs/manifest-schema.md` for the full `requests[]` entry shape — which
writer populates each field and its current nullability. `plan`
is likewise copied verbatim from the manifest request — not recomputed
anywhere, it lands in the request as-is. `plan_filter` is the one
exception sourced from neither the manifest nor the envelope: the
manifest deliberately never records it at dispatch time (it already
lives in the TF API request body, so an in-flight write would be
redundant) — it comes from `report/concurrent_parser.py`'s
`TaskResult.request_plan_filter`, populated by the same live per-task
TF fetch `enge report` already performs on every invocation and prints
in its `REQUEST METADATA` table, at zero additional network cost.
`TaskResult`'s `""` empty-string default is normalized to `null`,
matching this schema's established none-vs-empty convention.

**Plan — all required**: `name` (str, verbatim `testsuite@name` from
xunit), `verdict` (enum), `tests` (list).

**Test — required**: `name` (str, verbatim `testcase@name`), `verdict`
(enum), `duration_seconds` (float; `0` when not run). **Optional**:
`start_time`, `end_time` (ISO 8601, copied verbatim from xunit
`start-time`/`end-time`), `output` (str), `error_detail` (str).

**Verdict enum** (identical vocabulary at every level): `PASSED | FAILED
| SKIPPED | ERROR | CANCELED`. Unknown values are rejected at validation.

**Validity rules (8, enforced by `from_dict` at each level)**:
1. Task `CANCELED` -> `plans` MUST be `[]` (dispatch-level cancel; no
   partial results).
2. Task `ERROR` -> `plans` MAY be `[]` (terminal task with no xunit, e.g.
   misconfigured plan filter -> zero plans discovered) or populated (task
   errored mid-run).
3. Task `PASSED` or `FAILED` -> `plans` MUST be non-empty.
4. Plan `SKIPPED` or `ERROR` -> `tests` MAY be `[]`. Plan `PASSED`/
   `FAILED` -> `tests` MUST be non-empty.
5. Root `verdict`: `null` or enum. Non-null root verdict = file
   finalized/frozen.
6. `schema_version != 1` -> `ValidationError`.
7. Task entries must have unique `task_id`s -> duplicate ->
   `ValidationError`.
8. Duration/type/timestamp validation carries the lenient
   `datetime.fromisoformat` acceptance forward from the v1 module —
   ratified for MVP.

**Documented gaps (not contradictions — the ratified rule set is
explicit about which verdict/emptiness pairs it constrains, and no
others)**: task-level `SKIPPED` has no plans-emptiness rule (rules 1-3
only cover CANCELED/ERROR/PASSED/FAILED), and plan-level `CANCELED` has
no tests-emptiness rule (rule 4 only covers SKIPPED/ERROR/PASSED/FAILED).
Both are left unconstrained rather than inventing a rule; a maintainer
call is needed if either should be tightened.

**Root verdict derivation** (`finalize_root_verdict` in
`utils/results_parser.py`, the ONLY place this happens): severity
ranking `ERROR > FAILED > CANCELED > PASSED > SKIPPED`; root = the
highest-severity task verdict present. All-SKIPPED -> `SKIPPED`
(falls out of the ranking naturally — no special case needed).
Derivation happens exactly once, when `len(results)` reaches the
expected count passed in by the caller; never recomputed, never
overwritten once written.

**Xunit -> schema mapping contract** (exported from `results_parser` as
`XUNIT_RESULT_MAP`, NOT applied by this module — parsing xunit is
report-layer work on a later branch; the map exists purely so that
future parser and this schema cannot drift):
```python
XUNIT_RESULT_MAP = {
    "passed": "PASSED",
    "failed": "FAILED",
    "error": "ERROR",
    "skipped": "SKIPPED",
    "undefined": "ERROR",   # terminal task, plan never completed (e.g. stage=guest-provisioning)
    "pending": "ERROR",     # terminal task, test never ran; duration_seconds = 0
}
```
This mapping applies only to TERMINAL tasks; unknown xunit values are
report-layer policy (map to ERROR + WARNING log), not a schema concern.
The xunit `stage` attribute is deliberately not stored in results.json —
the verbatim xunit archive (see storage location below) retains it.

**Gap-fill write API** (`utils/results_parser.py`; replaces the deleted
v1 `write_results_json()` one-shot writer outright — there is no v3.1
equivalent of a single all-at-once write, because one run's results
arrive incrementally, one TF task at a time):
- `init_results_json(run_id, created_at, event, source, target,
  output_dir) -> Path` — creates `<output_dir>/<run_id>.json` with
  `verdict: null`, `results: []`. Refuses to overwrite an existing file
  by raising the builtin `FileExistsError` (its stdlib semantics are an
  exact match for "trying to create a file which already exists" — a
  filesystem precondition, not a schema-validation failure, so no new
  `EngeError` subclass was added for it).
- `upsert_task_result(path, task_entry) -> bool` — write-once per
  `task_id`. Absent task_id: validate, append, atomic rewrite, return
  `True`. Present with identical content: no-op, return `False`.
  Present with different content: raise `ConflictError` (new exception
  in `utils/errors.py`, alongside `ValidationError`) — the idempotency
  fence; never silently overwritten. File already finalized (non-null
  root verdict): raise `AlreadyFinalizedError` (a `ConflictError`
  subclass) regardless of content — the expected steady state on every
  re-report of a finalized run, not a data-drift signal; the cache layer
  logs it at DEBUG instead of WARNING for this reason (see "Caching
  failures never fail the report command" above).
- `finalize_root_verdict(path, expected_count) -> Optional[str]` — see
  derivation rules above. Under count: no-op, `None`. Exact count:
  derive and write, return the value (idempotent on repeat calls once
  finalized — returns the stored value, no rewrite). Over count: raise
  `ValidationError` (impossible state — something upstream recorded more
  tasks than expected).
- `write_xunit(run_id, task_id, xunit_bytes, output_dir) -> Path` —
  byte-verbatim write to `<output_dir>/<run_id>/<task_id>.xml` (one
  subdirectory per run; created as needed). **Conditionally present by
  contract**: a no-xunit ERROR task has an entry in results.json but no
  xml — gap-fill logic keys on the JSON entry, never on xml presence.
  Write-once: existing file with different bytes -> `ConflictError`;
  identical -> no-op.
- `parse_results_json(path)` — validates and returns the full nested
  dataclass structure (`ResultsJsonSchema` -> `TaskEntry` -> `PlanEntry`
  -> `TestEntry`).
- `results_dir()` in `utils/state_paths.py` is unchanged by this rework
  (XDG behavior stays the same).

Both JSON and xunit writes use the same tmp-file-then-`os.replace`
atomic-write pattern as `ManifestWriter.flush()` in `utils/manifest.py`
(duplicated, not imported — this module stays dependency-free of the
manifest layer by design).

**Storage layout** (XDG-compliant, config-overridable, mirrors the
manifest store, sibling symmetry with `~/.local/share/enge/runs/
<run_id>.json`):
```
~/.local/share/enge/results/<run_id>.json          # run envelope + all task/plan/test results
~/.local/share/enge/results/<run_id>/<task_id>.xml # one raw xunit file per task, verbatim bytes
```
(`XDG_DATA_HOME` respected; override via `[common] results_dir` in
config.) `results_dir()` in `utils/state_paths.py` follows
`resolve_runs_dir()`'s pattern: same `XDG_DATA_HOME` root as the
manifest store, `results/` instead of `runs/` as the leaf directory, and
(unlike the `resolve_*` family) also creates the directory on first
call, since the gap-fill writers each take their own `output_dir`
explicitly rather than sharing one central flush step.

**TF artifact URL**: stored as `TaskEntry.artifacts_url` (added
2026-07-24, see "Task entry" above) — copied verbatim from the
manifest's `requests[]` entry at harvest time, the same value dispatch
computed at request time (`dispatch/tf_send_request.py`). `enge
compare` (`compare/loader.py`) reads this field directly from the
results.json cache — it no longer joins the manifest's `requests[]`
itself for this value (fix/compare-manifest-decoupling, 2026-07-29; see
`docs/compare-consolidation.md` for the read-side layering rule).
The coldstore hyperlink is available directly from a cached
`results.json` entry without reconstruction or a manifest join;
superseded is this section's original plan to construct it externally
from `run_id`/`task_id` alone.

**Golden fixture MD5s** (`tests/fixtures/`; updated when the
dispatch-context-schema fields — `source`/`target`/`git_ref`/`event`/
`build_ids` — were added to every task entry below, and later when
`rerun_of`/`artifacts_url`/`plan`/`plan_filter` were added):
- `results_golden.json` (finalized multi-task run; 3 task entries —
  PASSED, FAILED-with-a-SKIPPED-plan, and an ERROR task with
  `plans: []`; root `verdict` = `"ERROR"`, the severity-max of
  PASSED/FAILED/ERROR): `7731ee7227a5b18562d56dd27c9cefd9`
- `results_golden_partial.json` (unfinalized run; root `verdict: null`,
  2 task entries against an assumed `expected_count=3` — a third
  tier1/aarch64 task has not reported in yet):
  `4d21cf882dfc26fd19f6c0a3a786257a`
- `results_golden_canceled.json` (finalized run mixing a CANCELED task,
  `plans: []`, `total_duration_seconds: 32.4`, with a PASSED task; root
  `verdict` = `"CANCELED"`, demonstrating CANCELED outranking PASSED in
  the severity ranking): `906acabeaec812e44f0109e64c8f7275`
- `results_golden_multiset.json` (finalized two-set run sharing one
  upgrade path across different tier/arch coordinates —
  `verification-alpha`/`tier0only`/`x86_64` and `verification-beta`/
  `tier1only`/`aarch64`, both `9.9`→`10.3`; root `verdict` = `"PASSED"`;
  demonstrates the envelope's `event`/`source`/`target` all `null`
  despite the matching path, while both task entries correctly carry
  `source`/`target`/`tier`/`arch`): `2c455f543b2b9b97bce636d09ddf6f28`

This schema is contract-pinned as of 2026-07-14. Cross-cutting contract:
schema changes require maintainer sign-off.
