# enge CLI Reference

Send requests to and get results back from Testing Farm conveniently.

## Global Options

- `-h, --help` — show this help message and exit
- `-c, --config CONFIG` — Custom path to the config file.
- `-v, --verbose` — Increase output verbosity. -v for verbose, -vv for full debug.

## Subcommands

- `test` — Dispatch a job to the Testing Farm API endpoint.
- `report` — Report results for requested tasks.
- `rerun` — Parse given tasks and rerun specified jobs.
- `reportportal` — Manage ReportPortal launches.
- `cancel` — Cancel Testing Farm tasks.
- `migrate-archive` — Convert legacy archive files to JSON manifests.

## test

Send requests to Testing Farm conveniently.

### Options

- `-h, --help` — show this help message and exit
- `-c, --config CONFIG` — Custom path to the config file.
- `-v, --verbose` — Increase output verbosity. -v for verbose, -vv for full debug.
- `-s, --source SOURCE` — Source compose to be upgraded. Can be provided in a format of <major>.<minor> (e.g. 8.10), explicit compose name (e.g. RHEL-8.10.0-Nightly), symbolic RHUI compose (e.g. RHEL-8-rhui, RHEL-8-sap-hana-rhui, RHEL-8-sap-netweaver-rhui), CentOS Stream format (e.g. CentOS-Stream-9, stream-9, cs-9, stream9, cs9), or AMI source alias/name for Alma Linux or Rocky Linux (e.g. alma97, rocky97, 'AlmaLinux OS 9.7.20251118 x86_64'). AMI aliases are configured in [sources.ami]. If --target is not specified, the path is resolved to <major + 1>.<minor - 6> (e.g. 8.10 -> 9.4). Required unless --set is provided.
- `-t, --target TARGET` — Target compose for upgrade. Can be provided in a format of <major>.<minor> (e.g. 9.4) or explicit compose name (e.g. RHEL-9.4.0-Nightly). When --source is CentOS Stream, providing only the major version (e.g. 10) is allowed. If not specified, will be derived from source as <source_major + 1>.<source_minor - 6>
- `--copr COPR` — COPR build reference: alias:version (e.g., lp:pr123, lpr:main) or numeric BuildID. Aliases: lp=leapp, lpr=leapp-repository. If neither --copr nor --brew is specified, compose artifacts are used.
- `--brew BREW` — Test a brew build RC. Accepts either version reference (0.1.2-3) or TaskID. Both are validated via Brew API. Task IDs are resolved to their NVR, and the NVR is used in the Testing Farm payload. If neither of copr/brew is specified, the compose build is tested.
- `-T, --tier TIER` — Test tier(s) to be executed. Multiple tiers can be provided. Can be combined with --plan to override config plans with specific plans.
- `-S, --set SET` — Test set to be executed. Multiple sets can be provided. Can be combined with --plan to override set plans with specific plans.
- `--set-regex REGEX` — Regular expression to select test sets by name. Matches against names under [tests.set.<name>] (Python regex). Can be specified multiple times; expands to concrete set names before validation.
- `-p, --plan PLAN` — Plans to be executed. Multiple plans can be provided. Can be used standalone (without --tier/--set). When combined with --tier or --set, overrides any plans from config/set. Requires source and architectures via CLI or [tests] config when used standalone.
- `--test TEST` — Specify test name to be executed. Only used in conjunction with --plan or --tier.
- `--test-filter, --testfilter TESTFILTER` — Filter tests using FMF filter syntax. This allows fine-grained filtering of which tests to run.
- `--plan-filter, --planfilter PLANFILTER` — Filter plans using FMF filter syntax. This overrides any automatically generated plan filters from --tier.
- `--only-rhsm-mock-cdn` — Add tag:rhsm to the combined plan filter. This filters tests to only those tagged with 'rhsm'.
- `--no-rhsm` — Add tag:-rhsm to the combined plan filter. This excludes tests tagged with 'rhsm' from execution.
- `--only-rhsm-stage-cdn` — Add tag:rhsm to the combined plan filter, set product_phase=rc in TMT context, and set RHSM_MODE=stage in environment variables. This is used for testing RHSM stage environment.
- `--event EVENT` — Event name for ReportPortal launch gating and attributes. Can also be configured in test set configuration.
- `--git-url GIT_URL` — URL to the tests metadata repository. If not specified, uses the default one from the config file.
- `--git-ref GIT_REF` — Git ref (branch, tag, or commit) to checkout the test suite from. If not specified, uses the default one from the config file.
- `--architectures, --arch ARCHITECTURES` — Target architecture. Can be specified multiple times: --arch x86_64 --arch aarch64. If not specified, uses the default from the config file.
- `--pool POOL` — Specify a provisioning pool from Testing Farm.
- `--parallel-limit N` — Maximum number of plans to run in parallel. Overrides configuration files and hardcoded default (20).
- `--environment VAR=VAL` — Additional environment variables to be set in the request. Can be provided multiple times: --environment VAR1=VAL1 --environment VAR2=VAL2
- `--context KEY=VAL` — Additional TMT context key-value pairs. Merges into the generated context; warnings are shown on overrides (config, set, or duplicate CLI). Can be provided multiple times: --context key1=val1 --context key2=val2
- `--rp-launch RP_LAUNCH` — Override ReportPortal launch name from configuration. Sets TMT_PLUGIN_REPORT_REPORTPORTAL_LAUNCH environment variable.
- `--rp-description RP_DESCRIPTION` — Override ReportPortal description from configuration. Sets TMT_PLUGIN_REPORT_REPORTPORTAL_DESCRIPTION environment variable.
- `--wait` — Wait for successful API response after submitting request.
- `-n, --dry-run, --dryrun` — Print the payload that would be sent to Testing Farm without sending it.
- `--set-tag TAG` — Tag the archived task file with a custom tag. Can be used multiple times.
- `-o, --format {terminal,gitlab,json}` — Output format. 'terminal' (default): colored output. 'gitlab' (default when -o is used without value): markdown code blocks and tables. 'json': machine-readable JSON to stdout (suppresses other output). (default: `terminal`)
- `--list-sets` — List available test sets from config and exit.
- `--list-sets-detail` — List available test sets with full configuration detail and exit.

