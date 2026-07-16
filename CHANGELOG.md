# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added
- `results.json` schema v3.1: a new `utils/results_parser.py` module defines the contract-pinned per-run results format as a three-level hierarchy — run envelope (`schema_version`, `run_id`, `created_at`, `event`, `source`, `target`, nullable write-once `verdict`, `results[]`) → task entry (one TF request: `task_id`, `set`, `tier`, `arch`, composes, `dispatched_at`, `verdict`, `total_duration_seconds`, `plans[]`) → plan (one xunit testsuite: `name`, `verdict`, `tests[]`) → test (`name`, `verdict`, `duration_seconds`, optional timestamps/output/error_detail); verdict enum `PASSED|FAILED|SKIPPED|ERROR|CANCELED` at every level, with 8 validity rules governing CANCELED/ERROR/PASSED/FAILED ↔ plans/tests emptiness and root-verdict derivation (severity-max: ERROR > FAILED > CANCELED > PASSED > SKIPPED). Ships with a gap-fill write API — `init_results_json()`, `upsert_task_result()` (write-once per `task_id`, new `ConflictError` on differing-content conflicts), `finalize_root_verdict()` (derives the root verdict exactly once all expected tasks have landed), `write_xunit()` (byte-verbatim xunit colocated per task at `<run_id>/<task_id>.xml`) — plus `parse_results_json()` and the exported `XUNIT_RESULT_MAP` xunit→verdict contract constant for the future report-layer parser. New `results_dir()` state path (`~/.local/share/enge/results/`, XDG/config-overridable, mirrors the manifest store). This supersedes the flat single-task/single-plan `results.json` MVP schema from the same unmerged branch (one `set`/`tier`/`arch` + flat `tests[]`, with a one-shot `write_results_json()`) before it ever reached `devel` — that shape couldn't represent a multi-task dispatch (one manifest, N requests) or a multi-plan xunit result, so it never shipped and nothing user-facing changes as a result
- `enge report` now caches its results locally: manifest-backed invocations (default latest run, `--run`, or the structured filters `--set`/`--tier`/`--arch`/`--tag`) gap-fill `~/.local/share/enge/results/<run_id>.json` plus a byte-verbatim copy of each terminal task's xunit under `~/.local/share/enge/results/<run_id>/<task_id>.xml`, using the `results_parser` gap-fill API from the new `report/results_cache.py` module. This is a caching side effect only — it never changes what `enge report` prints or its exit code. Raw-input invocations (`-f/--file`, `-i/--input`) never write a cache, since they have no manifest run to key on. A task is cached once its TF state is terminal (`COMPLETE`/`ERROR`/`CANCELED`); `CANCELED` counts as terminal for this purpose (unlike the unrelated ReportPortal poll-completeness predicate, which is unaffected by this change). See CLAUDE.md "Results.json format" for the full write policy
- Config presets (`[tests.preset.<name>]`): reusable key bundles that test sets inherit via `extends = "<name>"`. Resolution chain: CLI > set > preset > `[tests]` > bundled defaults. Set keys wholly replace preset values (no deep-merge of nested tables). Chained presets and unknown targets are rejected at validation (exit 99). Configs with no presets and no `extends` resolve identically to before
- `[composes.target_map]`: config-driven default-target overrides, keyed by source `<major>.<minor>` (e.g. `"8.10" = "9.9"`), consulted only when the CLI/set/preset/`[tests]` chain produced no target and resolved exactly like an explicit `--target`. On a miss (absent or `""` key), enge still falls back to the existing `<major+1>.<minor-6>` formula and now logs a WARNING naming the key that would override it. Exists because the formula is only accurate at a compose's release time — final z-stream sources (`.10`) drift afterward, so the bundled config ships a short-lived pinned-current map (`8.10`→`9.9`, `9.6`→`10.0`, `9.7`→`10.1`) as a bridge until team-wide config layers over it. Note: `""` is only a miss within a single config layer — a bundled entry can be overridden by a higher layer but cannot be cleared by setting `""` there, since layer merging inherits the bundled value in that case
- ReportPortal launches created by enge now carry structured attributes mirroring the manifest context vocabulary: `run_id`, `set`, `tier`, `arch`, `event`, `source` (source compose name), `target` (target compose name; omitted on rerun launches where unavailable), `tool` (always `enge`), plus `parent_run_id` on rerun-created launches. `run_id` is present on all real (non-dry-run) launches. Attributes are deduplicated by key against pre-existing TMT context attributes; pre-existing entries are never overwritten
- Manifest request entries now record the RP launch UUID (`launch_uuid`) when enge creates a ReportPortal launch during dispatch or rerun; `null` when no launch was created
- Set-level `plan_filter` and `test_filter` config keys: define FMF filters per test set instead of passing `--plan-filter`/`--test-filter` on every invocation. Priority: CLI > set > tier-generated.
- JSON manifest store: dispatch state moved from `/tmp/enge_latest_jobs` + filename-tagged archive files to XDG-compliant JSON manifests under `~/.local/share/enge/runs/`. Each invocation writes a single versioned manifest with structured per-request metadata (task_id, set, tier, arch, plan, composes, artifacts URL)
- `enge report --list`: run browser that replaces visual filename scanning — displays a rich table of all manifests in the store, filterable by `--set/--tier/--arch/--tag/--since/--until`
- `--run <run_id>` flag on `report`, `rerun`, and `cancel`: select a specific manifest by ID (pair with `--list` to browse → pick → act)
- Structured manifest filters: `--set`, `--tier`, `--arch`, `--tag` match against manifest context and per-request fields (no regex needed)
- `enge migrate-archive`: one-time subcommand converting legacy `~/.enge/jobs_archive/` files into synthetic manifests with `origin="migrated"`. Non-destructive, idempotent
- Rerun lineage: rerun manifests carry `parent_run_id` linking to the original run, replacing the `.rerun` filename suffix
- Automatic `skip_guest_setup` pipeline setting for RHUI source composes (`RHEL-*-rhui`, `RHEL-*-sap-rhui`, `RHEL-*-sap-ha-rhui`)
- Short flags: `-s` (source), `-t` (target), `-T` (tier), `-p` (plan), `-S` (set), `-n` (dryrun)
- Verbosity control: `-v` for VERBOSE level, `-vv` / `--debug` for DEBUG level
- Output format selection: `-o` / `--format` with `terminal`, `json`, `gitlab` modes
- Test set discovery: `--list-sets` and `--list-sets-detail` for quick configuration review
- Shell completion via `argcomplete` (optional dependency)
- Sensitive field redaction in dry-run payload output
- Structured JSON output (`-o json`) with honest success/failure counts
- `enge reportportal` subcommands: `finish`, `enrich`, `delete-logs`, `delete-stale`, `check` — replacing the flag-verb grammar
- `finish --enrich` combined flow: enrich artifact logs then finish the launch in one command

