"""Report-side results.json cache writer.

`enge report` gap-fills `<run_id>.json` + verbatim xunit for
manifest-backed invocations via the `results_parser` gap-fill API.
`enge dispatch` never touches results (hard invariant). Raw-input
invocations (`--file`/`--input`, or any selector with no resolvable
run_id) are a no-op -- see docs/results-json-schema.md for the full
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
    TASK_ENTRY_KEYS,
    XUNIT_RESULT_MAP,
    TaskEntry,
    finalize_root_verdict,
    init_results_json,
    read_raw_results_json,
    rewrite_finalized_results,
    stale_task_keys,
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
    run_id: str = "?",
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
        LOGGER.warning(
            "results cache: no xunit artifact collected for task %s in "
            "run %s; verdict recorded as ERROR, no archive written",
            task_result.request_uuid,
            run_id,
        )
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
    # rerun_of/artifacts_url/plan have no run-envelope equivalent to fall
    # back to (there is no such thing as a run's "envelope plan" or
    # "envelope rerun_of") -- straight copy, None when the manifest entry
    # lacks the key, regardless of single-set/multi-set.
    for key in ("rerun_of", "artifacts_url", "plan"):
        dispatch_context[key] = request_meta.get(key)
    # plan_filter is deliberately never written to the manifest at
    # dispatch time (maintainer ruling: it's still in the TF API request
    # body, so no in-flight write is needed) -- sourced exclusively from
    # the harvest's already-live-fetched TaskResult, not request_meta.
    # Normalizes TaskResult.request_plan_filter's "" default to None,
    # matching this schema's established none-vs-empty convention.
    dispatch_context["plan_filter"] = (
        getattr(task_result, "request_plan_filter", None) or None
    )

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


# ---------------------------------------------------------------------------
# Refresh (`enge report --refresh`).
#
# A finalized results.json is immutable through the gap-fill API, which is
# what makes two defects permanent: a task harvested before TF published
# its xunit stays ERROR + [] forever, and a cache written by an older enge
# version keeps whatever key set that version emitted. Refresh is the one
# sanctioned repair, and every guard below exists to keep it from becoming
# a way to lose data. See docs/results-json-schema.md, "Refresh".
# ---------------------------------------------------------------------------

# The three keys the content gate (G3) governs. Everything else on a task
# entry is metadata and is fill-only (G4).
_CONTENT_KEYS = ("verdict", "plans", "total_duration_seconds")

# Distinguishes "key absent" from "key present and null" while merging --
# `None` cannot, and the difference is the whole point of the raw read.
_ABSENT = object()


def _is_finalized(results_path: Path) -> bool:
    """True when the file exists, parses, and carries a non-null root
    verdict. A parse failure is not a refresh opportunity: the normal path
    already logs it, and rewriting a file we cannot read is not a repair."""
    try:
        return read_raw_results_json(results_path).get("verdict") is not None
    except (ValidationError, OSError):
        return False


def _merge_refreshed_entry(
    cached: Dict[str, Any], fresh: Dict[str, Any]
) -> Tuple[Dict[str, Any], bool, bool]:
    """Merge one cached entry with its fresh harvest under G3 and G4.

    Returns (merged, recovered, filled).

    G3 (content gate): `verdict`/`plans`/`total_duration_seconds` come from
    `fresh` if and only if the cached entry is the recoverable ERROR + []
    shape. `CANCELED` + [] is a legitimate terminal state (a dispatch-level
    cancel has no results to publish, ever) and is never replaced; neither
    is any entry that already recorded plans.

    G4 (fill-only): every other key except `task_id` is taken from `fresh`
    only where the cached entry has nothing -- absent, null, or (for
    `build_ids` alone, whose empty state is `[]` rather than null) empty. A
    populated cached value wins even when `fresh` disagrees: the cache is
    the historical record of what dispatch knew, and a later harvest does
    not get to rewrite it.
    """
    merged = dict(cached)

    recovered = False
    if cached.get("verdict") == "ERROR" and cached.get("plans") == []:
        for key in _CONTENT_KEYS:
            merged[key] = fresh[key]
        recovered = any(merged[key] != cached.get(key) for key in _CONTENT_KEYS)

    filled = False
    for key in TASK_ENTRY_KEYS:
        if key == "task_id" or key in _CONTENT_KEYS:
            continue
        current = cached.get(key, _ABSENT)
        is_empty = (
            current is _ABSENT
            or current is None
            or (key == "build_ids" and current == [])
        )
        if not is_empty:
            continue
        merged[key] = fresh.get(key)
        if merged[key] != (None if current is _ABSENT else current):
            filled = True

    return merged, recovered, filled


def _refresh_one_run(
    manifest: Dict[str, Any],
    matched_task_results: List[Tuple[Any, Dict[str, Any]]],
    output_dir: Path,
    results_path: Path,
) -> None:
    """Repair one finalized run in place. Guards G2-G4; the write itself
    goes through `rewrite_finalized_results`, which enforces that the task
    set cannot change."""
    run_id = manifest["run_id"]
    context = manifest.get("context") or {}
    request_sets = {r.get("set") for r in manifest.get("requests", [])}
    is_single_set = len(request_sets) <= 1

    raw = read_raw_results_json(results_path)
    cached_entries = raw.get("results") or []
    old_verdict = raw.get("verdict")

    available = {
        task_result.request_uuid: (task_result, request_meta)
        for task_result, request_meta in matched_task_results
    }

    # G2 (completeness). Refreshing from a partial invocation would mean
    # either inventing content for the tasks this run did not harvest or
    # dropping them from the file; both are data loss, so the run is left
    # exactly as it was.
    missing = [e["task_id"] for e in cached_entries if e["task_id"] not in available]
    if missing:
        LOGGER.warning(
            "results cache: cannot refresh run %s: %d of %d cached task(s) "
            "not available in this invocation; run is unchanged",
            run_id,
            len(missing),
            len(cached_entries),
        )
        return

    merged_entries = []
    recovered_ids = []
    filled_count = 0
    for cached in cached_entries:
        task_id = cached["task_id"]
        task_result, request_meta = available[task_id]
        fresh = _build_task_entry(
            task_result,
            request_meta,
            context=context,
            is_single_set=is_single_set,
            run_id=run_id,
        )
        merged, recovered, filled = _merge_refreshed_entry(cached, fresh)
        # The merge happens on raw dicts and only then becomes a TaskEntry
        # (G5): from_dict turns an absent key into None, so merging after
        # the round-trip would "repair" a stale cache into a schema-current
        # and permanently empty one.
        merged_entries.append(TaskEntry.from_dict(merged))
        if recovered:
            recovered_ids.append(task_id)
        if filled:
            filled_count += 1

    new_verdict = rewrite_finalized_results(results_path, merged_entries)

    for task_id in recovered_ids:
        xunit_bytes = getattr(available[task_id][0], "xunit_bytes", None)
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

    LOGGER.info(
        "results cache: refreshed run %s: %d task(s) recovered, %d task(s) "
        "had metadata filled; root verdict %s -> %s",
        run_id,
        len(recovered_ids),
        filled_count,
        old_verdict,
        new_verdict,
    )


def _warn_on_empty_error_entries(results_path: Path, run_id: str) -> None:
    """Surface finalized ERROR + [] entries once per run.

    This used to be a per-task DEBUG line and nothing else, which is how a
    real run kept a PASSED task recorded as ERROR without anyone noticing.
    Some of these are genuine -- TF never published an xunit and never will
    -- so the WARNING recurs on every report of such a run. That is the
    ratified trade (Q11-1): visibility over silence.
    """
    try:
        raw = read_raw_results_json(results_path)
    except (ValidationError, OSError):
        return
    if raw.get("verdict") is None:
        # Still gap-fillable through the normal path; --refresh does not
        # apply to it, so advertising the flag would be wrong advice.
        return
    count = sum(
        1
        for entry in raw.get("results") or []
        if entry.get("verdict") == "ERROR" and entry.get("plans") == []
    )
    if not count:
        return
    LOGGER.warning(
        "results cache: run %s has %d task(s) with no test results recorded; "
        "if Testing Farm has since published them, recover with "
        "'enge report --run %s --refresh'",
        run_id,
        count,
        run_id,
    )


def _warn_on_stale_caches(manifests: List[Dict[str, Any]], output_dir: Path) -> None:
    """One WARNING for the whole invocation, never one per run: a user who
    selected thirty runs with an old cache wants a pointer to the fix, not
    thirty copies of it."""
    stale = [
        manifest["run_id"]
        for manifest in manifests
        if _has_stale_entries(output_dir / f"{manifest['run_id']}.json")
    ]
    if not stale:
        return
    LOGGER.warning(
        "results cache: %d of %d selected run(s) were cached by an older "
        "enge version (e.g. %s); refresh them with 'enge report "
        "<same selectors> --refresh'",
        len(stale),
        len(manifests),
        stale[0],
    )


def _has_stale_entries(results_path: Path) -> bool:
    """True when the file is a FINALIZED cache holding at least one entry
    that predates the current key set. Unfinalized files are excluded for
    the same reason as above: --refresh refuses them."""
    try:
        raw = read_raw_results_json(results_path)
    except (ValidationError, OSError):
        return False
    if raw.get("verdict") is None:
        return False
    return any(stale_task_keys(entry) for entry in raw.get("results") or [])


def _cache_one_run(
    manifest: Dict[str, Any],
    matched_task_results: List[Tuple[Any, Dict[str, Any]]],
    output_dir: Path,
    *,
    refresh: bool = False,
) -> None:
    run_id = manifest["run_id"]
    results_path = output_dir / f"{run_id}.json"
    context = manifest.get("context") or {}
    request_sets = {r.get("set") for r in manifest.get("requests", [])}
    is_single_set = len(request_sets) <= 1

    if refresh and _is_finalized(results_path):
        _refresh_one_run(manifest, matched_task_results, output_dir, results_path)
        return

    if not results_path.exists():
        # Root envelope keeps only things relevant to the whole run as a
        # single batch (maintainer principle, 2026-07-24). A multi-set
        # manifest has no single correct event/source/target -- even when
        # two sets happen to share an upgrade path, tier/arch can still
        # diverge between them, so the envelope never picks a set's value
        # to stand in for the whole run. Deliberately not gated on path
        # equality across sets; is_single_set is the only signal.
        if is_single_set:
            envelope_event = context.get("event") or None
            envelope_source = context.get("source") or None
            envelope_target = context.get("target") or None
        else:
            envelope_event = envelope_source = envelope_target = None
        try:
            init_results_json(
                run_id=run_id,
                created_at=manifest.get("created_at", ""),
                event=envelope_event,
                source=envelope_source,
                target=envelope_target,
                output_dir=output_dir,
            )
        except FileExistsError:
            pass  # raced with another invocation; gap-fill continues below

    for task_result, request_meta in matched_task_results:
        task_id = task_result.request_uuid
        entry_dict = _build_task_entry(
            task_result,
            request_meta,
            context=context,
            is_single_set=is_single_set,
            run_id=run_id,
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

    if not refresh:
        _warn_on_empty_error_entries(results_path, run_id)


def cache_report_results(
    ctx: "AppContext", task_results: List[Any], *, refresh: bool = False
) -> None:
    """Gap-fill results.json + xunit for every manifest matched by this
    report invocation. No-op for raw-input invocations. Never raises: a
    caching failure must not fail the report command
    (docs/results-json-schema.md write policy) -- the user's table must
    still render.

    `refresh=True` (`enge report --refresh`) additionally repairs every
    matched run whose cache is already finalized, under guards G2-G4 -- see
    `_refresh_one_run`. Runs whose cache is absent or unfinalized take the
    normal gap-fill path unchanged. The two advisory WARNINGs that point at
    the flag are suppressed under refresh: the user is already doing the
    thing they would ask for."""
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
            _cache_one_run(
                manifest, by_run.get(run_id, []), output_dir, refresh=refresh
            )
        except Exception:  # noqa: BLE001
            LOGGER.warning(
                "results cache: unexpected error caching run %s",
                run_id,
                exc_info=True,
            )

    if not refresh:
        _warn_on_stale_caches(manifests, output_dir)