### Deprecated

- `--auto-tag` — Deprecated: context is now always recorded in manifests. This flag is a no-op and will be removed in a future release.

### Examples

```
examples:
  enge test -s 9.7 -T tier0                           # compose build
  enge test --copr lp:pr123 -s 9.7 -T tier0           # COPR PR build
  enge test --brew leapp-0.1-2.el9 -T tier0            # brew build
  enge test -S pre-release-smoke                       # pre-configured test set
  enge test -S pre-release-smoke -n                    # preview payload
```

## report

Parse task IDs, Testing Farm artifact URLs, or Testing Farm API request URLs from multiple sources.

### Options

- `-h, --help` — show this help message and exit
- `-c, --config CONFIG` — Custom path to the config file.
- `-v, --verbose` — Increase output verbosity. -v for verbose, -vv for full debug.
- `-f, --file FILE` — Filepath containing request IDs, artifact URLs, or request URLs to parse. Can be provided multiple times: -f file1 -f ~/file2
- `-i, --input ID_OR_URL` — Request ID, artifact URL, or request URL to parse from command line. Can be provided multiple times: -i id1 -i id2
- `--get-tag TAG` — Query for all task results under a given tag. Can be used multiple times.
- `--path PATH` — Custom path to archived task files directory.
- `--show-tests` — Display detailed test view. By default, only plan view is shown.
- `-s, --short` — Display shortened test and plan names.
- `-w, --wait` — Wait for the job to complete. Print the table afterwards
- `--download` — Download logs for requested run(s).
- `--skip-pass` — Skip PASSED results in table and log downloads.
- `--compare` — Build a comparison table for multiple run results.
- `--unify PLAN1=PLAN2` — Treat plan names as equivalent in 'plan1=plan2' format. Useful for comparing runs with renamed plans.
- `--show-ids` — Display only a list of UUIDs queried from the requested inputs.
- `-o, --format {terminal,gitlab,json}` — Output format. 'terminal' (default): colored output. 'gitlab' (default when -o is used without value): markdown code blocks and tables. 'json': machine-readable JSON to stdout (suppresses other output). (default: `terminal`)
- `--since DATE` — Only consider items from on or after DATE (YYYY-MM-DD or relative: 6h, 3d, 2w, 1m, 1y).
- `--until DATE` — Only consider items from on or before DATE (YYYY-MM-DD or relative: 6h, 3d, 2w, 1m, 1y).
- `--list` — List all runs in the manifest store as a table. Combinable with --set/--tier/--arch/--tag/--since/--until to narrow results.
- `--run RUN_ID` — Select a specific run by manifest ID. Use 'enge report --list' to browse available runs.
- `--set SET` — Filter runs by test set name (repeatable, OR within).
- `--tier TIER` — Filter runs by tier (repeatable, OR within).
- `--arch ARCH` — Filter runs by architecture (repeatable, OR within).
- `--tag TAG` — Filter runs by tag (repeatable, OR within).

