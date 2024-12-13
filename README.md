# Table of contents
1. [Introduction](#enge)
2. [Prerequisites](#prerequisites)
   1. [API configuration](#api-configuration)
       1. [Testing farm API key](#testing-farm-api-key)
   2. [Cloud Resources Tag](#cloud-resources-tag)
3. [Setting up](#setting-up)
   1. [Installation](#installation)
       1. [Install](#install)
      2[Set up the configuration file](#set-up-the-configuration-file)
   2. [Usage](#usage)
       1. [Commands](#sub-commands)
          1. [Test](#test)
          2. [Report](#report)
          3. [Rerun](#rerun)
       2. [Examples](#examples)
4. [Currently used variables](#currently-used-variables)
    1. [Payload](#payload)
    2. [List globally available composes](#list-globally-available-composes)
        1. [Public ranch](#public-ranch)
        2. [Private ranch](#private-ranch)



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
The template for the config file is available in the root of the repository. The default locations for the config file are `~/.config/enge.ini` or `~/.enge.ini`. A custom path to a config file can be specified through the commandline option -c.<br>
In case of any question, please reach out to the project maintainer(s).

### Usage

#### Sub-Commands
As of now enge is able to perform two tasks.<br>
`test` feeds the request payload with provided config options or arguments and dispatches a test job to the Testing Farm.<br>
`report` outputs the test results back to the command line.

##### Test

The goal of enge is to make requesting test jobs as easy as possible.<br>
The `--brew` and `--copr` mandatory options denote which type of a build artifact is to be requested for testing.<br>
Instead of looking for build IDs to pass to the payload, all you need to know is a reference for a pull request number (e.g. pr123) which triggered the build you need to test. In case you have the Build ID handy, you can use that instead of the reference.<br> For brew builds you just need to know the release version (e.g. 0.1.2-3). Or pass the TaskID as a value of the respective option.<br>
Multiple `--plans` can be specified and will be dispatched in separate jobs.
When using `--planfilter` or `--testfilter` to specify singular test it is disallowed to request multiple `--plans` in one command.<br>
Use `-w/--wait` to override the default 20 seconds waiting time for successful response from the endpoint, or `-nw/--no-wait` to skip the wait time.
If for any reason you would need to verify the validity of the raw payload, use `--dryrun` to get it pretty-printed to the command line.
When no `-t/--target` option is specified, the request is sent for all mapped target composes for their respective tested packages.
UEFI boot method can be requested by using the `-u/--uefi` option.
Default limit for plans to be run in parallel is set to 20, to override the default use the `--parallel-limit` option or change the option in the config file.

##### Report
With the report command you are able to get the results of the requested jobs straight to the command line.<br>
It works by parsing the xunit field in the request response.<br>
Results can be reported back in two levels - the default `l1` for plan overview and `-l2/--level2` for a tests overview.<br>
You can chain the report command with test command and use the `-w/--wait` argument to get the results back whenever the requests state is complete (or error in which case the job results cannot be and won't be reported due to the non-existent xunit field).<br>
`enge test` automatically stores the request IDs from the latest dispatched job - the primary location to store and read the data from is `/tmp/latest_enge_jobs` file. The file is also saved with a timestamp to the working directory just for a good measure.
Default invocation `enge report` parses the tasks stored in the latest file at `/tmp/latest_enge_jobs`.<br>
You can specify a different path to the file with `-f/--file` or pass the jobs to get report for straight to the commandline with `-c/--cmd`. Both can be used multiple times, the task IDs will get aggregated and reported in a single table.<br>
The tool is able to parse and report for multiple variants of values as long as they are separated by a new-line (in the files) or a `-c/--cmd` argument (on the commandline). Raw request_ids, artifact URLs (Testing Farm result page URLs) or request URLs are allowed.
In case you want to get the log files stored locally, use `-d/--download-logs`. Log files for pytest runs will be stored in `/var/tmp/enge/logs/{request_id}_log/`. In case there are multiple plans in one pipeline, the logs should get divided in their respective plan directories.

Corresponding return code is set based on the results with following logic:
 * 0 - The results are complete for each request and all are pass
 * 1 - Python exception or bailout
 * 2 - No error was hit, at least one fail was found
 * 3 - At least one error was hit
 * 4 - At least one request didn't have any result
 * everything else - consult with Tesar maintainer(s)

The default way to show results is by showing each run details as a separate table. In order to combine test results of several different tft runs you can use comparison mode which is triggered by the `--compare` flag of `enge report`.

```
❯ enge report -c 8f4e2e3e-beb4-4d3a-9b0a-68a2f428dd1b -c c3726a72-8e6b-4c51-88d8-612556df7ac1 --short --unify-results=tier2=tier2_7to8 --compare
```

##### Rerun
Rerun tasks which report as FAILED or ERROR.<br>
Only works for whole plans.<br>
Reads the same input as the report module - `--file`, `--cmd` or `--tag`, which can be combined.<br>
Use `--error` or `--fail` if you want to further specify which type of non-zero result you want to re-run, default is both results. If the whole task reports state error, the original plan filtering will be used, otherwise each of the failing/erroring plans will be passed to the plan name field connected by a pipe `|`, meaning all qualified plans from a single original request will be sent as one request for a re-run.<br>
Use `--dryrun` to only display the qualified plans, don't actually send any payload to the Testing Farm.


#### Examples

```
# Test latest build from main (most of the arguments set through the config file)
$ enge test --copr

# Test copr build for PR#123 with plan named basic_sanity_check on all targets
$ enge test --copr pr123 -p /plans/tier0/basic_sanity_checks

# Specify which composes you want to run test plan (in this case tier0 on RHEL9)
$ enge test --copr pr123 -p /plans/tier0 -7 rhel9

# Run every test plan for brew build 0.12-3 on all composes
$ enge test --brew 0.12-3 -p /plans

# Specify more individual test plans
$ enge test --brew 0.12-3 -p /plans/tier0/basic_sanity_checks /plans/tier1/whatever_else

```

```
# Get results for the requests in the latest file /tmp/enge_latest_jobs
$ enge report

# Report from custom file on the test level
$ enge report --level2 --file ~/my_jobs_file

# Pass requests' references to the commandline
$ enge report --cmd d60ee5ab-194f-442d-9e37-933be1daf2ce --cmd https://api.endpoint/requests/9f42645f-bcaa-4c73-87e2-6e1efef16635

# Shorten the displayed test and plan names
$ enge report --level2 --cmd 9f42645f-bcaa-4c73-87e2-6e1efef16635 --short

```

# Currently used variables

## Payload

Link to the testing farm payload documentation:<br>
https://testing-farm.gitlab.io/api/ <br>
As of now, the payload yields the following format.

```json lines
        {"Authorization": "Bearer {api_key}"}
        {
            "test": {
                "fmf": {
                    "url": tests_git_url,
                    "ref": tests_git_branch,
                    "name": plan,
                    "plan_filter": planfilter,
                    "test_filter": testfilter,
                }
            },
            "environments": [
                {
                    "arch": architecture,
                    "os": {"compose": compose},
                    "artifacts": [
                        {
                            "id": artifact_id,
                            "type": artifact_type,
                            "packages": [package],
                        }
                    ],
                    "settings": {
                        "provisioning": {
                            "tags": {"BusinessUnit": business_unit_tag},
                        }
                    },
                    "tmt": {
                        "context": {
                            "distro": tmt_distro,
                            "arch": architecture,
                            "boot_method": boot_method,
                        }
                    },
                    "hardware": {
                        "boot": {
                            "method": boot_method,
                        }
                    },
                }
            ],
            "settings": {"pipeline": {"parallel-limit": parallel_limit}},
        }
```

### Other
#### List globally available composes

The Testing Farm has many available composes on both public and private ranch.<br>
To list them use commands bellow:

#### Public ranch

https://api.dev.testing-farm.io/v0.1/composes

`https GET https://api.dev.testing-farm.io/v0.1/composes`

#### Private ranch

`curl -s https://gitlab.cee.redhat.com/baseos-qe/citool-config/-/raw/production/variables-composes.yaml | grep 'compose:' | tr -s ' '`
