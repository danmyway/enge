"""Report-side results.json cache writer.

`enge report` gap-fills `<run_id>.json` + verbatim xunit for
manifest-backed invocations via the `results_parser` gap-fill API.
`enge dispatch` never touches results (hard invariant). Raw-input
invocations (`--file`/`--input`, or any selector with no resolvable
run_id) are a no-op -- see CLAUDE.md "Results.json format" for the full
write policy and terminality predicate.

Invocation -> manifest resolution lives in `utils/manifest_resolution.py`
(`resolve_manifests_for_invocation`, aliased here as
`_resolve_manifests_for_report`) rather than in `utils/task_resolver.py`,
because it needs full manifest objects (envelope fields + per-request
metadata, and run_id-per-task attribution across the possibly-multiple
runs a filter selector can match) rather than the flat task_id list
`task_resolver` hands to the xunit fetch layer. This keeps
`results_parser` import-free of manifest code (by design) and keeps
`task_resolver`'s contract unchanged.
"""

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

import lxml.etree  # type: ignore

from enge.utils.errors import AlreadyFinalizedError, ConflictError, ValidationError
from enge.utils.manifest_resolution import (
    resolve_manifests_for_invocation as _resolve_manifests_for_report,
)
from enge.utils.results_parser import (
    XUNIT_RESULT_MAP,
    finalize_root_verdict,
    init_results_json,
    upsert_task_result,
    write_xunit,
)
from enge.utils.state_paths import results_dir

if TYPE_CHECKING:
    from enge.report.concurrent_parser import TaskResult
    from enge.utils.app_context import AppContext

LOGGER = logging.getLogger(__name__)

# Results-cache-specific terminality predicate. Deliberately NOT the same
# as reportportal.operations._is_tf_task_incomplete, which treats CANCELED
# as incomplete (there is nothing left to poll for RP finish/enrich
# purposes). Here CANCELED IS terminal: it is a valid, final results.json
# entry (dispatch-level cancel, plans: []) that gap-fill must record so a
# later report invocation doesn't keep re-checking it. RP's predicate is
# untouched -- RP is feature-frozen pending sunset.
_TERMINAL_TF_STATES = frozenset({"COMPLETE", "ERROR", "CANCELED", "CANCELLED"})

_SEVERITY_RANK = {
    "SKIPPED": 0,
    "PASSED": 1,
    "CANCELED": 2,
    "FAILED": 3,
    "ERROR": 4,
}


def _is_task_terminal(tf_state: str) -> bool:
    return tf_state.upper() in _TERMINAL_TF_STATES


def _map_verdict(raw_value: Optional[str], *, context: str) -> str:
    """Xunit result -> schema verdict, via the ratified XUNIT_RESULT_MAP.
    Missing or unrecognized values are report-layer policy (not a schema
    concern): map to ERROR and log a WARNING naming the offending value."""
    if not raw_value:
        LOGGER.warning(
            "%s: xunit 'result' attribute missing; treating as ERROR", context
        )
        return "ERROR"
    mapped = XUNIT_RESULT_MAP.get(raw_value.lower())
    if mapped is None:
        LOGGER.warning(
            "%s: unknown xunit verdict %r; treating as ERROR", context, raw_value
        )
        return "ERROR"
    return mapped


