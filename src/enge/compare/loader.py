"""I/O loading for `enge compare`: manifest resolution -> results.json
parsing -> unified floor policy -> per-task descriptor sourcing.

Manifest-schema independence (fix/compare-manifest-decoupling): a manifest
is consulted for run *selection* only, via the shared
`resolve_manifests_for_invocation` (never re-forked -- see
docs/results-json-schema.md); once a manifest is matched, the only field this
module reads from it is `run_id`. ALL column data -- `artifacts_url`,
`source`/`target`, `set`/`tier`/`arch`, plans -- comes from that run's
results.json cache (`parse_results_json`) exclusively. No xunit parsing,
no TF API calls, no second verdict mapping (verdicts arrive already as the
uppercase schema enum), and no reads of manifest `requests[]`. Manifests
are immutable and span every schema generation ever written; coupling
column data to their shape permanently couples `enge compare` to that
history. results.json is a derived cache the harvest can improve instead.

Layered `artifacts_url` sourcing (item 1): prefer the value stored on the
`TaskEntry` (verbatim historical truth); only when that is falsy (missing
or an empty string -- a legacy cache predating the field, or predating a
harvest that populated it) is a URL constructed from
`ctx.testing_farm_endpoint.log_artifact_baseurl` + `task_id`, mirroring
how dispatch derives the same URL at request time
(`dispatch/tf_send_request.py`).

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
descriptor fix; item 2(b) re-sources its gate): `ExecutionColumn.source`/
`.target` come from the PER-TASK `TaskEntry.source`/`.target` fields
(dispatch-context-schema). Fallback to the run envelope's `source`/
`target` applies ONLY when the per-task value is None AND the results.json
cache is single-set -- `len({t.set for t in schema.results}) <= 1`,
re-sourced from the cache itself rather than the manifest's `requests[]`
(which is how `report/results_cache.py`'s harvest-time `is_single_set`
rule is still expressed, since it is writing that same cache). A
multi-set cache with no per-task value gets no fallback and stays None;
`enge.compare.__main__` renders a None descriptor as the em dash, never
the literal string "None" -- the em dash is a render-time substitution,
never a stored value.

Ratified sunset (RULING D-2, 2026-07-29): this envelope fallback and its
single-set gate are legacy-cache support for results.json written before
per-task `source`/`target` existed, and are slated for DELETION. The
precondition is met -- the schema-staleness-warning + `--refresh` work
(ledgered as F7) has shipped, giving a stale cache a corrective path
(`enge report --run <run_id> --refresh`), and `_warn_on_stale_caches`
below points selected-but-stale runs at it. The deletion is queued as
its own PR: removing the fallback changes what an existing unrefreshed
cache renders, which is a separate user-visible behaviour change and
deserves its own review.

Staleness warning (F7): one aggregated WARNING per invocation when any
selected run's cache is finalized and missing keys the current writer
emits. Compare only reports it -- repairing is the report side's job.

Lineage mode (item 5.2, `load_lineage_columns`, ruling Q-L5-1): selection
is unchanged, but lineages are discovered by walking every cache under
`results_dir` via `rerun_of`. Both modes share one task -> column mapping
(`_columns_from_cache`), so a column means the same thing in either.
"""

import logging
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Set, Tuple, cast

from enge.compare.engine import ExecutionColumn
from enge.lineage import LineageIndex, LineageTask
from enge.utils.errors import ValidationError
from enge.utils.globals import ExitCode
from enge.utils.manifest_resolution import resolve_manifests_for_invocation
from enge.utils.results_parser import (
    ResultsJsonSchema,
    read_raw_results_json,
    stale_task_keys,
)
from enge.utils.state_paths import results_dir

if TYPE_CHECKING:
    from pathlib import Path

    from enge.utils.app_context import AppContext

LOGGER = logging.getLogger(__name__)

_MIN_COLUMNS = 1


