"""Shared invocation -> manifest(s) resolution.

Independently resolves which manifest(s) a CLI invocation is backed by,
given its parsed CLI args and manifest paths (via `AppContext`).
Precedence: file > input > tags/date-only-without-manifest-filters ->
legacy archive > no-selector -> latest manifest > run/filter selectors ->
`select_runs`. That last step -- resolving repeatable `--run` values and
AND-composing `--set`/`--tier`/`--arch`/`--tag`/`--since`/`--until` over
them -- is the shared `select_runs` in this module, which
`utils/task_resolver.py` now imports too (the long-planned unification of
the two resolvers partially landing here). They still differ in what they
return: this module hands back full manifest objects (and run_id-per-task
attribution across N matched runs), while `task_resolver` returns a flat
task_id list tuned for the xunit fetch layer.

Consumers: `report/results_cache.py` (`cache_report_results`'s
manifest-backed gap-fill); `enge compare` (`compare/loader.py`,
read-only).
"""

from datetime import timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from enge.utils import parse_date_arg
from enge.utils.manifest import ManifestReader

if TYPE_CHECKING:
    from enge.utils.app_context import AppContext


def resolve_manifests_for_invocation(ctx: "AppContext") -> List[Dict[str, Any]]:
    """Resolve which manifest(s) this invocation is backed by, as full
    manifest objects. Precedence: file > input >
    tags/date-only-without-manifest-filters -> legacy archive >
    no-selector -> latest manifest > run/filter selectors (delegated to
    `select_runs`).

    Returns [] for raw-input and legacy-archive invocations (the caller
    no-ops in that case). Raises `ValidationError` when a run/filter
    selector combination matches no runs -- see `select_runs`."""
    cli_args = ctx.cli_args

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

    # Run/filter selector path -- repeatable --run, AND-composed filters.
    return select_runs(ctx)


def _as_list(val: Any) -> List[str]:
    """Normalize a selector value (None / str / list) to a list, dropping
    empty strings. Keeps `select_runs` tolerant of callers that still pass a
    bare string (e.g. existing resolve_manifests tests)."""
    if val is None:
        return []
    if isinstance(val, str):
        return [val] if val else []
    return [v for v in val if v]


def _build_find_kwargs(
    filter_set: List[str],
    filter_tier: List[str],
    filter_arch: List[str],
    filter_tag: List[str],
    since_str: Optional[str],
    until_str: Optional[str],
) -> Dict[str, Any]:
    """Build `ManifestReader.find_runs` kwargs from the non-run selectors,
    normalizing --since/--until to tz-aware UTC bounds (until -> end of day)."""
    kwargs: Dict[str, Any] = {}
    if filter_set:
        kwargs["set_name"] = filter_set
    if filter_tier:
        kwargs["tier"] = filter_tier
    if filter_arch:
        kwargs["arch"] = filter_arch
    if filter_tag:
        kwargs["tag"] = filter_tag
    if since_str:
        dt = parse_date_arg(since_str)
        kwargs["since"] = dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
    if until_str:
        dt = parse_date_arg(until_str)
        dt = dt.replace(hour=23, minute=59, second=59)
        kwargs["until"] = dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
    return kwargs


def _resolve_by_run(
    runs_dir: Path, run_values: List[str], kwargs: Dict[str, Any]
) -> List[Dict[str, Any]]:
    """Resolve each --run value (exact then unique-prefix; `ValidationError`
    per value on ambiguity or no-match), de-duplicated by run_id with
    first-seen order preserved, then AND-intersect with the find_runs
    predicate when any other selector is present."""
    resolved: List[Dict[str, Any]] = []
    seen: set = set()
    for value in run_values:
        manifest = ManifestReader.get_run(runs_dir, value)
        rid = manifest["run_id"]
        if rid not in seen:
            seen.add(rid)
            resolved.append(manifest)
    if kwargs:
        allowed = {s["run_id"] for s in ManifestReader.find_runs(runs_dir, **kwargs)}
        resolved = [m for m in resolved if m["run_id"] in allowed]
    return resolved


def _selector_error_parts(
    run_values: List[str],
    filter_set: List[str],
    filter_tier: List[str],
    filter_arch: List[str],
    filter_tag: List[str],
    since_str: Optional[str],
    until_str: Optional[str],
) -> List[str]:
    """Render every provided selector value as a `--flag value` token for the
    empty-selection error message."""
    parts: List[str] = []
    parts += [f"--run {v}" for v in run_values]
    parts += [f"--set {v}" for v in filter_set]
    parts += [f"--tier {v}" for v in filter_tier]
    parts += [f"--arch {v}" for v in filter_arch]
    parts += [f"--tag {v}" for v in filter_tag]
    if since_str:
        parts.append(f"--since {since_str}")
    if until_str:
        parts.append(f"--until {until_str}")
    return parts


def select_runs(ctx: "AppContext") -> List[Dict[str, Any]]:
    """Resolve the run/filter selector path to a list of full manifest
    objects, shared by `resolve_manifests_for_invocation` and
    `task_resolver`. Repeatable `--run` values resolve via
    `ManifestReader.get_run` (exact then unique-prefix; `ValidationError` on
    ambiguity or no-match, per value), de-duplicated by run_id with
    first-seen order preserved. Any other provided selector
    (`--set`/`--tier`/`--arch`/`--tag`/`--since`/`--until`) is then
    AND-applied over that set with the same matching `find_runs` uses. With
    no `--run`, selection is `find_runs` alone. A final empty selection with
    at least one selector provided raises `ValidationError` naming every
    selector value -- never a silent []."""
    from enge.utils.errors import ValidationError

    cli_args = ctx.cli_args
    runs_dir = Path(ctx.manifest_runs_dir)

    run_values = _as_list(getattr(cli_args, "run", None))
    filter_set = _as_list(getattr(cli_args, "filter_set", None))
    filter_tier = _as_list(getattr(cli_args, "filter_tier", None))
    filter_arch = _as_list(getattr(cli_args, "filter_arch", None))
    filter_tag = _as_list(getattr(cli_args, "filter_tag", None))
    since_str = getattr(cli_args, "since", None)
    until_str = getattr(cli_args, "until", None)

    has_manifest_filters = any((filter_set, filter_tier, filter_arch, filter_tag))
    kwargs = _build_find_kwargs(
        filter_set, filter_tier, filter_arch, filter_tag, since_str, until_str
    )

    if run_values:
        result = _resolve_by_run(runs_dir, run_values, kwargs)
    else:
        summaries = ManifestReader.find_runs(runs_dir, **kwargs)
        result = [ManifestReader.load(Path(s["path"])) for s in summaries]

    if not result and (run_values or has_manifest_filters):
        parts = _selector_error_parts(
            run_values,
            filter_set,
            filter_tier,
            filter_arch,
            filter_tag,
            since_str,
            until_str,
        )
        raise ValidationError("selectors matched no runs: " + ", ".join(parts))

    return result
