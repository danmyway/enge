# CLAUDE.md

enge is a Python 3.11+ CLI that dispatches RHEL/CentOS Stream upgrade test
requests to Testing Farm, reports results parsed from xunit, reruns failures,
and manages ReportPortal launches. Successor to `tesar`. Single maintainer;
distributed as an RPM via COPR (Packit builds from `enge.spec`) and pip.

## Commands

```bash
pip install -e .                          # editable install (deps: rich, argcomplete, requests, copr, koji, lxml)
python -m unittest discover -s tests      # full suite — must stay green
python -m pytest tests/ -q                # what CI runs; collects the same tests
pre-commit run --all-files                # what the CI lint job runs
enge --help                               # smoke check after packaging changes
```

CI: `.github/workflows/ci.yml` — `lint` (pre-commit) + `test` (pytest, py3.11)
on PRs to `devel`. Packit builds RPMs for Fedora; runtime deps are
auto-generated from `pyproject.toml` via `%pyproject_buildrequires`, so a new pip
dependency must exist as a Fedora/EPEL package.

## Repo map

```
src/enge/
  __main__.py        entry point: arg pre-parse, logging, console config, exit-code mapping
  dispatch/          test dispatch: __main__ (flow + output), set_flow (per-spec pipeline),
                     tf_send_request (SubmitTest, payload build/POST, task recording),
                     pin_compose (compose resolution)
  report/            __main__ (tables, -o formats), concurrent_parser (parallel xunit fetch,
                     ExitCode via TaskResult.retval, severity-precedence aggregation),
                     results_cache (manifest-backed results.json gap-fill, see
                     "Results.json format")
  compare/           enge compare subcommand: engine (pure grouping/consolidation/
                     flakiness/exit-code logic), loader (manifest resolution +
                     results.json load + missing-cache policy), __main__ (rendering).
                     Read-only consumer of results.json — never parses xunit, never
                     writes a cache, see "Results.json format"
  rerun/             requalify FAILED/ERROR plans and re-dispatch
  cancel/            cancel TF tasks
  reportportal/      launch finish/enrich/delete subcommands (unified pipeline
                     via operations.py + utils.py)
  migrate/           migrate-archive subcommand (legacy → manifest conversion)
  utils/             opt_manager (config loading + validation: ParsedOpts), app_context
                     (runtime DI container), globals (ExitCode + worst_exit_code),
                     task_resolver (shared task-ID resolution: manifest, legacy, -i/-f),
                     manifest_resolution (shared invocation→manifest-object
                     resolution: report results cache, future `enge compare`),
                     test_attribute_builder (computed test attrs for AppContext),
                     manifest/state_paths/ulid/legacy_archive (manifest store + XDG paths),
                     arg_parser, console, source_target_parser, tf_artifact (COPR/Brew),
                     config_parser, http_client (use this, never raw requests), errors
tests/               unittest.TestCase style ONLY (see Conventions)
```

## Architecture facts you must know

- **`AppContext` is the runtime context object**, constructed once in
  `__main__` from the validated `ParsedOpts` output. For `action="test"`,
  computed test attributes (source_spec, target_spec, architectures,
  effective_tiers, environment_variables, etc.) are derived at construction
  time by `build_test_attributes()` in `utils/test_attribute_builder.py`
  and stored as frozen fields. For all other actions, defaults apply.
  `AppContext` is passed explicitly to every subcommand main.
  `ParsedOpts` (`utils/opt_manager.py`) handles config loading, env-var
  fallbacks, and validation — it no longer holds computed test attributes
  and no singleton wrapper exists.
- **Per-set artifact references** are resolved in
  `dispatch/set_flow._resolve_spec_artifact_refs()` with each artifact
  family (copr, brew) evaluated independently — a CLI override in one
  family does not suppress set-level resolution in the other.  Per-family
  effective api dict: per-key merge of run-level ctx dict (base) with
  set-level `copr_api`/`brew_api` from `spec.effective_values` layered
  over it; set-level keys win per-key, empty-string values inherit the
  run-level value.  References precedence: CLI `--copr`/`--brew` >
  merged dict's `build_references` > run-level references.  The
  run-level collapse in `build_test_attributes`
  (`effective_values = individual_test_sets[0]`) remains as the fallback
  source; single-set and no-set flows are unchanged.