### Changed
- **⚠ Behavior change — config file layering**: Configuration files now **layer** (bundled defaults < system/external `/etc/enge/` < user `~/.config/`) instead of first-found-wins. Each layer merges per-key over the layer below; nested TOML tables merge recursively. Keys omitted from a higher layer now inherit from the lower layer (previously, with wholesale file replacement, they effectively vanished). Empty-string `""` values inherit downward with a WARNING naming the file and key. `--config` replaces the user layer only — bundled and system layers remain active underneath. Full-copy external configs that contain every key are unaffected (merge is idempotent with identical content)
- **Display change — `report --list` table order**: the interactive rich table now lists runs oldest-first (newest at the bottom, adjacent to the shell prompt); json and gitlab output order is unchanged (newest-first)
- **⚠ Behavior change — RP launch naming**: Auto-generated ReportPortal launch names now use `{upgrade_path}~{tier}~{arch}` (e.g. `9to10~tier0~x86_64`). The old format was `{EVENT|SET}~YYYY-MM-DD~{tier}~{arch}` (e.g. `PRELIMINARY~2025-07-30~tier0~x86_64`). The upgrade_path token (e.g. `9to10`) is the baseline-path already carried in the TMT context — it stays durable across release rotation, unlike set names which are locally mutable config vocabulary. Event, date, and set are excluded from the name; granularity is available via the existing source/target/distro/event attributes. Reruns now share the original dispatch name for the same coordinates instead of using a `RERUN~` prefix — rerun identity is carried by the `rerun` tag and `parent_run_id` attribute. CLI `--rp-launch` and config `[reportportal].launch` still override auto-generation
- **Cancel reads manifest store**: `enge cancel` defaults to the latest manifest, and `--run <id>` selects a specific run. Legacy fallback still works for pre-migration files
- Task-ID resolution logic lifted from `report/__main__` to `utils/task_resolver` — all consumers (report, rerun, cancel, reportportal) import from the shared module
- **State store moved**: dispatch write path no longer touches `/tmp/enge_latest_jobs`, the CWD timestamped copy, or filename-tagged archive files. All state writes go to XDG JSON manifests. External scripts reading `/tmp/enge_latest_jobs` must migrate to the manifest store or `enge report --list --run`
- Report exit-code precedence is now severity-ranked error-dominates (3 > 2 > 4): a result set mixing test errors, failures, and missing results returns the most severe code (3, error) where it previously returned whichever was numerically highest (4, missing). Exit codes are now defined once as the `ExitCode` enum in `utils/globals.py`; missing results are rerun candidates and no longer mask a real error.
- Migrated terminal output from prettytable/ANSI to rich library (tables, panels, styled text)
- Logging now renders to stderr via dedicated console, keeping stdout clean for data output
- Request summary redesigned as a rich Panel with key-value grid
- Dispatch output batched — summaries printed after all requests complete
- `ReportPortalLaunch.generate_launch_payload` and `create_launch` accept `extra_tags: list[str] | None` (appended to default tags, deduplicated)
- Ruff lint gate added: `ruff check src tests` enforced in CI via pre-commit hook
- **Packaging migrated from `setup.cfg` to `pyproject.toml`** (PEP 621 `[project]` table, `setuptools.build_meta` backend). Package data (`enge_default_config.toml`) is now declared explicitly via `[tool.setuptools.package-data]` rather than relying on `include_package_data` + `MANIFEST.in` alone. No runtime dependency changes. CI's pip cache key (`cache-dependency-path`) now points at `pyproject.toml`. `black`'s target-version is now pinned explicitly via `[tool.black] target-version = ["py311"]` — previously black had no config source (`setup.cfg` is not read by black) and inferred its target per file; the new `pyproject.toml` made it infer from `requires-python` instead, silently changing the formatting gate. Pinning makes the gate explicit and change-controlled; 3 test files were reformatted accordingly (formatting only, no behavior change)
- **Version unified to CalVer `2026.7.12`**: `setup.cfg` (`2024.03.25`) and `enge.spec` (`0.1.3`, tag-tracked) previously diverged; both now carry the same `pyproject.toml`-sourced version string. Not zero-padded (`2026.7.12`, not `2026.07.12`) — PEP 440 normalizes leading zeros out of release segments during build, and a non-normalized `enge.spec` `Version:` would silently break `Source0`'s `enge-%{version}.tar.gz` tarball match

