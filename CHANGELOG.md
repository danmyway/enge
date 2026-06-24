# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added
- Set-level `plan_filter` and `test_filter` config keys: define FMF filters per test set instead of passing `--plan-filter`/`--test-filter` on every invocation. Priority: CLI > set > tier-generated.
- JSON manifest store: dispatch state moved from `/tmp/enge_latest_jobs` + filename-tagged archive files to XDG-compliant JSON manifests under `~/.local/share/enge/runs/`. Each invocation writes a single versioned manifest with structured per-request metadata (task_id, set, tier, arch, plan, composes, artifacts URL)
- `enge report --list`: run browser that replaces visual filename scanning — displays a rich table of all manifests in the store, filterable by `--set/--tier/--arch/--tag/--since/--until`
- `--run <run_id>` flag on `report` and `rerun`: select a specific manifest by ID (pair with `--list` to browse → pick → act)
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
- **State store moved**: dispatch write path no longer touches `/tmp/enge_latest_jobs`, the CWD timestamped copy, or filename-tagged archive files. All state writes go to XDG JSON manifests. External scripts reading `/tmp/enge_latest_jobs` must migrate to the manifest store or `enge report --list --run`
- Report exit-code precedence is now severity-ranked error-dominates (3 > 2 > 4): a result set mixing test errors, failures, and missing results returns the most severe code (3, error) where it previously returned whichever was numerically highest (4, missing). Exit codes are now defined once as the `ExitCode` enum in `utils/globals.py`; missing results are rerun candidates and no longer mask a real error.
- Migrated terminal output from prettytable/ANSI to rich library (tables, panels, styled text)
- Logging now renders to stderr via dedicated console, keeping stdout clean for data output
- Request summary redesigned as a rich Panel with key-value grid
- Dispatch output batched — summaries printed after all requests complete
- `ReportPortalLaunch.generate_launch_payload` and `create_launch` accept `extra_tags: list[str] | None` (appended to default tags, deduplicated)
- Ruff lint gate added: `ruff check src tests` enforced in CI via pre-commit hook

### Deprecated
- `--get-tag` — use `--tag` for native manifests (legacy archive fallback still works for pre-migration runs)
- `--auto-tag` — context is now always recorded in the manifest; the flag is a no-op
- `--jira` flag in `report` — use `-o gitlab` for merge-request-friendly output
- Old flag-verb spellings (`--finish`, `--enrich-logs`, `--delete-logs`, `--delete-stale`, `--test`, `--all-launches`) — use subcommands instead; old spellings emit a deprecation warning and will be removed in a future release

### Removed
- Dead functions: `merge_environment_variables`, `parse_test_sets` (source_target_parser), `get_config_value`, `validate_config_section` (config_parser), `_maybe_create_rp_launch` (dispatch), `_collect_inherited_tags` (rerun)
- Committed AI-generation deliberation comments and runtime `RerunReportPortalLaunch` subclass from `_create_rerun_launch_for_payload`
- `src/__init__.py` (src directory must not be a Python package)

### Fixed
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
