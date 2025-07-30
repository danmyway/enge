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
   2. [Usage](#usage)
       1. [Commands](#sub-commands)
          1. [Test](#test)
          2. [Test Sets](#test-sets)
          3. [TMT Context Integration](#tmt-context-integration)
          4. [ReportPortal Integration](#reportportal-integration)
          5. [Report](#report)
          6. [Rerun](#rerun)
          7. [Task Archiving and Tagging](#task-archiving-and-tagging)


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
Add the obtained api_key to the config file as instructed below.

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
The template for the config file is available in the root of the repository. The default locations for the config file are `~/.config/enge.toml` or `~/.enge.toml`. A custom path to a config file can be specified through the commandline option -c.<br>
In case of any question, please reach out to the project maintainer(s).

### Usage

#### Sub-Commands
Enge provides several commands for comprehensive test workflow management:<br>
`test` feeds the request payload with provided config options or arguments and dispatches a test job to the Testing Farm.<br>
`report` outputs the test results back to the command line.<br>
`rerun` re-dispatches failed or errored test jobs.<br>
`cancel` cancels running or queued Testing Farm tasks.

##### Test

The goal of enge is to make requesting test jobs as easy as possible.<br>
A default artifact to install (if not specified otherwise) is the one available in the compose.<br>
The `--brew` and `--copr` options denote which type of a build artifact is to be requested for testing.<br>
Instead of looking for build IDs to pass to the payload, all you need to know is a reference for a pull request number (e.g. pr123) which triggered the build you need to test. In case you have the Build ID handy, you can use that instead of the reference.<br>
For brew builds you can provide either the NVR (e.g. leapp-0.16.0-1.el9) or the TaskID. Both are validated via the Brew API, and Task IDs are automatically resolved to their corresponding NVR. The NVR is always used in the Testing Farm payload for consistency.<br>
Multiple `--plan` options can be specified and will be dispatched in separate jobs.
`--tier` options allow you to run predefined test tiers from your configuration.
`--set` options allow you to use pre-configured test sets (see Test Sets section below).
**Plan Override Behavior:** When using `--plan` with `--tier` or `--set`, the CLI plans override any plans defined in configuration or test sets.
When using `--planfilter` or `--test` to specify singular test it is disallowed to request multiple `--plan` options in one command.<br>
Use `--wait` if waiting for a successful response from the endpoint is required.
If for any reason you would need to verify the validity of the raw payload, use `--dryrun` to get it pretty-printed to the command line.

Use `--set-tag` to tag archived task files with custom tags for later retrieval (can be used multiple times).
Use `--auto-tag` to automatically tag archived task files with contextual information (set name, architecture, tier) and create separate, organized archive files for each unique combination.
The `--source` argument is required unless using `--set` (which defines source in the configuration).

```
# Test latest build from main (most of the arguments set through the config file)
enge test --copr

# Test copr build for PR#123 with plan named basic_sanity_check on all targets
enge test --copr pr123 --plan /plans/tier0/basic_sanity_checks

# Specify which composes you want to run test plan (in this case tier0 on RHEL9)
enge test --copr pr123 --plan /plans/tier0 --target rhel9

# Run every test plan for brew build 0.12-3 on all composes
enge test --brew 0.12-3 --plan /plans

# Specify more individual test plans
enge test --brew 0.12-3 --plan /plans/tier0/basic_sanity_checks --plan /plans/tier1/whatever_else

# Test using predefined tiers
enge test --copr pr123 --tier tier0 --tier tier1

# Test using a predefined test set
enge test --set pre-release-smoke

# Test with custom tags for archiving
enge test --copr pr123 --tier tier0 --set-tag regression --set-tag pr123

# Test with automatic tagging based on context
enge test --set pre-release-smoke --auto-tag

# Combine automatic and manual tagging
enge test --set pre-release-smoke --auto-tag --set-tag custom-run

# Use Task ID - automatically resolved to NVR in payload
enge test --brew 12345678 --tier tier0

# Combine tier with specific plans (CLI plans override config plans)
enge test --tier tier0 --plan /plans/custom-plan --source 9.7

# Override test set plans with CLI plans
enge test --set pre-release-smoke --plan /plans/override-plan

# Test multiple brew packages (each gets NVR in TMT context)
enge test --brew leapp-0.16.0-1.el9 --brew leapp-repository-0.1-32.el9 --tier tier0
```

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
git_branch = "main"
parallel_limit = 20

[tests.set.pre-release-smoke.brew_api]
package = "leapp"
build_reference = "leapp-0.1.2-3.el9"

[tests.set.pre-release-smoke.environment]
TARGET_RELEASE_URL = "https://some.url/to-the-target"
EXPERIMENTAL = "true"
```

When a test set defines multiple tiers and architectures, a separate payload is generated for every combination of tier and architecture. If the test set also includes specific plans, each (tier, architecture, plan) combination generates a separate request.

**Usage:**
- Use `--set <set-name>` to run a predefined test set
- Multiple sets can be specified: `--set set1 --set set2`
- CLI arguments override test set configurations when provided
- Test sets can define plans, artifacts, environment variables, and test selection criteria
- When using `--plan` with `--set`, CLI plans completely override any plans defined in the test set
- The `--source` argument is not required when using `--set` (it's defined in the set configuration)

**Priority Order:** CLI arguments > Test Set configuration > Main configuration

##### TMT Context Integration

Enge automatically populates TMT context variables that are available to test scripts and TMT plugins (including ReportPortal integration). The context includes:

**Standard Context Fields:**
- `distro`: Source release (e.g., "rhel-9.7")
- `target_distro`: Target release (e.g., "rhel-10.1")
- `source_compose`: Source compose name (e.g., "RHEL-9.7.0-Nightly")
- `upgrade_path`: Generated upgrade path (e.g., "9to10")
- `arch`: Target architecture (e.g., "x86_64")

**Conditional Context Fields:**
- `event`: Event name (from `--event` CLI arg or test set `event` field) or test set name fallback (only when using `--set`)
- `tier`: Test tier (when using `--tier` or test sets with tiers)
- `uniq_id`: Shortened ReportPortal launch UUID (when using `--rp`), format: "d51eba30-1956"
- `target_compose`: Target compose name (when `TARGET_COMPOSE_URL` environment variable is provided), format: "RHEL-10.0-19700101.0"

**Brew Artifact Context:**
When using `--brew`, package version information is automatically added in the format `package_name: version-release`:
- `leapp`: "0.16.0-1.el9"
- `leapp-repository`: "0.1-32.el9"

**Example TMT Context:**
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
  "leapp": "0.16.0-1.el9"
  "leapp-repository": "0.16.0-2.el9"
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
enge rerun --get-tag regression --rp
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

##### Report
With the report command you are able to get the results of the requested jobs straight to the command line.<br>
It works by parsing the xunit field in the request response.<br>
Results can be reported back in two levels - the default plan overview and `--show-tests` for a detailed tests overview.<br>
You can chain the report command with test command and use the `-w/--wait` argument to get the results back whenever the requests state is complete (or error in which case the job results cannot be and won't be reported due to the non-existent xunit field).<br>
`enge test` automatically stores the request IDs from the latest dispatched job - the primary location to store and read the data from is `/tmp/latest_enge_jobs` file. The file is also saved with a timestamp to the working directory just for a good measure.
Default invocation `enge report` parses the tasks stored in the latest file at `/tmp/latest_enge_jobs`.<br>
You can specify a different path to the file with `-f/--file` or pass the jobs to get report for straight to the commandline with `-i/--input`. Both can be used multiple times, the task IDs will get aggregated and reported in a single table.<br>
You can also use `--get-tag` to query archived task files by regex patterns (supports both simple tags and complex patterns - see [Task Archiving and Tagging](#task-archiving-and-tagging) section for details).<br>
The tool is able to parse and report for multiple variants of values as long as they are separated by a new-line (in the files) or a `-i/--input` argument (on the commandline). Raw request_ids, artifact URLs (Testing Farm result page URLs) or request URLs are allowed.
Use `--show-ids` to display only a list of UUIDs queried from the requested inputs, which is useful for extracting task IDs for further processing or scripting.<br>
In case you want to get the log files stored locally, use `--download`. Log files for pytest runs will be stored in `/var/tmp/enge/logs/{request_id}_log/`. In case there are multiple plans in one pipeline, the logs should get divided in their respective plan directories.

```
# Get results for the requests in the latest file /tmp/enge_latest_jobs
enge report

# Report from custom file on the test level
enge report --show-tests --file ~/my_jobs_file

# Pass requests' references to the commandline
enge report --input d60ee5ab-194f-442d-9e37-933be1daf2ce --input https://api.endpoint/requests/9f42645f-bcaa-4c73-87e2-6e1efef16635

# Shorten the displayed test and plan names
enge report --show-tests --input 9f42645f-bcaa-4c73-87e2-6e1efef16635 --short

# Display only UUIDs from the requested inputs
enge report --show-ids --file ~/my_jobs_file
```

Corresponding return code is set based on the results with following logic:
 * 0 - The results are complete for each request and all are pass
 * 1 - Python exception or bailout
 * 2 - No error was hit, at least one fail was found
 * 3 - At least one error was hit
 * 4 - At least one request didn't have any result
 * everything else - consult with Tesar maintainer(s)

The default way to show results is by showing each run details as a separate table. In order to combine test results of several different tft runs you can use comparison mode which is triggered by the `--compare` flag of `enge report`.

```
❯ enge report -i 8f4e2e3e-beb4-4d3a-9b0a-68a2f428dd1b -i c3726a72-8e6b-4c51-88d8-612556df7ac1 --short --unify tier2=tier2_7to8 --compare
```

##### Rerun
Rerun tasks which report as FAILED or ERROR.<br>
Only works for whole plans.<br>
Reads the same input as the report module - `--file`, `--input` or `--get-tag` (with regex pattern support), which can be combined.<br>
Use `--error` or `--fail` if you want to further specify which type of non-zero result you want to re-run, default is both results. If the whole task reports state error, the original plan filtering will be used, otherwise each of the failing/erroring plans will be passed to the plan name field connected by a pipe `|`, meaning all qualified plans from a single original request will be sent as one request for a re-run.<br>
Use `--dryrun` to only display the qualified plans, don't actually send any payload to the Testing Farm.<br>
Use `--set-tag` to label the archived jobs file.

For detailed information about task archiving and tagging functionality, see the [Task Archiving and Tagging](#task-archiving-and-tagging) section.

```
# Rerun qualified jobs from a file
enge rerun -f my_archive_file

# Disregard errors for a rerun qualification
enge rerun -f my_archive_file --fail

# Rerun qualified job from a commandline
enge rerun -i 8f4e2e3e-beb4-4d3a-9b0a-68a2f428dd1b

# Query the archive files by tag (simple match)
enge rerun --get-tag rc --set-tag secondrun --set-tag rc
# or
enge rerun --get-tag rc --set-tag secondrun.rc

# Query using regex patterns
enge rerun --get-tag "rc.*" --set-tag rerun         # matches rc, rc.x86_64, rc.tier0, etc.
enge rerun --get-tag "tier[01]" --set-tag tier01    # matches tier0 or tier1
```

##### Task Archiving and Tagging

The `--set-tag`, `--auto-tag`, and `--get-tag` options provide a powerful way to organize and retrieve test results:

**Setting Tags (`--set-tag`):**
- Available in `test` and `rerun` commands
- Tags archived task files with custom labels for later retrieval
- Can be used multiple times: `--set-tag tag1 --set-tag tag2`
- Tagged files are stored as `filename.tag1.tag2` in the archive directory

**Automatic Tagging (`--auto-tag`):**
- Available in `test` and `rerun` commands
- Automatically generates tags based on contextual information:
  - **Sets**: Creates combined tags like `setname.architecture.tier` for precise identification
  - **Tiers**: Creates combined tags like `architecture.tier` when no set is specified
  - **Plans**: Creates tags for architecture (when single architecture is configured)
- Can be combined with `--set-tag` for additional custom tags
- **Generates separate archive files** - each unique tag combination creates its own file
- Particularly useful for test sets with multiple tier/architecture combinations as it creates granular, organized files

**Getting Tagged Results (`--get-tag`):**
- Available in `report` and `rerun` commands
- Query archived task files by regex patterns (supports both simple strings and complex patterns)
- **Regex Pattern Support**: Each tag argument is treated as a regex pattern for flexible matching
- **Backward Compatible**: Simple strings work as literal matches (e.g., `--get-tag xml` matches "xml")
- **Pattern Matching**: Supports wildcards and complex patterns:
  - `--get-tag "rhel.*"` matches rhel8, rhel9, rhel8.x86_64, etc.
  - `--get-tag ".*\.xml$"` matches any XML files
  - `--get-tag "test-\d+"` matches test-1, test-23, etc.
  - `--get-tag "(rhel8|rhel9)"` matches either rhel8 or rhel9
- **Dual Matching**: Patterns match against both file extensions and full filenames
- **OR Logic**: `--get-tag pattern1 --get-tag pattern2` finds files matching either pattern
- **Error Handling**: Invalid regex patterns are caught with clear error messages
- Can be combined with `--file` and `--input` options

**Archive Locations:**
- Latest job IDs: `/tmp/enge_latest_jobs`
- Archived jobs: `~/.enge/jobs_archive/` (configurable)
- Tagged files: `~/.enge/jobs_archive/filename.tag1.tag2`
- **With `--auto-tag`**: Multiple separate files like `filename.setname.arch.tier`

**File Organization Example:**
When running `enge test --set pre-release --auto-tag` with tiers [tier0, tier1] and architectures [x86_64, aarch64], you get:
```
~/.enge/jobs_archive/
├── enge_jobs_archive_20250121_143022.pre-release.x86_64.tier0
├── enge_jobs_archive_20250121_143022.pre-release.x86_64.tier1
├── enge_jobs_archive_20250121_143022.pre-release.aarch64.tier0
└── enge_jobs_archive_20250121_143022.pre-release.aarch64.tier1
```
Each file contains exactly one task ID for its specific combination, enabling precise organization and querying.

**Examples:**
```bash
# Test with custom tags (creates one shared file)
enge test --copr pr123 --tier tier0 --set-tag regression --set-tag pr123

# Test with automatic tagging (creates separate file: enge_jobs_archive_timestamp.x86_64.tier0)
enge test --copr pr123 --tier tier0 --auto-tag

# Test set with automatic tagging (creates separate files for each tier/arch combination)
# Example files: enge_jobs_archive_timestamp.pre-release-smoke.x86_64.tier0
#                enge_jobs_archive_timestamp.pre-release-smoke.aarch64.tier0
enge test --set pre-release-smoke --auto-tag

# Combine automatic and manual tagging (separate files with both auto and manual tags)
enge test --set pre-release-smoke --auto-tag --set-tag custom-run

# Get results by tag (works across all files)
enge report --get-tag regression

# Get results by auto-generated tag (finds specific combination)
enge report --get-tag x86_64

# Get results for specific set and tier combination (exact match)
enge report --get-tag pre-release-smoke.x86_64.tier0

# Get results using regex patterns
enge report --get-tag "rhel.*"           # matches rhel8, rhel9, rhel8.x86_64, etc.
enge report --get-tag ".*\.xml$"         # matches any XML files
enge report --get-tag "test-\d+"         # matches test-1, test-23, etc.
enge report --get-tag "(tier0|tier1)"    # matches either tier0 or tier1

# Report results for multiple patterns (OR logic)
enge report --get-tag "regression.*" --get-tag "pr\d+"

# Rerun failed jobs with pattern matching
enge rerun --get-tag "tier[01]" --fail --auto-tag

# Combine tag search with other inputs
enge report --get-tag regression --file ~/my_jobs --input 8f4e2e3e-beb4-4d3a-9b0a-68a2f428dd1b
```