### Deprecated
- `--get-tag` — use `--tag` for native manifests (legacy archive fallback still works for pre-migration runs)
- `--auto-tag` — context is now always recorded in the manifest; the flag is a no-op
- `--jira` flag in `report` — use `-o gitlab` for merge-request-friendly output
- Old flag-verb spellings (`--finish`, `--enrich-logs`, `--delete-logs`, `--delete-stale`, `--test`, `--all-launches`) — use subcommands instead; old spellings emit a deprecation warning and will be removed in a future release

### Removed
- Dead functions: `merge_environment_variables`, `parse_test_sets` (source_target_parser), `get_config_value`, `validate_config_section` (config_parser), `_maybe_create_rp_launch` (dispatch), `_collect_inherited_tags` (rerun)
- Committed AI-generation deliberation comments and runtime `RerunReportPortalLaunch` subclass from `_create_rerun_launch_for_payload`
- `src/__init__.py` (src directory must not be a Python package)
- `setup.cfg`, including its embedded `[tox:tox]`/`[testenv]` block (unused — CI invokes `pytest` directly, not `tox`)

### Fixed
- Report run filters (`--set`/`--tier`/`--arch`/`--tag`) now accept multiple values (OR within a filter, AND across filters); previously repeated flags silently kept only the last value (`enge report --list --set A --set B` dropped A). Empty-string filter values are treated as unset
- Partial cancellation failures now exit 2 (partial failure) as documented; previously the cancel module's broad `except Exception` handler re-wrapped the internal `ValidationError` as an unmapped `EngeError`, producing exit 1
- Multi-set runs are now found by `--set <name>` for every dispatched set, not only the first; `find_runs` matches per-request set fields in addition to the run's context
- Rerun manifests now record `parent_run_id` and inherit the parent run's tags when tasks are resolved from a manifest; previously lineage was always empty despite the documented feature (the `ManifestWriter` was constructed with hardcoded `parent_run_id=None` and tags came only from CLI `--set-tag`)
- Multi-set dispatch (`-S setA -S setB`) previously resolved build artifacts from the first set's configuration for ALL sets; each set now resolves its own `copr_api`/`brew_api` `build_references` (per-family precedence, evaluated independently: CLI `--copr`/`--brew` > set-level config > run-level config)
- Set-level `copr_api`/`brew_api` sections now merge per-key over the run-level sections; previously a set defining only `build_references` lost the global endpoint URLs (`session_url`, `taskid_url`), breaking Brew resolution on multi-set dispatch
- ReportPortal timestamps (launch end-times, log upload times) were sent in local time mislabeled as UTC; now genuinely UTC
- Rerun RP launch creation crashed with `TypeError` — `ReportPortalLaunch()` was called without the required `ctx` argument on both the dry-run and real launch paths
- ReportPortal enrichment now uploads all artifact logs at INFO level uniformly. The previous per-artifact level mapping (ERROR for leapp/test logs, WARN for tmt-log) was both buggy (missing commas caused four names to fall through to INFO anyway) and premature — a curated level mapping will be designed separately
- Empty-string config values (`""`) are now treated as absent at every layer; the next
  precedence layer (default config) is inherited instead of being masked. A WARNING is
  logged per key where this substitution occurs. Previously `""` was treated as an
  explicit override, hiding the default silently.
