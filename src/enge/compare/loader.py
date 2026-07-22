"""I/O loading for `enge compare`: manifest resolution -> results.json
parsing -> unified floor policy -> per-task descriptor sourcing.

Data source is results.json caches ONLY (`parse_results_json`) -- no
xunit parsing, no TF API calls, no second verdict mapping (verdicts arrive
already as the uppercase schema enum). *Which* manifests/run_ids are in
scope is resolved via the shared `resolve_manifests_for_invocation` (never
re-forked -- see CLAUDE.md "Results.json format"); this module only reads
manifest `requests[]` afterwards for column-provenance metadata
(`artifacts_url`, joined on `task_id`) that results.json deliberately does
not store, mirroring the same join `report/results_cache.py` already
performs.

Missing-cache policy (fire-time ruling, unchanged by the compare-redesign
branch): a matched run with no results.json logs an ERROR naming the run
and the exact fix command (`enge report --run <run_id>`), then is
skipped.

Unified floor (C1): a single floor of >=1 comparable column, else the
usage/data error code (`ExitCode.CONFIG_ERROR` -- the documented
"universal floor" code for "this invocation cannot be serviced as given",
as opposed to the report-specific result-grading codes TEST_ERROR/
TEST_FAILURE/MISSING_RESULTS, which don't apply here since no grading has
happened yet). The old mode-aware split (consolidation's >=2-matched-
manifest floor vs flakiness's >=1-column floor) is gone entirely along
with the two-mode split itself -- one `enge dispatch` producing one
manifest fanned across N requests (e.g. one per arch) is now always
sufficient on its own, regardless of table partitioning.

Descriptor sourcing (item 7, the read-side half of the M4 multi-set
descriptor fix): `ExecutionColumn.source`/`.target` come from the
PER-TASK `TaskEntry.source`/`.target` fields (dispatch-context-schema).
Fallback to the run envelope's `source`/`target` applies ONLY when the
per-task value is None AND the manifest is single-set -- mirroring
`report/results_cache.py`'s own `is_single_set` harvest-time fallback
rule exactly (`len({r.get("set") for r in manifest["requests"]}) <= 1`).
A multi-set manifest with no per-task value gets no fallback and stays
None; `enge.compare.__main__` renders a None descriptor as the em dash,
never the literal string "None" -- the em dash is a render-time
substitution, never a stored value.
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

_MIN_COLUMNS = 1


def load_columns(
    ctx: "AppContext",
) -> Tuple[List[ExecutionColumn], Optional[ExitCode]]:
    """Resolve this invocation's manifests, load each one's results.json
    cache, and flatten every task entry into an ExecutionColumn.

    Returns (columns, None) on success, or ([], ExitCode.CONFIG_ERROR) if
    fewer than 1 comparable column is available.
    """
    manifests = resolve_manifests_for_invocation(ctx)
    output_dir = results_dir(ctx.config)

    columns: List[ExecutionColumn] = []
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

        request_index: Dict[str, Dict[str, Any]] = {
            r["task_id"]: r for r in manifest.get("requests", []) if r.get("task_id")
        }
        request_sets = {r.get("set") for r in manifest.get("requests", [])}
        is_single_set = len(request_sets) <= 1

        for task in schema.results:
            request_meta = request_index.get(task.task_id, {})
            source = (
                task.source
                if task.source is not None
                else (schema.source if is_single_set else None)
            )
            target = (
                task.target
                if task.target is not None
                else (schema.target if is_single_set else None)
            )
            columns.append(
                ExecutionColumn(
                    task_id=task.task_id,
                    run_id=run_id,
                    set=task.set,
                    tier=task.tier,
                    arch=task.arch,
                    source=source,
                    target=target,
                    source_compose=task.source_compose,
                    target_compose=task.target_compose,
                    dispatched_at=task.dispatched_at,
                    run_created_at=schema.created_at,
                    plans=task.plans,
                    artifacts_url=request_meta.get("artifacts_url"),
                )
            )

    if len(columns) < _MIN_COLUMNS:
        return [], ExitCode.CONFIG_ERROR

    return columns, None
