# mypy baseline triage

42 errors across 17 modules. Config: `python_version = 3.11`,
`ignore_missing_imports = True`, `check_untyped_defs = True`.

## Category (a) — potential runtime bugs

**Do NOT fix in this branch.** Each needs a red-first reproduction before any
fix is trusted.

| # | Module | Line | Code | Description |
|---|---|---|---|---|
| 1 | `cancel/__main__.py` | 221 | `call-arg` | `main()` called without `ctx` in `if __name__` guard — TypeError on direct invocation |
| 2 | `dispatch/__main__.py` | 482 | `call-arg` | Same pattern — `sys.exit(main())` missing `ctx` |
| 3 | `reportportal/__main__.py` | 1086 | `call-arg` | Same pattern — `sys.exit(main())` missing `ctx` |
| 4 | `rerun/__main__.py` | 873 | `call-arg` | Same pattern — `main()` missing `ctx` |
| 5 | `rerun/__main__.py` | 667 | `call-arg` | `ReportPortalLaunch()` missing `ctx` in dryrun path of `_create_reportportal_launch()` |
| 6 | `rerun/__main__.py` | 686 | `call-arg` | Same — real (non-dryrun) path |
| 7 | `rerun/__main__.py` | 727 | `attr-defined` | `submit.print_header = True` — `SubmitTest` has no `print_header` attribute (removed/renamed); assignment silently creates instance attr but controlled behavior is lost |
| 8 | `rerun/__main__.py` | 869 | `attr-defined` | Same — `submit.print_header = False` |
| 9 | `reportportal/operations.py` | 563 | `arg-type` | `lid` from `launch.get("id")` is `int|Any|None`; `update_launch(lid, ...)` expects `int` — None would produce a bad API request |
| 10 | `reportportal/operations.py` | 667 | `arg-type` | Same — `delete_launch(lid)` with potentially-None `lid` |
| 11 | `utils/task_resolver.py` | 59 | `arg-type` | `ManifestReader.get_run()` can return `None`; `get_task_ids(None)` would crash |
| 12 | `utils/reportportal_helper.py` | 37 | `return-value` | Declared return `str`, actual return is `rp_env_vars.get(key)` → `str|None`; callers assuming `str` would crash on `None` |
| 13 | `reportportal/utils.py` | 94 | `dict-item` | `"WARN": ("tmt-log")` — parentheses without trailing comma produce a `str`, not a 1-tuple; iteration would yield individual characters instead of the filename |