- Tier selection now falls back to `[tests].tiers` (list) from the merged config when
  no tier is given via `-T` or per-set `tiers`. Previously the fallback accidentally
  picked up `[tests].tier` (the filter-definition mapping table), causing a `KeyError: 0`
  crash. The default config now ships `tiers = ['tier3']` as a catch-all.
- Missing or misconfigured Testing Farm endpoint URLs now raise a `ConfigurationError`
  (exit code 99) naming the missing key(s) instead of a bare `ValueError` (exit code 1).
- Validation errors that collect multiple detail lines now include those details in the
  exception message instead of only in the CRITICAL log. The full log output is unchanged.
- `-o json` mode no longer silences log output; logs go to stderr independently
- `-o json --dry-run` produces a single JSON document instead of interleaved payloads
- `enge reportportal` with `ConfigurationError` now returns exit code 99 (was 1), consistent with other subcommands
- All reportportal operations now skip CANCELED Testing Farm tasks uniformly (previously inconsistent: finish skipped, enrich did not)
- GitLab output format uses triple-backtick fences instead of Jira `{noformat}` tags
- External strings (compose names, plan names, test results) escaped to prevent rich markup injection
- Partial dispatch failures now exit with code 2; previously they incorrectly exited 0 due to a request-counting bug
- `setup.cfg` `url` field had spurious quotes and trailing comma
- Dryrun guard for latest-jobs file extracted to `maybe_clear_latest_jobs_file()` in tf_send_request; tests now verify the real production function
- Bare-function tests in `test_auto_tagging`, `test_concurrent_parser`, `test_tf_send_request` converted to `unittest.TestCase` so `python -m unittest discover` collects them