def _warn_on_stale_caches(stale_run_ids: List[str], selected: int) -> None:
    """One WARNING for the whole invocation, never one per run.

    Compare cannot repair a stale cache -- it is a read-only consumer of
    results.json -- but staying silent about one leaves the user comparing
    columns whose blanks are an artifact of the cache's age rather than of
    the run. Point at the report-side repair and stop there.

    The staleness predicate is deliberately restated here rather than
    imported from `report/results_cache.py`: compare never depends on the
    writer. See tests/test_compare_loader.py and tests/test_results_refresh.py
    for the two halves of the same rule.
    """
    if not stale_run_ids:
        return
    LOGGER.warning(
        "compare: %d of %d selected run(s) were cached by an older enge "
        "version (e.g. %s); refresh them with 'enge report "
        "<same selectors> --refresh'",
        len(stale_run_ids),
        selected,
        stale_run_ids[0],
    )


def _is_stale(raw: Dict[str, Any]) -> bool:
    """A finalized cache missing keys the current writer emits.

    Unfinalized caches are excluded: `--refresh` refuses them (their
    gap-fill path is still live), so advertising the flag for one would be
    wrong advice.
    """
    return raw.get("verdict") is not None and any(
        stale_task_keys(entry) for entry in raw.get("results") or []
    )


def _columns_from_cache(
    ctx: "AppContext", run_id: str, schema: ResultsJsonSchema
) -> List[ExecutionColumn]:
    """One ExecutionColumn per task entry of one results.json cache.

    The single task -> column mapping shared by normal and lineage mode.
    """
    is_single_set = len({t.set for t in schema.results}) <= 1

    columns = []
    for task in schema.results:
        artifacts_url = (
            task.artifacts_url
            if task.artifacts_url
            else f"{ctx.testing_farm_endpoint.log_artifact_baseurl}/{task.task_id}"
        )
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
                artifacts_url=artifacts_url,
                rerun_of=task.rerun_of,
                verdict=task.verdict,
                plan=task.plan,
                tests=task.tests,
            )
        )
    return columns


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
    stale_run_ids: List[str] = []
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
            # The raw dict is read alongside the parsed schema, not instead
            # of it: `TaskEntry.from_dict` reads every optional key through
            # `.get()`, so by the time a key reaches `schema` an absent one
            # is indistinguishable from an explicit null. Staleness can only
            # be seen before that.
            raw = read_raw_results_json(results_path)
            schema = ResultsJsonSchema.from_dict(raw)
        except ValidationError:
            LOGGER.error(
                "compare: results cache for run %s is corrupted; "
                "regenerate it with 'enge report --run %s'",
                run_id,
                run_id,
            )
            continue

        if _is_stale(raw):
            stale_run_ids.append(run_id)

        columns.extend(_columns_from_cache(ctx, run_id, schema))

    # Emitted before the comparability floor: a run whose cache is too old
    # to yield a column is exactly the case the user most needs told.
    _warn_on_stale_caches(stale_run_ids, len(manifests))

    if len(columns) < _MIN_COLUMNS:
        return [], ExitCode.CONFIG_ERROR

    return columns, None


def _log_missing_or_corrupted(results_path: "Path", run_id: str) -> None:
    """The same ERRORs `load_columns` logs for a selected run."""
    if not results_path.exists():
        LOGGER.error(
            "compare: no results cache for run %s; generate it first "
            "with 'enge report --run %s'",
            run_id,
            run_id,
        )
    else:
        LOGGER.error(
            "compare: results cache for run %s is corrupted; "
            "regenerate it with 'enge report --run %s'",
            run_id,
            run_id,
        )


_Caches = Dict[str, Tuple[Dict[str, Any], ResultsJsonSchema]]


def _scan_caches(output_dir: "Path") -> Tuple[_Caches, List[str]]:
    """Every readable cache under `output_dir` keyed by run id, in
    file-name order, plus the file names that failed to read or validate."""
    caches: _Caches = {}
    unreadable: List[str] = []
    for path in sorted(output_dir.glob("*.json")):
        try:
            raw = read_raw_results_json(path)
            caches[path.stem] = (raw, ResultsJsonSchema.from_dict(raw))
        except (OSError, ValueError, ValidationError):
            unreadable.append(path.name)
    return caches, unreadable


