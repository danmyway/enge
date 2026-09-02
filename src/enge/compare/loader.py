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
per-task `source`/`target` existed. They are slated for DELETION when the
schema-staleness-warning + `--refresh` work ships (ledgered as F7) --
that work gives a stale/incomplete cache a corrective path, removing the
need to paper over it here.
"""

import logging
from typing import TYPE_CHECKING, List, Optional, Tuple

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

    if not manifests:
        # F2-f: compare has no legacy-archive path, so bare --since/--until
        # with no manifest selector resolve to [] (not the empty-selection
        # ValidationError, which fires only when a manifest selector matched
        # nothing). Warn once, then fall through to the comparability floor.
        cli_args = ctx.cli_args
        has_date = bool(
            getattr(cli_args, "since", None) or getattr(cli_args, "until", None)
        )
        has_manifest_selector = any(
            getattr(cli_args, attr, None)
            for attr in (
                "run",
                "filter_set",
                "filter_tier",
                "filter_arch",
                "filter_tag",
            )
        )
        if has_date and not has_manifest_selector:
            LOGGER.warning(
                "compare: --since/--until with no manifest selector "
                "(--run/--set/--tier/--arch/--tag) select no runs; pair them "
                "with a manifest selector to compare."
            )

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

        is_single_set = len({t.set for t in schema.results}) <= 1

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
                )
            )

    if len(columns) < _MIN_COLUMNS:
        return [], ExitCode.CONFIG_ERROR

    return columns, None
