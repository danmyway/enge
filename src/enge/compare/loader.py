"""I/O loading for `enge compare`: manifest resolution -> results.json
parsing -> missing-cache policy.

Data source is results.json caches ONLY (`parse_results_json`) -- no
xunit parsing, no TF API calls, no second verdict mapping (verdicts arrive
already as the uppercase schema enum). *Which* manifests/run_ids are in
scope is resolved via the shared `resolve_manifests_for_invocation` (never
re-forked -- see CLAUDE.md "Results.json format"); this module only reads
manifest `requests[]` afterwards for column-provenance metadata
(`artifacts_url`, joined on `task_id`) that results.json deliberately does
not store, mirroring the same join `report/results_cache.py` already
performs.

Missing-cache policy (fire-time ruling, no override): a matched run with
no results.json logs an ERROR naming the run and the exact fix command
(`enge report --run <run_id>`), then is skipped. Otherwise it returns the
usage/data error code (`ExitCode.CONFIG_ERROR` -- the documented
"universal floor" code for "this invocation cannot be serviced as given",
as opposed to the report-specific result-grading codes TEST_ERROR/
TEST_FAILURE/MISSING_RESULTS, which don't apply here since no grading has
happened yet).

Gating floor is mode-aware (AMENDMENT-1, 2026-07-21): one `enge dispatch`
produces ONE manifest fanned across N requests (e.g. one per arch), so a
single matched manifest can still carry multiple comparable columns.
Consolidation mode compares the SAME coordinate over time, so it still
requires >=2 matched MANIFESTS (`_MIN_CONSOLIDATION_MANIFESTS`) --
unchanged, zero behavioral difference from before this amendment.
Flakiness mode compares columns within one tier regardless of how many
manifests they came from, so it only requires >=1 comparable COLUMN
(`_MIN_FLAKINESS_COLUMNS`) -- the single-manifest/multi-arch `--run`
case this amendment fixes.
"""

import logging
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from enge.compare.engine import ExecutionColumn
from enge.utils.errors import ValidationError
from enge.utils.globals import ExitCode
from enge.utils.manifest_resolution import resolve_manifests_for_invocation
from enge.utils.results_parser import parse_results_json
from enge.utils.state_paths import results_dir

if TYPE_CHECKING:
    from enge.utils.app_context import AppContext

LOGGER = logging.getLogger(__name__)

_MIN_CONSOLIDATION_MANIFESTS = 2
_MIN_FLAKINESS_COLUMNS = 1


def load_columns(
    ctx: "AppContext", *, flakiness: bool = False
) -> Tuple[List[ExecutionColumn], Optional[ExitCode]]:
    """Resolve this invocation's manifests, load each one's results.json
    cache, and flatten every task entry into an ExecutionColumn.

    Returns (columns, None) on success, or ([], ExitCode.CONFIG_ERROR) if
    the mode-appropriate floor isn't met: fewer than 2 matched manifests
    (consolidation, `flakiness=False`) or zero comparable columns
    (flakiness, `flakiness=True`).
    """
    manifests = resolve_manifests_for_invocation(ctx)
    output_dir = results_dir(ctx.config)

    columns: List[ExecutionColumn] = []
    valid_runs = 0
    for manifest in manifests:
        run_id = manifest["run_id"]
        results_path = output_dir / f"{run_id}.json"
        if not results_path.exists():
            LOGGER.error(
                "compare: no results cache for run %s; generate it first "
                "with 'enge report --run %s'",
                run_id,
                run_id,
            )
            continue
        try:
            schema = parse_results_json(results_path)
        except ValidationError:
            LOGGER.error(
                "compare: results cache for run %s is corrupted; "
                "regenerate it with 'enge report --run %s'",
                run_id,
                run_id,
            )
            continue

        valid_runs += 1
        request_index: Dict[str, Dict[str, Any]] = {
            r["task_id"]: r for r in manifest.get("requests", []) if r.get("task_id")
        }
        for task in schema.results:
            request_meta = request_index.get(task.task_id, {})
            columns.append(
                ExecutionColumn(
                    task_id=task.task_id,
                    run_id=run_id,
                    set=task.set,
                    tier=task.tier,
                    arch=task.arch,
                    source=schema.source,
                    target=schema.target,
                    source_compose=task.source_compose,
                    target_compose=task.target_compose,
                    dispatched_at=task.dispatched_at,
                    run_created_at=schema.created_at,
                    plans=task.plans,
                    artifacts_url=request_meta.get("artifacts_url"),
                )
            )

    if flakiness:
        if len(columns) < _MIN_FLAKINESS_COLUMNS:
            return [], ExitCode.CONFIG_ERROR
    elif valid_runs < _MIN_CONSOLIDATION_MANIFESTS:
        return [], ExitCode.CONFIG_ERROR

    return columns, None