### Examples

```
examples:
  enge report                                          # report latest run
  enge report -i <uuid>                                # report specific task
  enge report --list                                   # browse all runs
  enge report --list --set smoke --since 3d            # filter runs
  enge report --run <run_id>                           # report specific run
  enge report --tag regression --show-tests            # detailed test view
  enge report -f tasks.txt -w                          # wait for completion
  enge report --get-tag v1 --get-tag v2 --compare      # compare runs (legacy)
```

## rerun

Rerun failed or errored tasks from previous runs.

### Options

- `-h, --help` — show this help message and exit
- `-c, --config CONFIG` — Custom path to the config file.
- `-v, --verbose` — Increase output verbosity. -v for verbose, -vv for full debug.
- `-f, --file FILE` — Filepath containing request IDs, artifact URLs, or request URLs to parse. Can be provided multiple times: -f file1 -f ~/file2
- `-i, --input ID_OR_URL` — Request ID, artifact URL, or request URL to parse from command line. Can be provided multiple times: -i id1 -i id2
- `--get-tag TAG` — Query for all task results under a given tag. Can be used multiple times.
- `--set-tag TAG` — Tag the archived task file with a custom tag. Can be used multiple times.
- `-n, --dry-run, --dryrun` — Print the payload that would be sent to Testing Farm without sending it.
- `-o, --format {terminal,gitlab,json}` — Output format. 'terminal' (default): colored output. 'gitlab' (default when -o is used without value): markdown code blocks and tables. 'json': machine-readable JSON to stdout (suppresses other output). (default: `terminal`)
- `--error` — Rerun only jobs that reported ERROR state.
- `--fail` — Rerun only jobs that reported FAILED state.
- `--run RUN_ID` — Select a specific run by manifest ID for rerun.

### Deprecated

- `--auto-tag` — Deprecated: context is now always recorded in manifests. This flag is a no-op and will be removed in a future release.

## reportportal

Create and manage ReportPortal launches through the ReportPortal API.

### Options

- `-h, --help` — show this help message and exit
- `-c, --config CONFIG` — Custom path to the config file.
- `-v, --verbose` — Increase output verbosity. -v for verbose, -vv for full debug.
- `-f, --file FILE` — Filepath containing request IDs, artifact URLs, or request URLs to parse. Can be provided multiple times: -f file1 -f ~/file2
- `-i, --input ID_OR_URL` — Request ID, artifact URL, or request URL to parse from command line. Can be provided multiple times: -i id1 -i id2
- `--get-tag TAG` — Query for all task results under a given tag. Can be used multiple times.
- `--since DATE` — Only consider items from on or after DATE (YYYY-MM-DD or relative: 6h, 3d, 2w, 1m, 1y).
- `--until DATE` — Only consider items from on or before DATE (YYYY-MM-DD or relative: 6h, 3d, 2w, 1m, 1y).
- `-n, --dry-run, --dryrun` — Show what would be sent to ReportPortal without actually sending it.

### Subcommands

- `finish` — Finish ReportPortal launches by resolving task state.
- `enrich` — Enrich launches with Testing Farm artifact logs.
- `delete-logs` — Delete all log entries from ReportPortal launches.
- `delete-stale` — Delete stale launches (stopped/interrupted with no test items).
- `check` — Test ReportPortal connection and show sample data.

#### finish

##### Options

- `-h, --help` — show this help message and exit
- `-c, --config CONFIG` — Custom path to the config file.
- `-v, --verbose` — Increase output verbosity. -v for verbose, -vv for full debug.
- `--enrich` — Enrich launches with artifact logs before finishing.
- `--all` — Operate on all IN_PROGRESS launches (no task input needed).
- `-f, --file FILE` — Filepath containing request IDs, artifact URLs, or request URLs to parse. Can be provided multiple times: -f file1 -f ~/file2
- `-i, --input ID_OR_URL` — Request ID, artifact URL, or request URL to parse from command line. Can be provided multiple times: -i id1 -i id2
- `--get-tag TAG` — Query for all task results under a given tag. Can be used multiple times.
- `--since DATE` — Only consider items from on or after DATE (YYYY-MM-DD or relative: 6h, 3d, 2w, 1m, 1y).
- `--until DATE` — Only consider items from on or before DATE (YYYY-MM-DD or relative: 6h, 3d, 2w, 1m, 1y).
- `-n, --dry-run, --dryrun` — Show what would be sent to ReportPortal without actually sending it.

