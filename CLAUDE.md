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
auto-generated from `setup.cfg` via `%pyproject_buildrequires`, so a new pip
dependency must exist as a Fedora/EPEL package.

## Repo map

```
src/enge/
  __main__.py        entry point: arg pre-parse, logging, console config, exit-code mapping
  dispatch/          test dispatch: __main__ (flow + output), set_flow (per-spec pipeline),
                     tf_send_request (SubmitTest, payload build/POST, task recording),
                     pin_compose (compose resolution)
  report/            __main__ (tables, -o formats), concurrent_parser (parallel xunit fetch;
                     module-global RETURN_VALUE drives exit codes 2/3/4)
  rerun/             requalify FAILED/ERROR plans and re-dispatch
  cancel/            cancel TF tasks
  reportportal/      launch finish/enrich/delete operations (flag-verbs, two parallel
                     task-based vs --all-launches pipelines — known debt)
  utils/             opt_manager (config+CLI god object), arg_parser, console,
                     source_target_parser, tf_artifact (COPR/Brew), config_parser,
                     http_client (use this, never raw requests), errors, globals
tests/               unittest.TestCase style ONLY (see Conventions)
```

## Architecture facts you must know

- **`parsed_opts` is a lazily-initialized module-level singleton**
  (`utils/opt_manager.py`, `_LazyParsedOpts`) imported by every command
  module. Its `__getattr__` resolves attributes by searching config sections
  at runtime. Tests populate it via `parsed_opts.set(stub)` or the
  `parsed_opts.use(stub)` context manager. A migration to an explicit
  AppContext is planned — do not extend the singleton's surface; do not
  start the migration unless explicitly asked.
- **~60 deferred in-function imports exist as circular-import workarounds**
  caused by the singleton. Do not "clean them up" casually; they disappear
  with the DI migration.
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
- **Config precedence**: CLI > test-set > user config > bundled defaults.
  Env vars (`TESTING_FARM_API_TOKEN`, `REPORTPORTAL_API_TOKEN`) currently
  LOSE to config values — counterintuitive but documented; don't flip it
  silently.
- **Exit codes** (`utils/globals.py` + report's RETURN_VALUE): 0 ok,
  1 exception, 2 partial failure / test fail, 3 error hit, 4 missing
  results, 99 config error, 130 interrupt. Partial dispatch failures exit 2.
- **State files**: `/tmp/enge_latest_jobs` (task IDs of the current run;
  cleared once per invocation via the guard in dispatch/rerun mains —
  **never on --dry-run**), archive files in `~/.enge/jobs_archive/` with
  tags encoded in filenames (queried by regex via `--get-tag`). This
  filename-as-database design is slated for replacement by a JSON manifest
  store; don't build new features on filename tags.
- **Short flags are case-paired**: `-s/--source` and `-t/--target` (compose
  pair), `-S/--set` and `-T/--tier` (selection pair). `-t tier0` is a
  silently-accepted wrong compose name — keep help text and README examples
  exactly consistent with these semantics.

## Conventions

- **Tests**: unittest.TestCase style exclusively (tempfile.TemporaryDirectory,
  unittest.mock) — pytest-style bare functions are silently skipped by
  `unittest discover` and have caused phantom coverage before. Every new
  behavior gets a test that **fails when the behavior is removed**; when a
  test guards a guard/branch, mutation-check it (break the code, watch the
  test fail, restore). Tests must not require network.
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
- For external review, bundle with both refs:
  `git bundle create <name>.bundle devel <branch>` (the `devel..branch`
  range form creates a thin, uncloneable bundle).

## Known debt — planned, do not preempt piecemeal

Sequenced roadmap (do not start these as side effects of other work):
characterization tests for opt_manager → `RequestContext` dataclass
(kills the 14-parameter functions in source_target_parser and decomposes
`set_flow.process_request_spec`) → singleton → AppContext DI, module by
module → unify reportportal task/all-launches pipelines under subcommands →
manifest-based state store (XDG paths, retires filename tags) →
config-as-data (RHSM flag presets, source→target mapping table replacing the
`minor - 6` formula, RP event list).