- **39 deferred in-function imports remain** as circular-import workarounds.
  Do not "clean them up" casually; the surviving genuine cycles are:
  `source_target_parser ↔ dispatch.pin_compose`,
  `source_target_parser ↔ dispatch.context`,
  `report.__main__ ↔ report.concurrent_parser`,
  `rerun ↔ reportportal.__main__`,
  `reportportal_helper ↔ reportportal.__main__`,
  `reportportal.operations ↔ reportportal.__main__`.
- **`utils/console.py` exposes `console` as a proxy** delegating to a
  module-private `_current`; `configure_console(output_format)` swaps
  `_current`. Never rebind `console` itself; never construct ad-hoc Consoles
  for user output. Logging goes through `EngeLogHandler`, which writes to a
  dedicated stderr Console — data to stdout, diagnostics to stderr, always.
- **Output format contract** (`-o/--format`): `terminal` (rich), `gitlab`
  (markdown/``` fences — never Jira `{noformat}`, which lives only under the
  hidden `--jira` alias), `json` (exactly ONE parseable JSON document on
  stdout; failures included with honest total/successful/failed counts;
  diagnostics on stderr only).
- **Config file layering**: configuration files merge in three layers:
  bundled defaults (`enge.utils/enge_default_config.toml`, always loaded)
  < system/external (`/etc/enge/enge_default_config.toml`,
  `/etc/enge/enge_user_config.toml`) < user (`~/.config/enge_user_config.toml`,
  `~/enge_user_config.toml`, or `--config`).  Each layer merges per-key
  over the layer below; nested TOML tables merge recursively.  `""` = unset
  inherits from the layer below with a WARNING.  `--config` replaces the
  user layer only — bundled and system layers remain active.
  `DEFAULT_USER_CONFIG_PATHS` is retired; use `SYSTEM_CONFIG_PATHS` and
  `USER_CONFIG_PATHS` from `utils/globals.py`.
- **Config precedence (intra-config)**: CLI > test-set > preset > `[tests]` > bundled defaults.
  A test set opts into a preset via `extends = "<preset_name>"` in its
  section; the preset fragment lives at `[tests.preset.<name>]`.  Set keys
  wholly replace preset values (top-level per-key REPLACE, no deep-merge of
  nested tables); `""` in a set key inherits the preset value with a WARNING.
  Chained presets (`extends` inside a preset) → ConfigurationError (exit 99).
  Resolution is handled by `_resolve_preset()` in `test_attribute_builder.py`,
  called once per set before `resolve_effective_values`.  Configs with zero
  presets and zero `extends` resolve identically to before this feature.
  This operates on the already-merged config dict — the file layering above
  determines which values enter the dict.
  Env vars (`TESTING_FARM_API_TOKEN`, `REPORTPORTAL_API_TOKEN`) currently
  LOSE to config values — counterintuitive but documented; don't flip it
  silently.
- **Exit codes** are defined once in `utils/globals.py` as the `ExitCode`
  IntEnum; all modules return its members, never bare integers. The
  universal floor (0 success, 1 exception mapped by `__main__`, 99 config
  error, 130 interrupt) applies to every subcommand. Code 2 means "ran,
  partial failure" for dispatch (some requests failed), report
  (test failures), and cancel (some cancellations failed).
  Report is the ONLY subcommand that returns codes 3
  (errors in parsed results) and 4 (missing/partial results, including
  unrecognized TF overall values — rerun candidates, maintainer-ratified
  2026-07-17), and the only one requiring severity precedence
  (3 > 2 > 4 > 0, error-dominates — missing results are rerun candidates
  and must not mask a real error), because it is the only command that
  grades multi-plan result sets.
- **State files**: JSON manifests under `~/.local/share/enge/runs/<run_id>.json`
  (XDG_DATA_HOME respected; config-overridable). Each dispatch/rerun writes
  one versioned manifest (schema_version=1) with structured per-request
  metadata. A latest pointer at `~/.local/state/enge/latest` tracks the
  newest run. The old `/tmp/enge_latest_jobs` + `~/.enge/jobs_archive/`
  filename-tagged model is retired; a read-only legacy bridge
  (`utils/legacy_archive.py`) provides backward-compatible reading for
  pre-migration runs. `enge migrate-archive` converts old files to synthetic
  manifests with `origin="migrated"`. Manifests carry dispatch facts only
  (no result fields) — a results cache is a separate future PR.
- **Short flags are case-paired**: `-s/--source` and `-t/--target` (compose
  pair), `-S/--set` and `-T/--tier` (selection pair). `-t tier0` is a
  silently-accepted wrong compose name — keep help text and README examples
  exactly consistent with these semantics.
- **RP operations gate on TF job completeness** via the unified
  `_is_tf_task_incomplete` predicate in `reportportal/operations.py`
  (NEW/QUEUED/RUNNING/CANCELED are all incomplete; CANCELED is skipped).
  RP launch status is downstream bookkeeping, never a correctness gate.
  Finish/enrich are order-independent once the TF job is terminal;
  incomplete runs yield incomplete log sets.
- **RP launch-attribute schema** is a contract mirroring the manifest
  context vocabulary: `run_id`, `set`, `tier`, `arch`, `event`,
  `source` (source compose name), `target` (target compose name;
  omitted on rerun launches where unavailable), `tool` (always `enge`),
  plus `parent_run_id` on rerun launches. Keys are never added without
  maintainer sign-off. Attributes are deduplicated by key against
  pre-existing TMT context entries (pre-existing win). `run_id` is
  omitted on dry-run (no manifest exists).
- **RP launch naming** uses the grammar `{upgrade_path}~{tier}~{arch}`
  (e.g. `9to10~tier0~x86_64`).  `upgrade_path` is the baseline-path
  token already carried in the TMT context — durable across release
  rotation, unlike set names (locally mutable config vocabulary).
  Event, date, and set are excluded from the name — event is carried
  by the `event` attribute, set by the `set` attribute, time filtering
  uses RP-native `startTime`.  Empty segments are omitted; all-empty
  returns None; `ENGE_Launch` no-context fallback unchanged.  Rerun
  launches use the payload's own tmt context for naming (no parent
  manifest dependency).  The canonical implementation is
  `_generate_auto_launch_name` in `utils/source_target_parser.py`.
  Precedence: CLI `--rp-launch` > config `[reportportal].launch` >
  auto-generation.

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
cache. `enge report --compare` is a one-release deprecation alias that
delegates to `enge compare` and is therefore also read-only, unlike every
other manifest-backed `enge report` invocation (see "Compare consolidation
policy" below).

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
key), `set`, `tier`, `arch`, `source_compose`/`target_compose` (required
keys, nullable values — manifest parity), `dispatched_at` (ISO 8601, from
manifest request), `verdict` (task-level, non-null), `total_duration_seconds`
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
discarding them at harvest time, it adds no new manifest field). `plan`
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
manifest's `requests[]` entry, the same value `compare`'s
`TASK REFERENCE` footer already joins in from the manifest separately.
The coldstore hyperlink is now available directly from a cached
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

## Compare consolidation policy

`enge compare` (`src/enge/compare/`) is a **read-only** consumer of the
`results.json` contract above — it never parses xunit, never calls
Testing Farm, never writes a cache. `compare/engine.py` is pure (no I/O);
`compare/loader.py` resolves manifests via the shared
`resolve_manifests_for_invocation` and loads each matched run's
`results.json`; `compare/__main__.py` renders. It replaced `enge report
--compare`'s `build_table_comparison` (deleted) and `--unify` (deleted;
no replacement — plan names in `results.json` are always verbatim).

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
manifest is single-set (`len({r.get("set") for r in
manifest["requests"]}) <= 1`), mirroring `report/results_cache.py`'s own
`is_single_set` harvest-time fallback exactly. A multi-set manifest with
no per-task value gets no fallback and stays `None`; `compare/__main__.py`
renders a `None` descriptor as the em dash, never the literal string
"None" — the em dash is always a render-time substitution, never a
stored value.

**Deprecation alias**: `enge report --compare` delegates to `enge compare`
for one release, emitting a WARNING. Unlike every other manifest-backed
`enge report` invocation, the alias does **not** gap-fill the results
cache while delegating (maintainer ruling, 2026-07-17) — run `enge report
--run <run_id>` first if the cache needs populating. `--splitarch`/
`--splitpath` are compare-only flags; the alias path's `report` action
parser doesn't define them, so `getattr(ctx.cli_args, "splitarch",
False)` (and the `splitpath` equivalent) default the alias to the
unsplit, one-table-per-tier shape.

## Conventions

- **Tests**: unittest.TestCase style exclusively (tempfile.TemporaryDirectory,
  unittest.mock) — pytest-style bare functions are silently skipped by
  `unittest discover` and have caused phantom coverage before. Every new
  behavior gets a test that **fails when the behavior is removed**; when a
  test guards a guard/branch, mutation-check it (break the code, watch the
  test fail, restore). Tests must not require network.
  - Tests construct contexts via `tests/_helpers.make_app_context`; there is
    no module-level runtime state to patch.
  - **Bypassing `ParsedOpts.__init__`** for isolated method tests: use
    `object.__new__(ParsedOpts)` then set `_validation_hooks = {}`,
    `config = <dict>`, `cli_args = get_arguments(args=[...])`, and
    `options = po._get_config_options()`. Most validation methods only read
    these four attributes.
- **Commits**: imperative subject ≤72 chars, body explains why. Atomic and
  bisectable — every commit must compile standalone
  (`git rebase -i devel --exec "python -m py_compile $(git ls-files '*.py')"`).
  Before a branch is under external review, fold fixes into the commit that
  introduced the code; after review starts, append fixup commits instead.
- **CHANGELOG.md**: Keep-a-Changelog, entries under `[Unreleased]`. Any
  user-visible behavior change (especially exit codes, flags, file
  locations) MUST get an entry in the same PR.
- **Docs drift is a bug**: changing a flag means updating argparse help,
  the epilog examples in `arg_parser.py`, and the README in the same commit.
- **Timestamps**: every persisted or API-emitted timestamp is UTC
  (`datetime.now(timezone.utc)`); comparisons (`--since`/`--until`) are UTC;
  localization is display-only and currently not done. Never store or send
  naive local time — two shipped bugs came from this.
- **Secrets**: anything printed or logged that could contain a payload goes
  through `redact_sensitive()` (utils). Real credentials only in the actual
  API request. No partial-token prints (`token[:10]` is still a leak).
- **Rich markup**: any external string (xunit test/plan names, TF responses)
  rendered via rich must pass `rich.markup.escape()` or be wrapped in
  `Text(...)` — bracketed test names have crashed rendering before.
- **HTTP**: use `utils/http_client.py` (sessions, retries); never raw
  `requests.get/post` in new code.
- **Lint**: ruff config carries a per-file-ignores "ratchet list" for legacy
  monoliths — never ADD entries; remove them as files are refactored.
- **mypy gate**: `mypy.ini` carries a per-module `disable_error_code`
  baseline — the same ratchet rule as ruff: never add entries, never widen a
  module's code list. `call-arg` is NEVER disabled at module level —
  pre-existing call-arg baseline lines use inline `# type: ignore[call-arg]`
  with a reason; new call-arg errors must be fixed, not suppressed. CI runs
  tests under a TZ matrix (UTC + America/Los_Angeles) and a pytest/unittest
  collector-parity guard; invoke tests as `python -m pytest` from the repo
  root.
- For external review, bundle with both refs:
  `git bundle create <name>.bundle devel <branch>` (the `devel..branch`
  range form creates a thin, uncloneable bundle).

## Known debt — planned, do not preempt piecemeal

Sequenced roadmap (do not start these as side effects of other work):
~~characterization tests for opt_manager~~ ✓ →
~~`RequestContext` dataclass~~ ✓ →
~~singleton → AppContext DI, module by module~~ ✓ →
~~unify reportportal task/all-launches pipelines under subcommands~~ ✓ →
~~manifest-based state store (XDG paths, retires filename tags)~~ ✓ →
config-as-data (RHSM flag presets, source→target mapping table replacing the
`minor - 6` formula, RP event list).

Planned breaking config change (deferred, coordinate with users):
rename `[tests].tier` (filter-definition table) to `[tests].tier_definitions` to
eliminate the naming collision with the `[tests].tiers` selection list; requires a
migration guide and a config-schema version bump.