**Adjacent finding** (not a mypy error, but spotted during triage of #13):
`reportportal/utils.py:90` — `"test_debug.log" "leapp-preupgrade.log" "leapp.out"`
are three adjacent string literals without commas. Python concatenates them into
one string `"test_debug.logleapp-preupgrade.logleapp.out"`. The ERROR tuple has
5 entries instead of the intended 7; `leapp-preupgrade.log` and `leapp.out`
will never match any artifact filename.

### Summary of category (a)

- **6 call-arg errors** — the exact regression class (missing `ctx` from DI
  migration). Four are `if __name__ == "__main__"` guards (crash on direct
  invocation); two are live code paths in `rerun/__main__._create_reportportal_launch()`.
- **2 attr-defined errors** — `SubmitTest.print_header` was removed/renamed;
  `rerun/__main__` still writes to it. Dead assignment (Python allows dynamic
  attrs) but the behavior it was supposed to control is lost.
- **3 arg-type errors** — None reaching typed parameters without guards.
- **1 return-value error** — return type annotation lies about Optional.
- **1 dict-item error** — missing trailing comma turns 1-tuple into bare string.

---

## Category (b) — annotation noise

Code is correct at runtime; types are imprecise or mypy needs more annotations.

| # | Module | Line | Code | Description |
|---|---|---|---|---|
| 1 | `cancel/__main__.py` | 31 | `var-annotated` | `cancel_results = []` needs type hint |
| 2 | `dispatch/__main__.py` | 394 | `name-defined` | `global artifact_type` with no module-level def — works at runtime (assignment creates it), `global` is vestigial |
| 3 | `dispatch/__main__.py` | 411 | `name-defined` | Same — use of `artifact_type` after 394 assignment |
| 4 | `dispatch/__main__.py` | 441 | `name-defined` | Same |
| 5 | `dispatch/pin_compose.py` | 343 | `var-annotated` | `_repin_cache = {}` needs type hint |
| 6 | `dispatch/set_flow.py` | 214 | `arg-type` | `None` passed for `tier_config: Dict[str, str]` — docstring shows `([], None, ...)` is an expected call pattern; annotation should be `Optional` |
| 7 | `dispatch/tf_send_request.py` | 140 | `arg-type` | `self.artifacts: List[Dict[str, str]]` but dict contains `packages: List[str]`; annotation too narrow |
| 8 | `dispatch/tf_send_request.py` | 308 | `dict-item` | `"skip_guest_setup": True` — heterogeneous dict value types in nested `env_settings` |
| 9 | `report/__main__.py` | 99 | `var-annotated` | `regroup_results_plans = {}` needs type hint |
| 10 | `report/__main__.py` | 101 | `var-annotated` | `regroup_results_tests = {}` needs type hint |
| 11 | `report/concurrent_parser.py` | 25 | `arg-type` | `int|None` vs `ExitCode|None` — ExitCode is IntEnum, works at runtime |
| 12 | `report/concurrent_parser.py` | 497 | `assignment` | `Future[TaskResult]` vs `Future[TaskResult|None]` — generic variance in as_completed() |
| 13 | `report/concurrent_parser.py` | 498 | `index` | Same Future variance — dict key type mismatch |
| 14 | `report/concurrent_parser.py` | 504 | `assignment` | `future.result()` returns `TaskResult|None` assigned to `TaskResult` — result() return type includes None for timeouts, not applicable here |
| 15 | `report/concurrent_parser.py` | 508 | `union-attr` | `result.xunit_content` where `result` typed `TaskResult|None` — None case handled by try/except, mypy doesn't narrow |
| 16 | `reportportal/utils.py` | 704 | `arg-type` | `append((ArtifactFile, str))` to `list[(ArtifactFile, None)]` — list invariance; code is correct |
| 17 | `reportportal/utils.py` | 712 | `return-value` | Same list invariance — `list[(AF, None)]` vs `list[(AF, str|None)]` |
| 18 | `rerun/__main__.py` | 101 | `var-annotated` | `rerun_payloads = []` needs type hint |
| 19 | `rerun/__main__.py` | 102 | `var-annotated` | `parsed_dict = {}` needs type hint |
| 20 | `rerun/__main__.py` | 103 | `var-annotated` | `processed_data = {}` needs type hint |
| 21 | `rerun/__main__.py` | 104 | `var-annotated` | `rerun_uuids = []` needs type hint |
| 22 | `utils/source_target_parser.py` | 547 | `var-annotated` | `env_vars = {}` needs type hint |
| 23 | `utils/tf_artifact.py` | 224 | `assignment` | `packages[0]` fallback chain can be `None` but var typed `str` |
| 24 | `utils/tf_artifact.py` | 362 | `var-annotated` | `build_info = []` needs type hint |
| 25 | `utils/tf_artifact.py` | 746 | `var-annotated` | `tasks = []` needs type hint |
| 26 | `reportportal/operations.py` | 563 | — | *(listed under (a) — see above)* |

*(26 entries — operations.py 563/667 are listed under (a), not here)*

---

## Category (c) — mypy false positive

| # | Module | Line | Code | Description |
|---|---|---|---|---|
| 1 | `report/concurrent_parser.py` | 78 | `attr-defined` | `self.session.mount(...)` — session set to `requests.Session()` on line 71, then used on line 78; mypy can't narrow mutable instance attrs in same method |
| 2 | `report/concurrent_parser.py` | 79 | `attr-defined` | Same — HTTPS mount |
| 3 | `utils/arg_parser.py` | 15 | `assignment` | `try: import argcomplete / except: argcomplete = None` — standard optional-import pattern; mypy sees Module vs None conflict |

---

## Error-code distribution by module

For reference when narrowing the ratchet (`call-arg` must never be disabled —
inline suppression only):

| Module | Error codes present |
|---|---|
| `cancel/__main__` | var-annotated, **call-arg** |
| `dispatch.__main__` | name-defined, **call-arg** |
| `dispatch.pin_compose` | var-annotated |
| `dispatch.set_flow` | arg-type |
| `dispatch.tf_send_request` | arg-type, dict-item |
| `report.__main__` | var-annotated |
| `report.concurrent_parser` | arg-type, attr-defined, assignment, index, union-attr |
| `reportportal.__main__` | **call-arg** |
| `reportportal.operations` | arg-type |
| `reportportal.utils` | dict-item, arg-type, return-value |
| `rerun.__main__` | var-annotated, **call-arg**, attr-defined |
| `utils.arg_parser` | assignment |
| `utils.config_parser` | arg-type |
| `utils.reportportal_helper` | return-value |
| `utils.source_target_parser` | var-annotated |
| `utils.task_resolver` | arg-type |
| `utils.tf_artifact` | assignment, var-annotated |