#### enrich

##### Options

- `-h, --help` — show this help message and exit
- `-c, --config CONFIG` — Custom path to the config file.
- `-v, --verbose` — Increase output verbosity. -v for verbose, -vv for full debug.
- `--all` — Enrich all launches (any status, no task input needed).
- `-f, --file FILE` — Filepath containing request IDs, artifact URLs, or request URLs to parse. Can be provided multiple times: -f file1 -f ~/file2
- `-i, --input ID_OR_URL` — Request ID, artifact URL, or request URL to parse from command line. Can be provided multiple times: -i id1 -i id2
- `--get-tag TAG` — Query for all task results under a given tag. Can be used multiple times.
- `--since DATE` — Only consider items from on or after DATE (YYYY-MM-DD or relative: 6h, 3d, 2w, 1m, 1y).
- `--until DATE` — Only consider items from on or before DATE (YYYY-MM-DD or relative: 6h, 3d, 2w, 1m, 1y).
- `-n, --dry-run, --dryrun` — Show what would be sent to ReportPortal without actually sending it.

#### delete-logs

##### Options

- `-h, --help` — show this help message and exit
- `-c, --config CONFIG` — Custom path to the config file.
- `-v, --verbose` — Increase output verbosity. -v for verbose, -vv for full debug.
- `--all` — Delete logs from all IN_PROGRESS launches.
- `-f, --file FILE` — Filepath containing request IDs, artifact URLs, or request URLs to parse. Can be provided multiple times: -f file1 -f ~/file2
- `-i, --input ID_OR_URL` — Request ID, artifact URL, or request URL to parse from command line. Can be provided multiple times: -i id1 -i id2
- `--get-tag TAG` — Query for all task results under a given tag. Can be used multiple times.
- `-n, --dry-run, --dryrun` — Show which logs would be deleted without actually deleting them.

#### delete-stale

##### Options

- `-h, --help` — show this help message and exit
- `-c, --config CONFIG` — Custom path to the config file.
- `-v, --verbose` — Increase output verbosity. -v for verbose, -vv for full debug.
- `--since DATE` — Only consider items from on or after DATE (YYYY-MM-DD or relative: 6h, 3d, 2w, 1m, 1y).
- `--until DATE` — Only consider items from on or before DATE (YYYY-MM-DD or relative: 6h, 3d, 2w, 1m, 1y).
- `-n, --dry-run, --dryrun` — Show which launches would be deleted without actually deleting them.

#### check

##### Options

- `-h, --help` — show this help message and exit
- `-c, --config CONFIG` — Custom path to the config file.
- `-v, --verbose` — Increase output verbosity. -v for verbose, -vv for full debug.

## cancel

Cancel running or queued Testing Farm tasks by sending DELETE requests.

### Options

- `-h, --help` — show this help message and exit
- `-c, --config CONFIG` — Custom path to the config file.
- `-v, --verbose` — Increase output verbosity. -v for verbose, -vv for full debug.
- `-f, --file FILE` — Filepath containing request IDs, artifact URLs, or request URLs to parse. Can be provided multiple times: -f file1 -f ~/file2
- `-i, --input ID_OR_URL` — Request ID, artifact URL, or request URL to parse from command line. Can be provided multiple times: -i id1 -i id2
- `--get-tag TAG` — Query for all task results under a given tag. Can be used multiple times.
- `--run RUN_ID` — Select a specific run by manifest ID to cancel.
- `-n, --dry-run, --dryrun` — Show which tasks would be cancelled without actually cancelling them.

## migrate-archive

One-time migration of ~/.enge/jobs_archive/ files into the manifest store. Non-destructive and idempotent.

### Options

- `-h, --help` — show this help message and exit
- `-c, --config CONFIG` — Custom path to the config file.
- `-v, --verbose` — Increase output verbosity. -v for verbose, -vv for full debug.