def _parse_xunit_plans(xunit_bytes: bytes) -> List[Dict[str, Any]]:
    """Independent, minimal xunit -> schema parser. Deliberately not a
    reuse/extension of report.concurrent_parser.XMLParser: that parser
    truncates plan names for display (`.split(":")[-1]`) and never reads
    per-test time/start-time/end-time attributes, neither of which is
    compatible with the results.json contract (verbatim names, real
    durations/timestamps). Keeping this separate also means the existing
    table-rendering code path is untouched -- caching is a side effect,
    never a behavior change to report output."""
    root = lxml.etree.fromstring(xunit_bytes)
    plans = []
    for suite in root.xpath("//testsuite"):
        plan_name = suite.get("name", "")
        plan_verdict = _map_verdict(suite.get("result"), context=f"plan '{plan_name}'")

        tests = []
        for testcase in suite.xpath("./testcase"):
            test_name = testcase.get("name", "")
            test_verdict = _map_verdict(
                testcase.get("result"), context=f"test '{test_name}'"
            )
            raw_time = testcase.get("time")
            try:
                duration = float(raw_time) if raw_time else 0.0
            except ValueError:
                duration = 0.0

            test_entry: Dict[str, Any] = {
                "name": test_name,
                "verdict": test_verdict,
                "duration_seconds": duration,
            }
            start_time = testcase.get("start-time")
            end_time = testcase.get("end-time")
            if start_time:
                test_entry["start_time"] = start_time
            if end_time:
                test_entry["end_time"] = end_time
            tests.append(test_entry)

        plans.append({"name": plan_name, "verdict": plan_verdict, "tests": tests})
    return plans


def _task_level_verdict(task_result: "TaskResult", plans: List[Dict[str, Any]]) -> str:
    """Task-scoped rollup: prefer TF's own per-task overall result when it
    is a real (non-placeholder) value TF understands; otherwise fall back
    to severity-max over this task's plan verdicts."""
    overall = getattr(task_result, "request_result_overall", None)
    if overall and overall != "Undefined":
        mapped = XUNIT_RESULT_MAP.get(overall.lower())
        if mapped is not None:
            return mapped
        LOGGER.warning(
            "task %s: unknown TF overall result %r; falling back to plan "
            "severity-max",
            task_result.request_uuid,
            overall,
        )
    if plans:
        return max((p["verdict"] for p in plans), key=lambda v: _SEVERITY_RANK[v])
    return "ERROR"


def _build_task_entry(
    task_result: "TaskResult",
    request_meta: Dict[str, Any],
    context: Optional[Dict[str, Any]] = None,
    is_single_set: bool = False,
) -> Dict[str, Any]:
    state = task_result.request_state.upper()
    xunit_bytes = getattr(task_result, "xunit_bytes", None)

    if state in ("CANCELED", "CANCELLED"):
        # Validity rule 1: CANCELED tasks are dispatch-level cancels with
        # no partial results -- plans MUST be [] regardless of anything
        # else on the TaskResult.
        verdict = "CANCELED"
        plans: List[Dict[str, Any]] = []
        total_duration = 0.0
    elif xunit_bytes:
        plans = _parse_xunit_plans(xunit_bytes)
        if plans:
            verdict = _task_level_verdict(task_result, plans)
            total_duration = sum(
                test["duration_seconds"] for plan in plans for test in plan["tests"]
            )
        else:
            # xunit fetched but contained zero testsuites -- treat like
            # the no-xunit case below rather than violating validity rule
            # 3 (PASSED/FAILED tasks must have non-empty plans).
            verdict = "ERROR"
            total_duration = 0.0
    else:
        # Terminal, no xunit artifact (e.g. misconfigured plan filter, or
        # the fetch failed/returned non-200). Contract's second legitimate
        # empty-plans state: entry with verdict ERROR, plans: [] -- the
        # xml file is legitimately absent (gap-fill keys on the JSON
        # entry, never on xml presence). 0.0 total_duration_seconds is the
        # ratified sentinel: the fetch layer does not expose a
        # finished/updated TF timestamp to compute elapsed time from
        # (fire-time coordinator ruling, 2026-07-15/16).
        verdict = "ERROR"
        plans = []
        total_duration = 0.0

    context = context or {}
    dispatch_context = {}
    for key in ("source", "target", "git_ref", "event"):
        value = request_meta.get(key)
        if value is None and is_single_set:
            value = context.get(key)
        dispatch_context[key] = value
    dispatch_context["build_ids"] = request_meta.get("build_ids") or []

    return {
        "task_id": task_result.request_uuid,
        "set": request_meta.get("set"),
        "tier": request_meta.get("tier"),
        "arch": request_meta.get("arch"),
        "source_compose": request_meta.get("source_compose"),
        "target_compose": request_meta.get("target_compose"),
        "dispatched_at": request_meta.get("dispatched_at"),
        "verdict": verdict,
        "total_duration_seconds": total_duration,
        "plans": plans,
        **dispatch_context,
    }


