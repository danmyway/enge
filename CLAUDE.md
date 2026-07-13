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
                     ExitCode via TaskResult.retval, severity-precedence aggregation)
  rerun/             requalify FAILED/ERROR plans and re-dispatch
  cancel/            cancel TF tasks
  reportportal/      launch finish/enrich/delete subcommands (unified pipeline
                     via operations.py + utils.py)
  migrate/           migrate-archive subcommand (legacy → manifest conversion)
  utils/             opt_manager (config loading + validation: ParsedOpts), app_context
                     (runtime DI container), globals (ExitCode + worst_exit_code),
                     task_resolver (shared task-ID resolution: manifest, legacy, -i/-f),
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
  (errors in parsed results) and 4 (missing/partial results), and the only
  one requiring severity precedence (3 > 2 > 4 > 0, error-dominates —
  missing results are rerun candidates and must not mask a real error),
  because it is the only command that grades multi-plan result sets.
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

**Ownership**: `enge report` will eventually write `results.json` after
parsing xunit; `enge dispatch` never touches it. As of this MVP, the
writer (`utils/results_parser.py`) exists and is fully tested, but
nothing calls it yet — report-subcommand integration (manifest lookup,
actual write calls during `enge report`) is deferred to a separate
feature branch. `results_parser.py` is standalone and does not import
manifest modules, by design.

**Schema (contract-pinned as of 2026-07-13)**:
```json
{
  "run_id": "01ARZ3NDEKTSV4RRFFQ69G5FAV",
  "request_timestamp": "2026-07-12T14:30:00Z",
  "set": "rhel8-to-rhel9",
  "tier": "tier0",
  "arch": "x86_64",
  "source": "rhel-8.10",
  "target": "rhel-9.4",
  "verdict": "PASSED",
  "tests": [
    {
      "name": "test_upgrade_9_to_10",
      "verdict": "PASSED",
      "duration_seconds": 120.5,
      "output": "test output or summary"
    },
    {
      "name": "test_rollback_scenario",
      "verdict": "FAILED",
      "duration_seconds": 45.2,
      "output": "Assertion failed: /proc/version mismatch",
      "error_detail": "Full error traceback if available"
    },
    {
      "name": "test_package_compat",
      "verdict": "SKIPPED",
      "duration_seconds": 0,
      "output": "Skipped: requires RHEL9+"
    }
  ],
  "total_duration_seconds": 245.7
}
```

CANCELED example (dispatch-level event — misconfiguration, timeout, user
interrupt — not a test-level outcome; `tests` is always empty,
`total_duration_seconds` reflects actual elapsed time before
cancellation):
```json
{
  "run_id": "01ARZ3NDEKTSV4RRFFQ69G5FAW",
  "request_timestamp": "2026-07-12T15:00:00Z",
  "set": "rhel8-to-rhel9",
  "tier": "tier0",
  "arch": "x86_64",
  "source": "rhel-8.10",
  "target": "rhel-9.4",
  "verdict": "CANCELED",
  "tests": [],
  "total_duration_seconds": 32.4
}
```

**Verdict enum** (top-level and per-test, identical vocabulary): `PASSED
| FAILED | SKIPPED | ERROR | CANCELED`. Unknown values are rejected at
validation. The root `verdict` is a required, explicit, caller-supplied
value — it is never auto-derived from per-test verdicts anywhere in this
module; a root verdict that disagrees with the "worst" per-test verdict
is accepted verbatim (see `tests/test_results_parser.py::
test_verdict_is_caller_supplied_not_derived`).

**Required top-level fields**: `run_id`, `request_timestamp` (ISO 8601 —
when the request was created, not when results.json was written),
`set`, `tier`, `arch`, `source`, `target`, `verdict`, `tests`,
`total_duration_seconds`. **Per-test required**: `name`, `verdict`,
`duration_seconds` (0 if skipped/not run). **Per-test optional**:
`output`, `error_detail` (only meaningful when verdict is FAILED or
ERROR — not otherwise enforced).

**CANCELED semantics**: verdict = `CANCELED` at root implies `tests`
MUST be empty — rejected by `ResultsJsonSchema` otherwise.
`total_duration_seconds` for a CANCELED run is independent of `tests`
(there are none) and reflects actual elapsed wall time.

**Duration arithmetic**: `total_duration_seconds` is not schema-validated
against `sum(tests[].duration_seconds)` — it is a caller-supplied value,
not derived or cross-checked, because the CANCELED case requires a
positive total with zero tests (impossible to reconcile with a strict
sum-equality rule), and TF-side timing overhead may not be attributable
to any single test. Tests still pin the golden fixture's own arithmetic
as an internal-consistency regression check (not a schema rule).

**Metadata strategy (baked-in with fallback lookup)**: `set`, `tier`,
`arch`, `source`, `target` are stored directly in `results.json` for
self-containment. When (future) `report` integration writes
`results.json`, it will attempt a manifest lookup by `run_id` to
populate these fields, falling back to caller-supplied values if the
manifest is unavailable — `results.json` remains valid either way.
`write_results_json()` itself only accepts explicit values; the
manifest-lookup/fallback logic lives in the report layer, not here.

**Storage location** (XDG-compliant, config-overridable, mirrors the
manifest store): `~/.local/share/enge/results/<run_id>.json`
(`XDG_DATA_HOME` respected; override via `[common] results_dir` in
config). Raw xunit is colocated verbatim (byte-for-byte, no
re-encoding) at `~/.local/share/enge/results/<run_id>.xml`. This
resolves an inconsistency in early drafts of this feature that floated
`~/.enge/results/` informally — that path doesn't match any existing
XDG convention in this codebase, so `results_dir()` in
`utils/state_paths.py` instead follows `resolve_runs_dir()`'s pattern:
same `XDG_DATA_HOME` root as the manifest store, `results/` instead of
`runs/` as the leaf directory. Unlike the `resolve_*` family,
`results_dir()` also creates the directory on first call (no separate
writer/flush step exists yet to do that for it).

**TF artifact URL**: NOT stored in `results.json`. The coldstore
hyperlink will be constructed externally later from the TF result URL
plus `run_id` — no field for this exists or is planned here.

**Golden fixture MD5s** (`tests/fixtures/`):
- `results_golden.json` (mixed PASSED/FAILED/SKIPPED/ERROR):
  `873497adc800d7eec35c1ecfa4098acc`
- `results_golden_canceled.json` (CANCELED, empty tests, positive
  duration): `56e5ef2f51c3b86a9d7bc9f16d25289d`

This schema is contract-pinned as of 2026-07-13. Cross-cutting contract:
schema changes require maintainer sign-off.

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
