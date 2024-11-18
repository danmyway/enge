# Table of contents
1. [Introduction](#tesar)
2. [Prerequisites](#prerequisites)
   1. [API configuration](#api-configuration)
       1. [Testing farm API key](#testing-farm-api-key)
   2. [Cloud Resources Tag](#cloud-resources-tag)
   3. [Package dependencies](#package-dependencies)
3. [Setting up](#setting-up)
   1. [Installation](#installation)
       1. [Clone](#clone)
       2. [Install](#install)
       3. [Set up the configuration file](#set-up-configuration-file)
       4. [Config file template](#config-file-template)
   2. [Usage](#usage)
       1. [Commands](#commands)
          1. [Test](#test)
          2. [Report](#report)
       2. [Examples](#examples)
4. [Currently used variables](#currently-used-variables)
    1. [Payload](#payload)
    2. [Mapped composes](#mapped-composes)
        1. [List globally available composes](#list-globally-available-composes)
            1. [Public ranch](#public-ranch)
            2. [Private ranch](#private-ranch)



ENGE
=
### ENGE is a New Generation of the tesar Tool
#### Send requests to the Testing Farm API through the command line interface
Posting requests to the Testing farm requires some json payload manipulation, which is inconvenient and might be time-consuming.<br><br>
For example, to be able to send a request to test on three different composes you would need to edit 'compose', 'id' and 'distro' in the example payload below twice!<br>
To be able to send request for testing just two individual test plans on three composes, you would even need to change the 'name' three times as well.<br><br>
That is six changes and six `https POST` commands sent to command line.<br>
That is IMHO **exactly six times more**, than it should be.

> Payload: {"api_key": "foo", "test": {"fmf": {"url": "https://github.com/oamg/convert2rhel", "ref": "main", "path": ".", "name": "/plans/tier0/basic_sanity_checks"}, "script": null, "sti": null}, "state": "new", "environments": [{"arch": "x86_64", "os": {"compose": "CentOS-8.4"}, "pool": null, "variables": null, "secrets": null, "artifacts": [{"id": "4533523:epel-8-x86_64", "type": "fedora-copr-build", "packages": ["convert2rhel"]}], "settings": null, "tmt": {"context": {"distro": "centos-8.4", "arch": "x86_64"}}, "hardware": null}], "notification": null, "settings": null, "created": "None", "updated": "None"}

With this script, you will be able to do all of the above with just one command!

```
tesar test copr c2r -ref pr123 -g github oamg main -p /plans/tier0/basic_sanity_checks /plans/tier1/rhsm -c cos84 cos7 ol8
```

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
sudo dnf copr enable danmyway/tesar
```

If you are brave enough, though not advised, you can try out the development repository.

```
sudo dnf copr enable danmyway/tesar-devel
```

#### Install

```
sudo dnf install tesar
```
#### Set up the configuration file
Set up config file with obtained Testing Farm API key and Cloud Resources Tag that helps with tracking cloud spend.

```
touch ~/.config/tesar && printf "[testing-farm]\nAPI_KEY={your testing farm api key}\n[cloud-resources-tag]\nCLOUD_RESOURCES_TAG={tag}" > ~/.config/tesar
```
or copy provided file and edit with your favourite editor, e.g.
```
cp ./tesar.temp ~/.config/tesar
vim ~/.config/tesar
```

##### Config file template
```
[testing-farm]
API_KEY=
[cloud-resources-tag]
CLOUD_RESOURCES_TAG=
```
### Usage

#### Commands
As of now tesar is able to perform two tasks.
`test` feeds the request payload with provided arguments and dispatches a test job to the Testing Farm.
`report` feeds the test results back to the command line and downloads the log files.

##### Test
> **_NOTE:_** Even though there are some mentions of leapp and leapp-repository in the code. The Leapp project is not yet fully supported by tesar for test jobs dispatching.

The goal of tesar is to make requesting test jobs as easy as possible.<br>
Instead of looking for build IDs to pass to the payload, all you need to know is a `--reference` for a pull request number associated with the build you need to test. For brew builds you just need to know the release version.<br>
In case you have the Build ID handy, you can use that instead of the reference.<br>
Use `-g/--git` to point from where the test metadata and code should be run. Specify the repository base e.g. github, repository owner and the branch from which the tests should run.<br>
Multiple `--plans` can be specified and will be dispatched in separate jobs.
When using `--planfilter` or `--testfilter` to specify singular test it is disallowed to request multiple `--plans` in one command.<br>
You can look for possible [targeted OS' below](#mapped-composes), multiple can be requested and will be dispatched in separate jobs.
Use `-w/--wait` to override the default 20 seconds waiting time for successful response from the endpoint, or `-nw/--no-wait` to skip the wait time.
If for any reason you would need the raw payload, use `--dry-run` to get it printed to the command line or use `--dry-run-cli` to print out the full usable `http POST` command.
When no `-t/--target` option is specified, the request is sent for all mapped target composes for their respective tested packages.
UEFI boot method can be requested by using the `-u/--uefi` option. Please note, that **only Alma and Rocky 8.9+** targets have the boot method available.
Default limit for plans to be run in parallel is 42, to override the default use the `--parallel-limit` option.

##### Report
With the report command you are able to get the results of the requested jobs straight to the command line.<br>
It works by parsing the xunit field in the request response.<br>
Default invocation `tesar report` parses the `./report_jobs` from the tesar directory.<br>
Results can be reported back in two levels - `l1` for plan overview and `l2` for tests overview.<br>
You can chain the report command with test command and use the `-lt/--latest` and `-w/--wait` argument to get the results back whenever the requests state is complete (or error in which case the job results cannot be and won't be reported due to the non-existent xunit field).<br>
`tesar test` automatically stores the request IDs from the latest dispatched job - the primary location to store and read the data from is `/tmp/latest_tesar_jobs` file. The file is also saved with a timestamp to the working directory just for a good measure.
You can specify a different path to the file with `-f/--file` or pass the jobs to get report for straight to the commandline with `-c/--cmd`. Both can be used multiple times, the task IDs will get aggregated and reported in a single table.<br>
The tool is able to parse and report for multiple variants of values as long as they are separated by a new-line (in the files) or a `-c/--cmd` argument (on the commandline). Raw request_ids, artifact URLs (Testing Farm result page URLs) or request URLs are allowed.
In case you want to get the log files stored locally, use `-d/--download-logs`. Log files for pytest runs will be stored in `/var/tmp/tesar/logs/{request_id}_log/`. In case there are multiple plans in one pipeline, the logs should get divided in their respective plan directories.

Corresponding return code is set based on the results with following logic:
 * 0 - The results are complete for each request and all are pass
 * 1 - Python exception or bailout
 * 2 - No error was hit, at least one fail was found
 * 3 - At least one error was hit
 * 4 - At least one request didn't have any result
 * everything else - consult with Tesar maintainer(s)

The default way to show results is by showing each run details as a separate table. In order to combine test results of several different tft runs you can use comparison mode which is triggered by the `--compare` flag of `tesar report`.

```
❯ tesar report -c 8f4e2e3e-beb4-4d3a-9b0a-68a2f428dd1b -c c3726a72-8e6b-4c51-88d8-612556df7ac1 --short --unify-results=tier2=tier2_7to8 --compare
```

#### Examples

```
# Test copr build for PR#123 with plan basic_sanity_check on CentOS 8.4
$ tesar test copr c2r -ref pr123 -g github oamg main -p /plans/tier0/basic_sanity_checks -c cos84

# Specify which composes you want to run test plan (in this case tier0)
$ tesar test copr c2r -ref pr123 -g gitlab.cee.redhat xyz testing -p /plans/tier0 -c ol7 cos8

# Run every test plan for brew build 0.12-3 on all composes
$ tesar test brew c2r -ref 0.12-3 -g github oamg main -p /plans

# Specify more individual test plans
$ tesar test brew c2r -ref 0.12-3 -git github oamg main -p /plans/tier0/basic_sanity_checks /plans/tier1/rhsm

```

```
# Get results for the requests in the default file ./latest_jobs
$ tesar report

# Report the latest test run on the plan level
$ tesar report --latest

# Report from custom file on the test level
$ tesar report --level2 --file /home/username/my_jobs_file

# Pass requests' references to the commandline
$ tesar report --cmd d60ee5ab-194f-442d-9e37-933be1daf2ce https://api.endpoint/requests/9f42645f-bcaa-4c73-87e2-6e1efef16635

# Shorten the displayed test and plan names
$ tesar report --level2 --cmd 9f42645f-bcaa-4c73-87e2-6e1efef16635 /home/username/my_jobs_file --short

```

# Currently used variables

## Payload

Link to the testing farm payload documentation:<br>
https://testing-farm.gitlab.io/api/ <br>
For convert2RHEL testing we are currently using this form of payload:
> **_NOTE:_** <br> Some of the targeted instances disallow to use root login when connecting through the ssh.
> Hardcoded post_install_script is sent with each request to enable root login on the target.
```json lines
    payload_raw = {
        "api_key": api_key,
        "test": {
            "fmf": {
                "url": git_url,
                "ref": git_branch,
                "name": plan,
                "plan_filter": planfilter,
                "test_filter": testfilter,
            }
        },
        "environments": [
            {
                "arch": architecture,
                "os": {"compose": compose},
                "pool": pool,
                "variables": {
                    "SOURCE_RELEASE": source_release,
                    "TARGET_RELEASE": target_release,
                },
                "artifacts": [
                    {
                        "id": artifact_id,
                        "type": artifact_type,
                        "packages": [package],
                    }
                ],
                "settings": {
                    "provisioning": {
                        "post_install_script": POST_INSTALL_SCRIPT,
                        "tags": {"BusinessUnit": CLOUD_RESOURCES_TAG},
                    }
                },
                "tmt": {
                    "context": {
                        "distro": tmt_distro,
                        "arch": tmt_architecture,
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


## Mapped composes
We are requesting official publicly available free instances from the AWS marketplace, if possible.<br>
At this moment this applies to the Alma Linux, Oracle Linux and Rocky Linux instances.<br>
CentOS 7 latest is the last one remaining to be implemented.<br>
Sadly, there is no instance of any relevance for CentOS 8 latest available.
```
# RHEL8 targets
cos8: CentOS-8-latest
ol8: OL8.9-x86_64-HVM-2024-02-02
al88: AlmaLinux OS 8.8.20230524 x86_64
al8: AlmaLinux OS 8.10.20240530 x86_64
roc88: Rocky-8-EC2-Base-8.8-20230518.0.x86_64
roc8: Rocky-8-EC2-Base-8.10-20240528.0.x86_64
str8: CentOS-Stream-8

# RHEL7 targets
cos7: CentOS-7-latest
ol7: OL7.9-x86_64-HVM-2023-01-05
```

### List globally available composes
>**__INFO:__** Mostly deprecated.<br>
> As far as convert2rhel is concerned, the majority of those images is custom-built, thus we try to use the publicly available officially maintained instances from the AWS.

Testing farm has many available composes on both public and private ranch.<br>
To list them use commands bellow.

#### Public ranch

https://api.dev.testing-farm.io/v0.1/composes

`https GET https://api.dev.testing-farm.io/v0.1/composes`

#### Private ranch

`curl -s https://gitlab.cee.redhat.com/baseos-qe/citool-config/-/raw/production/variables-composes.yaml | grep 'compose:' | tr -s ' '`