def _cache_one_run(
    manifest: Dict[str, Any],
    matched_task_results: List[Tuple[Any, Dict[str, Any]]],
    output_dir: Path,
) -> None:
    run_id = manifest["run_id"]
    results_path = output_dir / f"{run_id}.json"
    context = manifest.get("context") or {}
    request_sets = {r.get("set") for r in manifest.get("requests", [])}
    is_single_set = len(request_sets) <= 1

    if not results_path.exists():
        try:
            init_results_json(
                run_id=run_id,
                created_at=manifest.get("created_at", ""),
                event=context.get("event") or "",
                source=context.get("source") or "",
                target=context.get("target") or "",
                output_dir=output_dir,
            )
        except FileExistsError:
            pass  # raced with another invocation; gap-fill continues below

    for task_result, request_meta in matched_task_results:
        task_id = task_result.request_uuid
        entry_dict = _build_task_entry(
            task_result, request_meta, context=context, is_single_set=is_single_set
        )
        try:
            upsert_task_result(results_path, entry_dict)
        except AlreadyFinalizedError:
            LOGGER.debug(
                "results cache: run %s is already finalized; skipping "
                "re-upsert of task %s (steady state, nothing rewritten)",
                run_id,
                task_id,
            )
            continue
        except ConflictError:
            LOGGER.warning(
                "results cache: conflicting cached entry for task %s in run "
                "%s; keeping the existing entry",
                task_id,
                run_id,
            )
            continue

        xunit_bytes = getattr(task_result, "xunit_bytes", None)
        if xunit_bytes:
            try:
                write_xunit(run_id, task_id, xunit_bytes, output_dir)
            except ConflictError:
                LOGGER.warning(
                    "results cache: conflicting cached xunit for task %s in "
                    "run %s; keeping the existing file",
                    task_id,
                    run_id,
                )

    expected_count = len(manifest.get("requests", []))
    try:
        finalize_root_verdict(results_path, expected_count=expected_count)
    except ValidationError:
        LOGGER.warning(
            "results cache: cannot finalize run %s (unexpected task count)",
            run_id,
        )


def cache_report_results(ctx: "AppContext", task_results: List[Any]) -> None:
    """Gap-fill results.json + xunit for every manifest matched by this
    report invocation. No-op for raw-input invocations. Never raises: a
    caching failure must not fail the report command (CLAUDE.md
    "Results.json format" write policy) -- the user's table must still
    render."""
    try:
        manifests = _resolve_manifests_for_report(ctx)
    except Exception:  # noqa: BLE001
        LOGGER.warning(
            "results cache: failed to resolve manifest(s) for this "
            "invocation; skipping",
            exc_info=True,
        )
        return
    if not manifests:
        return

    output_dir = results_dir(ctx.config)

    index: Dict[str, Tuple[str, Dict[str, Any]]] = {}
    for manifest in manifests:
        run_id: str = manifest["run_id"]
        for request_meta in manifest.get("requests", []):
            task_id = request_meta.get("task_id")
            if task_id:
                index[task_id] = (run_id, request_meta)

    by_run: Dict[str, List[Tuple[Any, Dict[str, Any]]]] = {
        manifest["run_id"]: [] for manifest in manifests
    }
    for task_result in task_results:
        task_id = task_result.request_uuid
        entry = index.get(task_id)
        if entry is None:
            LOGGER.warning(
                "results cache: task %s not found in the resolved "
                "manifest(s); skipping (never fabricating metadata)",
                task_id,
            )
            continue
        run_id, request_meta = entry
        if not _is_task_terminal(task_result.request_state):
            continue
        by_run[run_id].append((task_result, request_meta))

    for manifest in manifests:
        run_id = manifest["run_id"]
        try:
            _cache_one_run(manifest, by_run.get(run_id, []), output_dir)
        except Exception:  # noqa: BLE001
            LOGGER.warning(
                "results cache: unexpected error caching run %s",
                run_id,
                exc_info=True,
            )
