# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added
- Short flags: `-s` (source), `-t` (target), `-T` (tier), `-p` (plan), `-S` (set), `-n` (dryrun)
- Verbosity control: `-v` for VERBOSE level, `-vv` / `--debug` for DEBUG level
- Output format selection: `-o` / `--format` with `terminal`, `json`, `gitlab` modes
- Test set discovery: `--list-sets` and `--list-sets-detail` for quick configuration review
- Shell completion via `argcomplete` (optional dependency)
- Sensitive field redaction in dry-run payload output
- Structured JSON output (`-o json`) with honest success/failure counts

### Changed
- Migrated terminal output from prettytable/ANSI to rich library (tables, panels, styled text)
- Logging now renders to stderr via dedicated console, keeping stdout clean for data output
- Request summary redesigned as a rich Panel with key-value grid
- Dispatch output batched — summaries printed after all requests complete

### Deprecated
- `--jira` flag in `report` — use `-o gitlab` for merge-request-friendly output

### Fixed
- `-o json` mode no longer silences log output; logs go to stderr independently
- `-o json --dry-run` produces a single JSON document instead of interleaved payloads
- GitLab output format uses triple-backtick fences instead of Jira `{noformat}` tags
- External strings (compose names, plan names, test results) escaped to prevent rich markup injection
- Partial dispatch failures now exit with code 2; previously they incorrectly exited 0 due to a request-counting bug