def _selected_runs(
    manifests: List[Dict[str, Any]],
    caches: _Caches,
    unreadable: List[str],
    output_dir: "Path",
) -> List[str]:
    """Selected run ids with a usable cache. A missing or corrupted one gets
    `load_columns`' ERROR and leaves `unreadable` -- it is reported once."""
    selected_runs = []
    for manifest in manifests:
        run_id = manifest["run_id"]
        if run_id in caches:
            selected_runs.append(run_id)
            continue
        results_path = output_dir / f"{run_id}.json"
        _log_missing_or_corrupted(results_path, run_id)
        if results_path.name in unreadable:
            unreadable.remove(results_path.name)
    return selected_runs


def _first_occurrence_columns(
    ctx: "AppContext", caches: _Caches
) -> Dict[str, ExecutionColumn]:
    """Columns by task id; a task id cached twice keeps its first file."""
    columns: Dict[str, ExecutionColumn] = {}
    duplicates: List[str] = []
    for run_id, (_, schema) in caches.items():
        for column in _columns_from_cache(ctx, run_id, schema):
            if column.task_id in columns:
                if column.task_id not in duplicates:
                    duplicates.append(column.task_id)
                continue
            columns[column.task_id] = column
    if duplicates:
        LOGGER.warning(
            "compare: %d task id(s) appear in more than one results cache "
            "(e.g. %s); using the first cache in file-name order",
            len(duplicates),
            duplicates[0],
        )
    return columns


def load_lineage_columns(
    ctx: "AppContext",
) -> Tuple[List[ExecutionColumn], List[str], Optional[ExitCode]]:
    """Columns for `enge compare --lineage` (contract v3 §5a, Q-L5-1).

    Manifests select runs (their `run_id` only, as in normal mode); every
    task of each selected run's cache is a selected task. Lineages are then
    discovered by walking EVERY cache under `results_dir` via `rerun_of`, so
    a rerun's run resolves to the same lineages as its root's run.

    Returns (closure columns, selected task ids, None), where the closure is
    every task of every selected task's lineage, or ([], [],
    ExitCode.CONFIG_ERROR) when no selected task survives. Each WARNING the
    directory walk can raise (unreadable caches, duplicate task ids, orphan
    reruns, stale caches) is aggregated into one line per invocation.
    """
    manifests = resolve_manifests_for_invocation(ctx)
    output_dir = results_dir(ctx.config)

    caches, unreadable = _scan_caches(output_dir)
    selected_runs = _selected_runs(manifests, caches, unreadable, output_dir)
    if unreadable:
        LOGGER.warning(
            "compare: skipped %d unreadable results cache(s) while resolving "
            "lineages (e.g. %s)",
            len(unreadable),
            unreadable[0],
        )

    columns = _first_occurrence_columns(ctx, caches)

    selected = list(
        dict.fromkeys(
            task.task_id
            for run_id in selected_runs
            for task in caches[run_id][1].results
        )
    )
    if not selected:
        return [], [], ExitCode.CONFIG_ERROR

    index = LineageIndex(cast(List[LineageTask], list(columns.values())))
    closure: Set[str] = set()
    for task_id in selected:
        closure.update(t.task_id for t in index.lineage(index.root_of(task_id)))

    orphans = [task_id for task_id in index.orphans if task_id in closure]
    if orphans:
        LOGGER.warning(
            "compare: %d rerun task(s) have no cached parent and are shown as "
            "their own lineage root (e.g. %s)",
            len(orphans),
            orphans[0],
        )

    contributing = sorted({columns[task_id].run_id for task_id in closure})
    _warn_on_stale_caches(
        [run_id for run_id in contributing if _is_stale(caches[run_id][0])],
        len(contributing),
    )

    return [col for col in columns.values() if col.task_id in closure], selected, None
