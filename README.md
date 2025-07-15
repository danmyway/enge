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
          3. [Report](#report)
          4. [Rerun](#rerun)
          5. [Task Archiving and Tagging](#task-archiving-and-tagging)


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
As of now enge is able to perform three tasks.<br>
`test` feeds the request payload with provided config options or arguments and dispatches a test job to the Testing Farm.<br>
`report` outputs the test results back to the command line.<br>
`rerun` re-dispatches failed or errored test jobs.

##### Test

The goal of enge is to make requesting test jobs as easy as possible.<br>
A default artifact to install (if not specified otherwise) is the one available in the compose.<br>
The `--brew` and `--copr` options denote which type of a build artifact is to be requested for testing.<br>
Instead of looking for build IDs to pass to the payload, all you need to know is a reference for a pull request number (e.g. pr123) which triggered the build you need to test. In case you have the Build ID handy, you can use that instead of the reference.<br> For brew builds you just need to know the release version (e.g. 0.1.2-3). Or pass the TaskID as a value of the respective option.<br>
Multiple `--plan` options can be specified and will be dispatched in separate jobs.
`--tier` options allow you to run predefined test tiers from your configuration.
`--set` options allow you to use pre-configured test sets (see Test Sets section below).
When using `--planfilter` or `--test` to specify singular test it is disallowed to request multiple `--plan` options in one command.<br>
Use `--wait` if waiting for a successful response from the endpoint is required.
If for any reason you would need to verify the validity of the raw payload, use `--dryrun` to get it pretty-printed to the command line.

Use `--set-tag` to tag archived task files with custom tags for later retrieval (can be used multiple times).
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
```

##### Test Sets

Test sets are pre-configured test scenarios that can be defined in your configuration file under `[tests.set]`. They allow you to define reusable test configurations with specific source/target composes, tiers, architectures, artifacts, and environment variables.

**Configuration Example:**
```toml
[tests.set.pre-release-smoke]
source = "9.7"
target = "10.1"  # Optional, will be auto-derived if not specified
tiers = ["tier0", "tier1"]
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

When a test set defines multiple tiers and architectures, a separate payload is generated for every combination of tier and architecture. For example, with `tiers = ["tier0", "tier1"]` and `architectures = ["x86_64", "aarch64"]`, four payloads will be dispatched: one for each (tier, architecture) pair (tier0 on x86_64, tier0 on aarch64, tier1 on x86_64, tier1 on aarch64).

**Usage:**
- Use `--set <set-name>` to run a predefined test set
- Multiple sets can be specified: `--set set1 --set set2`
- CLI arguments override test set configurations when provided
- Test sets can define artifacts, environment variables, and test selection criteria
- The `--source` argument is not required when using `--set` (it's defined in the set configuration)

**Priority Order:** CLI arguments > Test Set configuration > Main configuration

##### Report
With the report command you are able to get the results of the requested jobs straight to the command line.<br>
It works by parsing the xunit field in the request response.<br>
Results can be reported back in two levels - the default plan overview and `--show-tests` for a detailed tests overview.<br>
You can chain the report command with test command and use the `-w/--wait` argument to get the results back whenever the requests state is complete (or error in which case the job results cannot be and won't be reported due to the non-existent xunit field).<br>
`enge test` automatically stores the request IDs from the latest dispatched job - the primary location to store and read the data from is `/tmp/latest_enge_jobs` file. The file is also saved with a timestamp to the working directory just for a good measure.
Default invocation `enge report` parses the tasks stored in the latest file at `/tmp/latest_enge_jobs`.<br>
You can specify a different path to the file with `-f/--file` or pass the jobs to get report for straight to the commandline with `-i/--input`. Both can be used multiple times, the task IDs will get aggregated and reported in a single table.<br>
You can also use `--get-tag` to query archived task files by tag (see [Task Archiving and Tagging](#task-archiving-and-tagging) section for details).<br>
The tool is able to parse and report for multiple variants of values as long as they are separated by a new-line (in the files) or a `-i/--input` argument (on the commandline). Raw request_ids, artifact URLs (Testing Farm result page URLs) or request URLs are allowed.
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
Reads the same input as the report module - `--file`, `--input` or `--get-tag`, which can be combined.<br>
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

# Query the archive files by tag
enge rerun --get-tag rc --set-tag secondrun --set-tag rc
# or
enge rerun --get-tag rc --set-tag secondrun.rc
```

##### Task Archiving and Tagging

The `--set-tag` and `--get-tag` options provide a powerful way to organize and retrieve test results:

**Setting Tags (`--set-tag`):**
- Available in `test` and `rerun` commands
- Tags archived task files with custom labels for later retrieval
- Can be used multiple times: `--set-tag tag1 --set-tag tag2`
- Tagged files are stored as `filename.tag1.tag2` in the archive directory

**Getting Tagged Results (`--get-tag`):**
- Available in `report` and `rerun` commands
- Query archived task files by tag
- Works with OR logic: `--get-tag tag1 --get-tag tag2` finds files containing either tag
- When looking for a file with a specific combination of tags query for a dot separated single string: `--get-tag tag1.tag2`
- Can be combined with `--file` and `--input` options

**Archive Locations:**
- Latest job IDs: `/tmp/enge_latest_jobs`
- Archived jobs: `~/.enge/jobs_archive/` (configurable)
- Tagged files: `~/.enge/jobs_archive/filename.tag1.tag2`

**Examples:**
```bash
# Test with custom tags
enge test --copr pr123 --tier tier0 --set-tag regression --set-tag pr123

# Get results by tag
enge report --get-tag regression

# Report results for both regression and pr123 tags
enge report --get-tag regression --get-tag pr123

# Rerun failed jobs tagged with 'rc'
enge rerun --get-tag rc --fail

# Combine tag search with other inputs
enge report --get-tag regression --file ~/my_jobs --input 8f4e2e3e-beb4-4d3a-9b0a-68a2f428dd1b
```
