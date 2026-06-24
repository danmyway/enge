# Table of contents
1. [Introduction](#enge)
2. [Prerequisites](#prerequisites)
   1. [API configuration](#api-configuration)
       1. [Testing farm API key](#testing-farm-api-key)
   2. [Cloud Resources Tag](#cloud-resources-tag)
3. [Setting up](#setting-up)
   1. [Installation](#installation)
       1. [Install](#install)
       2. [Set up the configuration file](#set-up-the-configuration-file)
          1. [Configuration locations and precedence](#configuration-locations-and-precedence)
          2. [Default configuration and version check](#default-configuration-and-version-check)
          3. [System-wide configuration (RPM installs)](#system-wide-configuration-rpm-installs)
   2. [Usage](#usage)
       1. [Commands](#sub-commands)
          1. [Test](#test)
             1. [RHSM-Specific Filtering](#rhsm-specific-filtering)
             2. [Compose resolution and target derivation](#compose-resolution-and-target-derivation)
          2. [Test Sets](#test-sets)
          3. [TMT Context Integration](#tmt-context-integration)
          4. [ReportPortal Integration](#reportportal-integration)
             1. [ReportPortal Launch Management](#reportportal-launch-management)
          5. [Report](#report)
          6. [Rerun](#rerun)
   3. [Troubleshooting configuration and validation](#troubleshooting-configuration-and-validation)
          7. [Manifest Store and Run History](#manifest-store-and-run-history)


ENGE
=
### ENGE is a New Generation of the [tesar](https://github.com/danmyway/tesar) Tool
#### Send requests to the Testing Farm API through the command line interface
This tool mimics the ability of the Packit project to dispatch a test request to the Testing Farm endpoint.
The features are custom tailored around for the user's comfort.
The additional value is not only in the ability to quickly dispatch a test request job without the knowledge of any specific build IDs, but also in the possibility to quickly get and read results from the command line interface.

# Prerequisites

### API Configuration

#### Testing Farm API Key

To be able to send requests to Testing Farm API, you need to obtain the API key.
Please, kindly refer to [testing farm onboarding](https://docs.testing-farm.io/general/0.1/onboarding.html)
to request the API key.<br>
Add the obtained api_key to the config file as instructed below, or export it as an environment variable:

```bash
export TESTING_FARM_API_TOKEN="your-api-key"
```

> **Priority**: config value > environment variable. If `api_key` is set in `enge.toml`, the environment variable is ignored.

### Cloud Resources Tag

Each team using the Testing Farm to run test efforts has a BusinessUnit tag assigned.<br>
Those are important to use for correct reporting efforts of a cloud spend for each team.<br>
Ask peers in your team for the tag value.

# Setting up

### Installation

#### Enable the copr repository

```
sudo dnf copr enable danmyway/enge
```

If you are brave enough, though not advised, you can try out the development repository.

```
sudo dnf copr enable danmyway/enge-devel
```

#### Install

```
dnf install enge
```
>__NOTE__:<br>Additionally the tool should be installable from the repository root with `pip install .`

#### Set up the configuration file
The template for the config file is available in the root of the repository. The default locations for the config file are `~/.config/enge_user_config.toml`, `~/enge_user_config.toml`, or `/etc/enge/enge_user_config.toml`. A custom path to a config file can be specified through the commandline option `-c`.<br>
In case of any question, please reach out to the project maintainer(s).

##### Configuration locations and precedence
When loading configuration, enge applies these rules:

- **CLI-provided path**: If `-c/--config` is used, that file is tried first.
- **User locations (searched in order)**:
  - `~/.config/enge_user_config.toml`
  - `~/enge_user_config.toml`
  - `/etc/enge/enge_user_config.toml`
- The first existing file in the search order above is used as the user configuration.
- The user configuration is then **merged over defaults** (see below). Nested tables are merged recursively; user values take precedence.

##### Default configuration and version check
enge ships with a default configuration used as a base for all settings. Defaults are loaded from the first available location:

- `/etc/enge/enge_default_config.toml` (system-wide, installed by the RPM)
- Bundled example inside the package (`enge.utils/enge_default_config.toml`)

>__NOTE__: Pre-configured default configuration file will be distributed in the leapp-tests repository.

If you maintain the default configuration in a different location (for example,
checked out from a private repository), point enge to it by adding
`default_config_path = '/path/to/enge_default_config.toml'` either at the root of
your `enge.toml` or inside the `[common]` section. When set, this path takes
precedence over the system-wide default.

If both are present, enge compares their `version` fields (semantic-like `X.Y.Z`, e.g. `2025.08.27`), and **logs a warning** when the system default under `/etc/enge/enge_default_config.toml` appears older than the bundled example, suggesting an update.

Key default paths from the bundled defaults (can be overridden in your `enge.toml`):

- **Manifest store**: `~/.local/share/enge/runs/` (XDG_DATA_HOME respected)
- **Latest pointer**: `~/.local/state/enge/latest`
- **Legacy archive** (read-only bridge): `~/.enge/jobs_archive/`
- **Logs directory**: `/var/tmp/enge/logs/`

##### System-wide configuration (RPM installs)
When installed via RPM, the following files are provided under `/etc/enge/`:

- `enge_default_config.toml` — system default configuration used as a base
- `enge_user_config.toml` — an empty user configuration file (marked as `noreplace` so upgrades do not overwrite local changes)

### Usage

#### Sub-Commands
Enge provides several commands for comprehensive test workflow management:<br>
`test` feeds the request payload with provided config options or arguments and dispatches a test job to the Testing Farm.<br>
`report` outputs the test results back to the command line.<br>
`rerun` re-dispatches failed or errored test jobs.<br>
`cancel` cancels running or queued Testing Farm tasks.

#### Common Options

**Output format (`-o` / `--format`)**

Controls how results are rendered. Available modes:
- `terminal` (default) — colored rich output with panels and tables
- `json` — machine-readable JSON on stdout; logs go to stderr
- `gitlab` — plain text wrapped in triple-backtick fences for GitLab MR comments

The hidden `--jira` flag produces `{noformat}` fences for Jira tickets.

**Verbosity (`-v` / `--verbose`)**

- Default — INFO-level messages
- `-v` — VERBOSE-level (extra detail without full debug noise)
- `-vv` or `--debug` — DEBUG-level (all internal tracing)

**Dry run (`-n` / `--dry-run`)**

Shows what would be sent without making API calls. Sensitive fields in payloads are redacted. The legacy spelling `--dryrun` is accepted as an alias.

**Short flags**

| Long form | Short |
|-----------|-------|
| `--source` | `-s` |
| `--target` | `-t` |
| `--tier` | `-T` |
| `--plan` | `-p` |
| `--set` | `-S` |
| `--dry-run` | `-n` |

**Test set discovery (`--list-sets` / `--list-sets-detail`)**

Quick-look at configured test sets without running anything:
```bash
enge test --list-sets            # brief table: name, path, arch, tiers
enge test --list-sets-detail     # full table: all set fields
```

**Shell completion**

Tab completion is available when `argcomplete` is installed:
```bash
pip install argcomplete
eval "$(register-python-argcomplete enge)"
```

##### Test

The goal of enge is to make requesting test jobs as easy as possible.<br>
A default artifact to install (if not specified otherwise) is the one available in the compose.<br>
The `--brew` and `--copr` options denote which type of a build artifact is to be requested for testing.<br>

**COPR Builds:**
- **Reference Format**: `alias:reference` (e.g., `lp:pr123`) or direct Build ID (integer, e.g., `12345`)
- **Package Aliases**:
  - `lp` → leapp
  - `lpr` → leapp-repository
- **Automatic Package Resolution**: All packages from the build are fetched from COPR API and included as full NVRAs (name-version-release.arch)
- **Chroot Derivation**: Automatically derived from source version (e.g., source 8.10 → epel-8-x86_64)
- **Multi-Package Builds**: When a COPR build produces multiple packages (e.g., leapp-repository builds produce leapp-upgrade-el8toel9, leapp-upgrade-el8toel9-deps, etc.), all non-source packages are automatically included in the Testing Farm payload

**Brew Builds:**
- Provide either the NVR (e.g., leapp-0.16.0-1.el9) or the TaskID
- Both are validated via the Brew API
- Task IDs are automatically resolved to their corresponding NVR
- The NVR is always used in the Testing Farm payload for consistency<br>
Multiple `--plan` options can be specified and will be dispatched in separate jobs.
`--tier` options allow you to run predefined test tiers from your configuration.
`--set` options allow you to use pre-configured test sets (see Test Sets section below).
`--set-regex` allows selecting multiple test sets by Python regular expression (expanded to concrete set names before validation).
**Plan Override Behavior:** When using `--plan` with `--tier` or `--set`, the CLI plans override any `plans` defined in configuration or test sets.
When using `--plan-filter` or `--test-filter` to specify a singular test, it is disallowed to request multiple `--plan` options in one command (legacy spellings `--planfilter`/`--testfilter` are accepted).<br>
Use `--wait` if waiting for a successful response from the endpoint is required.
If for any reason you would need to verify the validity of the raw payload, use `--dry-run` to get it pretty-printed to the command line.

Use `--set-tag` to attach custom tags to the dispatch manifest (can be used multiple times). Tags are stored in the manifest's `tags` array and queryable via `enge report --list --tag <tag>`.
The `--source` argument is required unless using `--set` (which defines source in the configuration).

```
# Test copr build by Build ID
enge test --copr 12345 --source 8.10 --plan /plans/tier0

# Test leapp PR#123 with plan named basic_sanity_check
enge test --copr lp:pr123 --source 9.7 --plan /plans/tier0/basic_sanity_checks

# Test leapp-repository PR#456 on RHEL 8 to 9
enge test --copr lpr:pr456 --source 8.10 --plan /plans/tier0

# Run every test plan for brew build 0.12-3 on all composes
enge test --brew 0.12-3 --plan /plans

# Specify more individual test plans
enge test --brew 0.12-3 --plan /plans/tier0/basic_sanity_checks --plan /plans/tier1/whatever_else

# Test using predefined tiers with leapp PR
enge test --copr lp:pr123 --source 9.7 --tier tier0 --tier tier1

# Test using a predefined test set
enge test --set pre-release-smoke

# Select multiple sets by regex (names under [tests.set.<name>])
enge test --set-regex '^pre-release-.*'

# Combine exact and regex selection (deduplicated, order preserved)
enge test --set pre-release-smoke --set-regex 'regression-[0-9]+'

# Test with custom tags (stored in manifest)
enge test --copr lp:pr123 --source 9.7 --tier tier0 --set-tag regression --set-tag pr123

# Use Task ID - automatically resolved to NVR in payload
enge test --brew 12345678 --tier tier0

# Combine tier with specific plans (CLI plans override config `plans`)
enge test --tier tier0 --plan /plans/custom-plan --source 9.7

# Override test set `plans` with CLI plans
enge test --set pre-release-smoke --plan /plans/override-plan

# Test multiple brew packages (each gets NVR in TMT context)
enge test --brew leapp-0.16.0-1.el9 --brew leapp-repository-0.1-32.el9 --tier tier0

# Provide additional TMT context values (CLI overrides config)
enge test --source 9.7 --plan /plans/tier0 --context event=nightly --context custom_key=custom_value

# Multiple architectures in non-set mode create one request per architecture
enge test --source 9.7 --plan /plans/tier0 --arch s390x --arch x86_64

# Test both leapp and leapp-repository PRs
enge test --copr lp:pr123 --copr lpr:321 --source 9.7 --tier tier0
# (All packages from the build are automatically included)

# Filter to only RHSM-tagged tests
enge test --source 9.7 --tier tier0 --only-rhsm-mock-cdn

# Exclude RHSM-tagged tests
enge test --source 9.7 --tier tier0 --no-rhsm

# Filter to RHSM tests with stage environment configuration
enge test --source 9.7 --tier tier0 --only-rhsm-stage-cdn

# Test with CentOS Stream as source (various alias formats supported)
enge test --source CentOS-Stream-9 --plan /plans/tier0
enge test --source stream-9 --plan /plans/tier0
enge test --source cs-9 --plan /plans/tier0
enge test --source stream9 --plan /plans/tier0

# Test with Alma Linux / Rocky Linux as source (AMI-based)
# (configure aliases under [sources.ami] in enge.toml)
enge test --source alma97 --plan /plans/tier0 --arch x86_64
enge test --source rocky97 --plan /plans/tier0 --arch aarch64

# Or pass direct AMI names
enge test --source "AlmaLinux OS 9.7.20251118 x86_64" --plan /plans/tier0
enge test --source "Rocky-9-EC2-Base-9.7-20251123.2.aarch64" --plan /plans/tier0
```

###### RHSM-Specific Filtering

Enge provides specialized options for testing Red Hat Subscription Manager (RHSM) functionality:

**`--only-rhsm-mock-cdn`**
- Adds `tag:rhsm` to the combined plan filter
- Filters test execution to only tests tagged with 'rhsm' in test metadata
- Can be combined with tiers, test sets, or standalone

**`--no-rhsm`**
- Adds `tag:-rhsm` to the combined plan filter
- Excludes tests tagged with 'rhsm' from execution
- Useful for running all tests except RHSM-specific ones
- Can be combined with tiers, test sets, or standalone

**`--only-rhsm-stage-cdn`**
- Adds `tag:rhsm` to the combined plan filter (same as `--only-rhsm-mock-cdn`)
- Additionally configures the stage environment:
  - Sets `RHSM_MODE=stage` in environment variables
  - Sets `product_phase=rc` in TMT context
- Used specifically for testing RHSM against the stage CDN environment

**Examples:**
```bash
# Run tier0 tests that are RHSM-tagged only
enge test --source 9.7 --tier tier0 --only-rhsm-mock-cdn

# Run tier0 tests excluding RHSM-tagged tests
enge test --source 9.7 --tier tier0 --no-rhsm

# Run RHSM tests with stage environment configuration
enge test --source 9.7 --tier tier0 --only-rhsm-stage-cdn

# Combine with test sets
enge test --set pre-release-smoke --only-rhsm-mock-cdn

# Exclude RHSM tests from a test set
enge test --set pre-release-smoke --no-rhsm

# Use with specific plans
enge test --source 9.7 --plan /plans/subscription --only-rhsm-stage-cdn
```

**Filter Behavior:**
- Without RHSM flags: `tag:8to9 & enabled:true`
- With `--only-rhsm-mock-cdn`: `tag:8to9 & tag:rhsm & enabled:true`
- With `--no-rhsm`: `tag:8to9 & tag:-rhsm & enabled:true`
- With `--only-rhsm-stage-cdn`: `tag:8to9 & tag:rhsm & enabled:true` (plus environment variables)
- With tier: `tag:8to9 & tag:tier[0] & tag:rhsm & enabled:true`
- With tier and `--no-rhsm`: `tag:8to9 & tag:tier[0] & tag:-rhsm & enabled:true`

###### Compose resolution and target derivation

- You can specify composes as a simple version `MAJOR.MINOR` (e.g., `9.7`), a full compose name (e.g., `RHEL-9.7.0-Nightly`), or CentOS Stream format.
- **CentOS Stream sources** are supported with the following aliases (all case-insensitive):
  - `CentOS-Stream-9`, `centos-stream-9` (full format)
  - `stream-9`, `cs-9` (short format with hyphen)
  - `stream9`, `cs9` (short format without hyphen)
  - When CentOS Stream is used as source, the compose name in the request body is set to `CentOS-Stream-<major>`, `SOURCE_RELEASE`/`TARGET_RELEASE` environment variables use the major version only, and the TMT context `distro`/`target_distro` default to `centos-<major>` and `rhel-<major>` respectively (with `target_distro` switching to `centos-<major>` when `TARGET_OS=centos` is provided via `--environment`).
- **Alma Linux / Rocky Linux sources** are supported as AMI-based sources:
  - **Alias mode**: define aliases in `[sources.ami]` in `enge.toml`, e.g. `alma97 = 'AlmaLinux OS 9.7.20251118'`, `rocky97 = 'Rocky-9-EC2-Base-9.7-20251123.2'`, then use `--source alma97` / `--source rocky97`.
  - **Direct mode**: pass full AMI source names directly (with or without architecture suffix), e.g. `AlmaLinux OS 9.7.20251118 x86_64`, `Rocky-9-EC2-Base-9.7-20251123.2.aarch64`.
  - For AMI sources, only `x86_64` and `aarch64` architectures are supported.
- **RHUI sources** (`RHEL-<major>-rhui`, `RHEL-<major>-sap-hana-rhui`, `RHEL-<major>-sap-netweaver-rhui`, and other `RHEL-<major>-<middle>-rhui` variants) are passed through as-is without compose pinning. When an RHUI source is detected, `skip_guest_setup` is automatically set in the Testing Farm request pipeline settings.
- When CentOS Stream is the source, the `--target` argument may be provided as a major version only (e.g., `10`); it is automatically interpreted internally as `<major>.0` for compose pinning.
- When a simple version is provided, enge attempts to pin it to an actual compose name by consulting `testing_farm.composes_prod_url` from the configuration. It tries the following formats in order:
  - `RHEL-MAJOR.MINOR.0-Nightly` (RHEL 8/9 style)
  - `RHEL-MAJOR.MINOR-Nightly` (RHEL 10 style)
  If the `composes_prod_url` is not configured or resolution fails, enge falls back to `RHEL-MAJOR.MINOR.0-Nightly`.
- If `--target` is not provided, it is derived from the source version using the rule: `target_major = source_major + 1`, `target_minor = max(0, source_minor - 6)`. The target compose name is then formed as `RHEL-target_major.target_minor.0-Nightly`.

##### Test Sets

Test sets are pre-configured test scenarios that can be defined in your configuration file under `[tests.set]`. They allow you to define reusable test configurations with specific source/target composes, tiers, architectures, artifacts, and environment variables.

**Configuration Example:**
```toml
[tests.set.pre-release-smoke]
source = "9.7"
target = "10.1"  # Optional, will be auto-derived if not specified
tiers = ["tier0", "tier1"]
plans = ["/plans/custom-smoke", "/plans/integration"]  # Optional: specific plans (overridden by CLI --plan)
architectures = ["x86_64", "aarch64"]
git_ref = "main"
parallel_limit = 20

# For COPR builds - use alias:reference format
[tests.set.pre-release-smoke.copr_api]
# No package specification needed - all packages from build are included automatically
# Reference format: "alias:ref" where alias is lp (leapp) or lpr (leapp-repository)
build_references = ["lp:pr123"]

# For Brew builds
[tests.set.pre-release-smoke.brew_api]
package = "leapp"
build_reference = "leapp-0.1.2-3.el9"

[tests.set.pre-release-smoke.environment]
TARGET_RELEASE_URL = "https://some.url/to-the-target"
EXPERIMENTAL = "true"
```

**FMF Filters:**

Test sets can define `plan_filter` and `test_filter` to bake FMF filter expressions into the set, avoiding repeated CLI `--plan-filter`/`--test-filter` usage:

```toml
[tests.set.custom-scope]
source = "9.7"
tiers = ["tier0"]
architectures = ["x86_64"]
git_ref = "main"
plan_filter = "tag:custom & enabled:true"  # overrides tier-generated plan filter
test_filter = "tag:fast"                   # passed to Testing Farm as test filter
```

Priority: `CLI --plan-filter` > set `plan_filter` > tier-generated filter.
Priority: `CLI --test-filter` > set `test_filter`.

When a test set defines multiple tiers and architectures, a separate payload is generated for every combination of tier and architecture. If the test set also includes specific plans, each (tier, architecture, plan) combination generates a separate request.

**Usage:**
- Use `--set <set-name>` to run a predefined test set
- Multiple sets can be specified: `--set set1 --set set2`
- Use `--set-regex <regex>` to select sets by name using Python regular expressions; can be specified multiple times
- CLI arguments override test set configurations when provided
- Test sets can define plans, artifacts, environment variables, and test selection criteria
- When using `--plan` with `--set`, CLI plans completely override any plans defined in the test set
- The `--source` argument is not required when using `--set` (it's defined in the set configuration)

`--set-regex` behavior:
- Matches against names defined under `[tests.set.<name>]` in configuration
- Each regex expands to all matching set names; expansions are logged for visibility
- Combined with `--set`, results are merged, order-preserved, and de-duplicated
- Invalid patterns or patterns that match nothing cause a clear error listing available sets

Examples:
```bash
# Run all regression sets like regression-1, regression-2, ...
enge test --set-regex 'regression-[0-9]+$'

# Combine with exact set
enge test --set smoke --set-regex 'regression-.*'
```

**Priority Order:**
- CLI arguments > Test Set configuration > Main configuration
- For context: Derived base > [tests].context > [tests.set.<name>].context > CLI --context
- For architectures: CLI `--architectures/--arch` overrides set or tests defaults (warning logged)

##### TMT Context Integration

Enge automatically populates TMT context variables that are available to test scripts and TMT plugins (including ReportPortal integration). The context includes:

**Standard Context Fields:**
- `distro`: Source release (e.g., "rhel-9.7" for RHEL, "centos-9" for CentOS Stream)
- `target_distro`: Target release (e.g., "rhel-10.1" for RHEL sources, "rhel-10" for CentOS Stream sources, or "centos-10" when `TARGET_OS=centos`)
- `source_compose`: Source compose name (e.g., "RHEL-9.7.0-Nightly" for RHEL, "CentOS-Stream-9" for CentOS Stream). Spaces in compose names (e.g. Alma Linux AMI names) are replaced with dashes in TMT context so values work when passed to tmt.
- `upgrade_path`: Generated upgrade path (e.g., "9to10")
- `arch`: Target architecture (e.g., "x86_64")

**Conditional Context Fields:**
- `event`: Event name (from `--event` CLI arg or test set `event` field) or test set name fallback (only when using `--set`)
- `tier`: Test tier (when using `--tier` or test sets with tiers)
- `uniq_id`: Shortened ReportPortal launch UUID (when using `--rp`), format: "d51eba30-1956"
- `target_compose`: Target compose name (when `TARGET_COMPOSE_URL` environment variable is provided), format: "RHEL-10.0-19700101.0"

**Build Artifact Context:**
When using `--brew`, package version information is automatically added in the format `package_name: version-release`:
- `leapp`: "0.16.0-1.el9"
- `leapp-repository`: "0.1-32.el9"

When using `--copr`, all packages built in the COPR build are automatically included in the Testing Farm payload as full NVRAs (name-version-release.arch), ensuring all related packages are tested together.

**Configurable Context Defaults:**
You can define default context in config:
```toml
[tests.context]
product = "rhel"
team = "qe"

[tests.set.myset.context]
event = "override-newevent"
```
Merge order and overrides (warnings are logged on overrides):
1) Derived base (distro, target_distro, upgrade_path, etc.)
2) `[tests].context`
3) `[tests.set.<name>].context` (when using sets)
4) CLI `--context key=value`

**Example TMT Context (RHEL source):**
```json
{
  "distro": "rhel-9.7",
  "target_distro": "rhel-10.1",
  "source_compose": "RHEL-9.7.0-19700101.0",
  "upgrade_path": "9to10",
  "arch": "x86_64",
  "event": "pre-release-smoke",
  "tier": "tier0",
  "uniq_id": "d51eba30-1956",
  "target_compose": "RHEL-10.0-19700101.0",
  "leapp": "0.16.0-1.el9",
  "leapp-repository": "0.16.0-2.el9"
}
```

**Example TMT Context (CentOS Stream source):**
```json
{
  "distro": "centos-9",
  "target_distro": "rhel-10",
  "source_compose": "CentOS-Stream-9",
  "upgrade_path": "9to10",
  "arch": "x86_64",
  "event": "pre-release-smoke",
  "tier": "tier0"
}
```

**Example TMT Context (Alma Linux source):**
```json
{
  "distro": "alma-9.7",
  "target_distro": "rhel-10.1",
  "source_compose": "AlmaLinux-OS-9.7.20251118",
  "upgrade_path": "9to10",
  "arch": "x86_64",
  "event": "pre-release-smoke",
  "tier": "tier0"
}
```

##### ReportPortal Integration

Enge provides native ReportPortal integration that creates launches and manages test result uploads automatically. When using the `--rp` flag, enge creates ReportPortal launches directly via API and configures TMT to upload results to the appropriate launches.

**Per-Request Launch Strategy:**

When `--rp` is used, enge creates one ReportPortal launch per individual test request. This provides maximum granularity and isolation for test results:

- **Launch 1**: `RELEASE-CANDIDATE~2025-07-30~tier0~x86_64` → Contains tier0 results for x86_64
- **Launch 2**: `RELEASE-CANDIDATE~2025-07-30~tier1~x86_64` → Contains tier1 results for x86_64
- **Launch 3**: `RELEASE-CANDIDATE~2025-07-30~tier0~s390x` → Contains tier0 results for s390x
- **Launch 4**: `RELEASE-CANDIDATE~2025-07-30~tier1~s390x` → Contains tier1 results for s390x

**Launch Naming Convention:**

Launch names follow the format: `(EVENT_NAME|SET_NAME)~YYYY-MM-DD~tier~architecture`

- **Event Priority**: If an `event` is defined (in test set config or CLI `--event`), it's used in uppercase
- **Set Name Fallback**: If no event is specified, the test set name is used in uppercase
- **Date Format**: Current date in YYYY-MM-DD format
- **Tier**: Test tier (e.g., tier0, tier1, unknown if not specified)
- **Architecture**: Target architecture (e.g., x86_64, s390x, aarch64)

**Configuration:**

ReportPortal integration requires configuration in your `enge.toml`:

```toml
[reportportal]
url = "https://your-reportportal.company.com"
token = "your-reportportal-api-token"
project = "your-project-name"

# Optional: Default launch configuration
launch = "Custom Default Launch Name"
description = "Default launch description"
```

The API token can also be provided via environment variable:

```bash
export REPORTPORTAL_API_TOKEN="your-reportportal-api-token"
```

> **Priority**: config value > environment variable. If `token` is set in `enge.toml`, the environment variable is ignored.

**Test Set Event Configuration:**

Test sets can define custom event names for launch naming:

```toml
[tests.set.pre-release-smoke]
source = "9.7"
target = "10.1"
event = "release-candidate"  # Used in launch names instead of set name
tiers = ["tier0", "tier1"]
architectures = ["x86_64", "s390x"]

[tests.set.pre-release-smoke.reportportal]
description = "Pre-release smoke testing with RC builds"
```

**Usage Examples:**

```bash
# Creates launches per request: SMOKE-TESTS~2025-07-30~tier0~x86_64, SMOKE-TESTS~2025-07-30~tier1~x86_64, etc.
enge test --set smoke-tests --rp

# Creates launches with event name: RELEASE-CANDIDATE~2025-07-30~tier0~x86_64, RELEASE-CANDIDATE~2025-07-30~tier1~x86_64, etc.
enge test --set smoke-tests --event "release-candidate" --rp

# Works with legacy approach too
enge test --source 9.7 --tier tier0 --event "nightly-build" --rp

# Rerun with ReportPortal integration
enge rerun --run <run_id> --rp
```

**How It Works:**

1. **Launch Creation Phase**: During request processing, enge creates one ReportPortal launch for each individual test request
2. **UUID Distribution**: Each Testing Farm request receives `TMT_PLUGIN_REPORT_REPORTPORTAL_UPLOAD_TO_LAUNCH` with its unique launch UUID
3. **TMT Context Enhancement**: Each request gets a `uniq_id` field in TMT context with a shortened version of the launch UUID (first 12 characters)
4. **Result Upload**: TMT automatically uploads test results to the specific launch for that request
5. **Maximum Isolation**: Each test scenario gets its own launch, providing complete isolation and detailed tracking

**Testing Farm Payload Integration:**

When `--rp` is used, enge automatically configures the Testing Farm payload with ReportPortal environment variables:

```json
{
  "environments": [
    {
      "arch": "x86_64",
      "tmt": {
        "environment": {
          "TMT_PLUGIN_REPORT_REPORTPORTAL_URL": "https://your-reportportal.com",
          "TMT_PLUGIN_REPORT_REPORTPORTAL_TOKEN": "your-token",
          "TMT_PLUGIN_REPORT_REPORTPORTAL_PROJECT": "your-project",
          "TMT_PLUGIN_REPORT_REPORTPORTAL_UPLOAD_TO_LAUNCH": "4cc4dbff-bcf3-49a2-8369-8b1f7c14d6df" # Assigned automatically based on the respective launch created before the request dispatch
        }
      }
    }
  ]
}
```

**Variable Filtering:**

When `--rp` is used, enge automatically excludes conflicting variables to prevent TMT from creating its own launches:
- ✅ **Included**: `TMT_PLUGIN_REPORT_REPORTPORTAL_URL`, `TMT_PLUGIN_REPORT_REPORTPORTAL_TOKEN`, `TMT_PLUGIN_REPORT_REPORTPORTAL_PROJECT`, `TMT_PLUGIN_REPORT_REPORTPORTAL_UPLOAD_TO_LAUNCH`
- ❌ **Excluded**: `TMT_PLUGIN_REPORT_REPORTPORTAL_LAUNCH`, `TMT_PLUGIN_REPORT_REPORTPORTAL_LAUNCH_DESCRIPTION`

**Benefits:**

- **Maximum Granularity**: Each test request gets its own launch for complete isolation
- **Detailed Tracking**: Easy to identify specific tier/architecture combinations
- **Automated Management**: No manual launch creation or UUID management required
- **Consistent Naming**: Standardized launch names including tier information
- **Flexible Configuration**: Event names can be customized per test set or via CLI
- **TMT Integration**: Shortened UUID available in TMT context as `uniq_id` for test scripts
- **Test Set Focus**: Optimized for the modern test sets approach (legacy approach not supported)

###### ReportPortal Launch Management

The `enge reportportal` subcommand provides tools for managing ReportPortal launches after dispatch — finishing launches, enriching them with artifact logs, and deleting logs. Operations are organized as subcommands with explicit verbs instead of flag combinations.

**Subcommands:**

- `enge reportportal finish` — Finish IN_PROGRESS launches
- `enge reportportal enrich` — Enrich launches with artifact logs
- `enge reportportal delete-logs` — Delete logs from launches
- `enge reportportal delete-stale` — Delete empty launches
- `enge reportportal check` — Test ReportPortal connectivity

**Finishing Launches:**

Finish an IN_PROGRESS launch by resolving the Testing Farm task status and setting the appropriate end time and status on the RP launch.

```bash
# Finish launches resolved from the latest archived tasks
enge reportportal finish

# Finish launches for specific Testing Farm task(s)
enge reportportal finish -i <tf-task-uuid>

# Finish all IN_PROGRESS launches in the project (no TF input needed)
enge reportportal finish --all

# Preview what would be finished
enge reportportal finish --all --dry-run

# Enrich then finish in one step
enge reportportal finish --enrich -i <tf-task-uuid>
```

When using `--all`, the status and end time are derived from the RP test items within each launch (not from the current time). The Testing Farm artifacts URL is extracted from test-item descriptions and added to the launch description.

**Enriching Launches with Logs:**

Download artifact logs from the Testing Farm artifact endpoint and upload them to the corresponding RP launch. Only logs belonging to failed test items are attached — passed and skipped items are excluded to keep the RP interface focused on failures. Suite-level (unmapped) logs are included when at least one item in the launch has failed. Logs are downloaded in memory and uploaded in batches — nothing is written to disk.

```bash
# Enrich launches from specific TF task(s)
enge reportportal enrich -i <tf-task-uuid>

# Enrich all launches regardless of status (carpet bomb)
enge reportportal enrich --all

# Preview enrichment
enge reportportal enrich --all --dry-run
```

With `--all`, the artifacts URL is extracted from RP test-item descriptions (set by the TMT plugin), then `results.xml` is fetched to discover artifact files. No Testing Farm task input is needed.

**Deduplication:**

Enge prevents duplicate log uploads through two layers:

1. **Launch attribute** (`logs_attached=true`): After successful enrichment, this attribute is stamped on the launch. Subsequent runs skip launches with this attribute entirely — no API calls needed.
2. **Message-header matching**: Each uploaded log starts with a `### \`artifact-name\`` header. Before uploading, enge fetches existing logs from the launch's test items and filters out artifacts whose headers already exist.

This means you can safely run `enge reportportal enrich --all` repeatedly without creating duplicates.

**Deleting Logs:**

Remove all log entries from a launch.

```bash
# Delete logs from launches resolved from TF task(s)
enge reportportal delete-logs -i <tf-task-uuid>

# Delete logs from all IN_PROGRESS launches
enge reportportal delete-logs --all

# Preview deletion
enge reportportal delete-logs --all --dry-run
```

**Deleting Stale Launches:**

Delete launches that are stopped or interrupted and have no test items — empty launches left behind by failed or aborted dispatches.

```bash
# Delete all stale launches
enge reportportal delete-stale

# Preview which launches would be deleted
enge reportportal delete-stale --dry-run

# Only stale launches started before a given date
enge reportportal delete-stale --until 2025-12-31

# Combine both bounds
enge reportportal delete-stale --since 2025-01-01 --until 2025-06-30
```

**Date Filters (`--since` / `--until`):**

Narrow any launch-listing operation by date. Accepts absolute dates (`YYYY-MM-DD`) or relative aliases (`6h`, `3d`, `2w`, `1m`, `1y` — meaning "that many units ago from now"). Effective with `--all` and `delete-stale`; ignored for task-based operations.

```bash
# Finish only launches started after a date
enge reportportal finish --all --since 2025-07-01

# Delete stale launches from the last 3 days
enge reportportal delete-stale --since 3d

# Delete stale launches older than 2 weeks
enge reportportal delete-stale --until 2w

# Enrich launches within a relative window
enge reportportal enrich --all --since 1m

# Mix absolute and relative
enge reportportal enrich --all --since 2025-01-01 --until 3m
```

The same `--since` / `--until` flags are also available on the `report` subcommand — see [Report](#report).

**Testing Connection:**

Verify your ReportPortal configuration and connectivity.

```bash
enge reportportal check
```

**Input Sources:**

All task-based operations (`finish`, `enrich`, `delete-logs` without `--all`) accept the same input sources as the report module:
- `-i/--input <uuid>` — Testing Farm task UUID(s)
- `-f/--file <path>` — file containing task UUIDs
- Default: reads the latest manifest from the manifest store

**Subcommand Reference:**

| Subcommand | Scope | Behavior |
|---|---|---|
| `finish --all` | IN_PROGRESS | Derives status & end time from test items, sets artifacts URL as description |
| `finish --enrich` | Task-based | Enriches artifact logs then finishes the launch in one command |
| `enrich --all` | All statuses | Enriches every launch; skips already-enriched (`logs_attached`) |
| `delete-logs --all` | IN_PROGRESS | Deletes all logs from each launch |
| `delete-stale` | STOPPED / INTERRUPTED | Deletes empty launches (no test items); standalone, no `--all` needed |

**Note:** The old flag-verb spellings (`--finish`, `--enrich-logs`, `--delete-logs`, `--delete-stale`, `--test`, `--all-launches`) still work for backward compatibility but emit a deprecation warning. They will be removed in a future release.

##### Report
With the report command you are able to get the results of the requested jobs straight to the command line.<br>
It works by parsing the xunit field in the request response.<br>
Results can be reported back in two levels - the default plan overview and `--show-tests` for a detailed tests overview.<br>
You can chain the report command with test command and use the `-w/--wait` argument to get the results back whenever the requests state is complete (or error in which case the job results cannot be and won't be reported due to the non-existent xunit field).<br>
`enge test` writes a JSON manifest to `~/.local/share/enge/runs/` for each dispatch invocation. The latest pointer at `~/.local/state/enge/latest` tracks the newest run.
Default invocation `enge report` reads tasks from the latest manifest. Use `enge report --list` to browse all runs, then `enge report --run <run_id>` to report a specific one.<br>
You can specify a different path to a file with `-f/--file` or pass task IDs with `-i/--input`. Both can be used multiple times, the task IDs will get aggregated and reported in a single table.<br>
Use structured filters `--set`, `--tier`, `--arch`, `--tag` to match against manifest metadata. Legacy `--get-tag` still works for pre-migration archive files but is deprecated.<br>
The tool is able to parse and report for multiple variants of values as long as they are separated by a new-line (in the files) or a `-i/--input` argument (on the commandline). Raw request_ids, artifact URLs (Testing Farm result page URLs) or request URLs are allowed.
Use `--show-ids` to display only a list of UUIDs queried from the requested inputs, which is useful for extracting task IDs for further processing or scripting.<br>
In case you want to get the log files stored locally, use `--download`. Log files for pytest runs will be stored in `/var/tmp/enge/logs/{request_id}_log/`. In case there are multiple plans in one pipeline, the logs should get divided in their respective plan directories.

```
# Get results for the latest run (reads manifest store)
enge report

# Browse all runs in the manifest store
enge report --list

# Report a specific run by ID
enge report --run <run_id>

# Report from custom file on the test level
enge report --show-tests --file ~/my_jobs_file

# Pass requests' references to the commandline
enge report --input d60ee5ab-194f-442d-9e37-933be1daf2ce --input https://api.endpoint/requests/9f42645f-bcaa-4c73-87e2-6e1efef16635

# Shorten the displayed test and plan names
enge report --show-tests --input 9f42645f-bcaa-4c73-87e2-6e1efef16635 --short

# Display only UUIDs from the requested inputs
enge report --show-ids --file ~/my_jobs_file
```

**Date Filters (`--since` / `--until`):**

`--since` and `--until` filter manifests by `created_at` timestamp. Accepts absolute dates (`YYYY-MM-DD`) or relative aliases (`6h`, `3d`, `2w`, `1m`, `1y`). Combinable with structured filters (`--set`, `--tier`, `--arch`, `--tag`). Files provided via `-f` or `-i` are not filtered.

```bash
# Report runs from the last week for a specific set
enge report --set base-8to9 --since 1w

# List runs within a date range
enge report --list --since 2026-01-01 --until 2026-06-30

# Report runs from the last 12 hours
enge report --since 12h
```

## Troubleshooting configuration and validation

**Empty string and None are treated as unset.** If your `enge.toml` sets a key
to `''` (empty string) or `null`, enge inherits the default from
`enge_default_config.toml` for that key.  A warning is logged so you know
the override was ignored.  Validators only fire when *no layer* provides a
non-empty value.

enge uses a single `ExitCode` enum (`utils/globals.py`). The universal floor applies to every subcommand:

| Code | Meaning |
|------|---------|
| 0    | Success |
| 1    | Unhandled exception |
| 99   | Configuration error (bad/missing config, invalid endpoint URL) |
| 130  | Interrupted (Ctrl-C) |

`enge report` additionally returns result-grading codes, since it is the only command that grades multi-plan result sets:

| Code | Meaning |
|------|---------|
| 2    | Ran; at least one test FAILED (no errors) |
| 3    | Ran; at least one ERROR was hit |
| 4    | Ran; at least one request had no results (missing/expired) |

When a report run mixes these, the most severe wins: **3 > 2 > 4 > 0** (error-dominates — missing results are rerun candidates and must not mask a real error). `enge test` also uses code 2 for partial dispatch failure (some requests submitted, some failed).

**Comparison mode (`--compare`, `--unify`):**

By default each run's results are shown as a separate table. Use `--compare` to build a side-by-side comparison table across multiple runs. `--unify PLAN1=PLAN2` treats renamed plans as equivalent when comparing (can be specified multiple times).

```
❯ enge report -i 8f4e2e3e-beb4-4d3a-9b0a-68a2f428dd1b -i c3726a72-8e6b-4c51-88d8-612556df7ac1 --short --unify tier2=tier2_7to8 --compare
```

##### Rerun
Rerun tasks which report as FAILED or ERROR.<br>
Only works for whole plans.<br>
Reads the same input as the report module — default is the latest manifest; use `--run <id>`, `--file`, or `--input` to select specific runs.<br>
Use `--error` or `--fail` if you want to further specify which type of non-zero result you want to re-run, default is both results. If the whole task reports state error, the original plan filtering will be used, otherwise each of the failing/erroring plans will be passed to the plan name field connected by a pipe `|`, meaning all qualified plans from a single original request will be sent as one request for a re-run.<br>
Use `--dry-run` to only display the qualified plans, don't actually send any payload to the Testing Farm.<br>
Use `--set-tag` to attach custom tags to the rerun manifest.

Rerun manifests carry `parent_run_id` linking to the original run, and inherit the parent's tags plus `"rerun"`. See the [Manifest Store and Run History](#manifest-store-and-run-history) section for details.

```
# Rerun from the latest run
enge rerun

# Rerun a specific run by manifest ID
enge rerun --run <run_id>

# Rerun from a file or direct UUID
enge rerun -f my_jobs_file
enge rerun -i 8f4e2e3e-beb4-4d3a-9b0a-68a2f428dd1b

# Rerun only failures (exclude errors)
enge rerun --run <run_id> --fail

# Rerun with custom tags
enge rerun --run <run_id> --set-tag rc-revalidation

# Legacy: query pre-migration archive files (deprecated, use --run instead)
enge rerun --get-tag "rc.*" --set-tag rerun
```

##### Manifest Store and Run History

Each `enge test` or `enge rerun` invocation writes a JSON manifest to `~/.local/share/enge/runs/`. Manifests record structured per-request metadata (task ID, set, tier, architecture, plan, composes, artifacts URL) and are identified by a time-sortable ULID. A latest pointer at `~/.local/state/enge/latest` tracks the newest run.

> **MIGRATION NOTE**: The old `/tmp/enge_latest_jobs` file and `~/.enge/jobs_archive/` filename-tagged files are no longer written. External scripts that read these files must migrate to `enge report --list`/`--run` or read the manifest JSON directly. The read-only legacy bridge inside enge still reads old files so `enge report` and `enge rerun` work against pre-migration runs.

**Browsing and filtering runs:**

```bash
# List all runs (newest first)
enge report --list

# Filter by set, tier, architecture, or tag
enge report --list --set base-8to9
enge report --list --tier tier0 --arch x86_64
enge report --list --tag nightly --since 3d

# Report a specific run by ID (copy from --list output)
enge report --run <run_id>

# Rerun from a specific run
enge rerun --run <run_id>
```

**Tagging (`--set-tag`):**
- Available in `test` and `rerun` commands
- Tags are stored in the manifest's `tags` array
- Can be used multiple times: `--set-tag tag1 --set-tag tag2`
- Query with `--tag` in report/rerun: `enge report --list --tag regression`

**`--auto-tag` (deprecated):**
Context (set name, architecture, tier) is now always recorded in the manifest. `--auto-tag` is a no-op and will be removed in a future release.

**`--get-tag` (deprecated):**
Use `--tag` for native manifests. `--get-tag` still works for pre-migration archive files via the legacy bridge.

**Rerun lineage:**
Rerun manifests carry `parent_run_id` linking to the original run, replacing the old `.rerun` filename suffix. Inherited tags are copied from parent to child.

**Migrating legacy archives:**

```bash
# Convert ~/.enge/jobs_archive/ files to manifests (non-destructive, idempotent)
enge migrate-archive
```

This creates synthetic manifests with `origin="migrated"`. Task IDs and artifacts URLs are populated; compose/event/plan fields are null (not recoverable offline). Original archive files are not deleted.

**Manifest store locations (XDG-compliant, config-overridable):**
- Manifests: `~/.local/share/enge/runs/<run_id>.json`
- Latest pointer: `~/.local/state/enge/latest`
- Logs: `~/.local/state/enge/logs/`

**Examples:**
```bash
# Test with custom tags
enge test --copr lp:pr123 --source 9.7 --tier tier0 --set-tag regression --set-tag pr123

# Browse and pick
enge report --list --tag regression
enge report --run 01J5KXYZ...

# Filter by structured fields
enge report --list --set pre-release-smoke --tier tier0 --since 1w
```
