"""Shared invocation -> manifest(s) resolution.

Independently resolves which manifest(s) a CLI invocation is backed by,
given its parsed CLI args and manifest paths (via `AppContext`). Mirrors
`utils/task_resolver.py`'s precedence (file > input >
tags/date-only-without-manifest-filters -> legacy archive > no-args ->
latest manifest > --run / filter selectors -> manifest store) closely
enough to agree with it on manifest-backed vs. raw-input, but is not
imported from it: `task_resolver` hands back a flat task_id list tuned
for the xunit fetch layer, not the full manifest objects (and
run_id-per-task attribution across N matched runs) this resolver's
consumers need.

Consumers: `report/results_cache.py` (`cache_report_results`'s
manifest-backed gap-fill); the planned `enge compare` subcommand. A
future unification of this resolver with `task_resolver` is a separate,
maintainer-approved concern.
"""

from datetime import timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List

from enge.utils import parse_date_arg
from enge.utils.manifest import ManifestReader

if TYPE_CHECKING:
    from enge.utils.app_context import AppContext


def resolve_manifests_for_invocation(ctx: "AppContext") -> List[Dict[str, Any]]:
    """Independently resolve which manifest(s) this report invocation is
    backed by. Mirrors utils/task_resolver.py's precedence (file > input >
    tags/date-only-without-manifest-filters -> legacy archive > no-args ->
    latest manifest > --run / filter selectors -> manifest store) closely
    enough to agree with it on manifest-backed vs. raw-input, but is not
    imported from it: task_resolver hands back a flat task_id list tuned
    for the xunit fetch layer, not the full manifest objects (and
    run_id-per-task attribution across N matched runs) this module needs.

    Returns [] for raw-input invocations -- the caller no-ops in that
    case."""
    cli_args = ctx.cli_args
    runs_dir = Path(ctx.manifest_runs_dir)

    if getattr(cli_args, "file", None):
        return []
    if getattr(cli_args, "input", None):
        return []

    since_str = getattr(cli_args, "since", None)
    until_str = getattr(cli_args, "until", None)
    has_tags = bool(getattr(cli_args, "get_tag", None))
    has_date_filter = bool(since_str or until_str)
    has_manifest_filters = any(
        getattr(cli_args, attr, None)
        for attr in ("filter_set", "filter_tier", "filter_arch", "filter_tag", "run")
    )

    if (has_tags or has_date_filter) and not has_manifest_filters:
        # Legacy archive path (--get-tag, or bare --since/--until with no
        # manifest filters) -- no resolvable run_id.
        return []

    if not any((has_tags, has_date_filter, has_manifest_filters)):
        # No selector at all: the latest manifest, if one exists. (If not,
        # task_resolver falls back to the legacy latest-jobs file, which
        # is also not manifest-backed -- nothing to cache either way.)
        manifest_data = ManifestReader.load_latest(Path(ctx.manifest_latest))
        return [manifest_data] if manifest_data else []

    run_id = getattr(cli_args, "run", None)
    if run_id:
        return [ManifestReader.get_run(runs_dir, run_id)]

    kwargs: Dict[str, Any] = {}
    if getattr(cli_args, "filter_set", None):
        kwargs["set_name"] = cli_args.filter_set
    if getattr(cli_args, "filter_tier", None):
        kwargs["tier"] = cli_args.filter_tier
    if getattr(cli_args, "filter_arch", None):
        kwargs["arch"] = cli_args.filter_arch
    if getattr(cli_args, "filter_tag", None):
        kwargs["tag"] = cli_args.filter_tag
    if since_str:
        dt = parse_date_arg(since_str)
        kwargs["since"] = dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
    if until_str:
        dt = parse_date_arg(until_str)
        dt = dt.replace(hour=23, minute=59, second=59)
        kwargs["until"] = dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt

    summaries = ManifestReader.find_runs(runs_dir, **kwargs)
    return [ManifestReader.load(Path(summary["path"])) for summary in summaries]
