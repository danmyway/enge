"""Shared task-ID resolution for report, rerun, cancel, and reportportal.

Resolves Testing Farm task UUIDs from CLI input, manifest store, or legacy
archive files.  Every consumer calls ``parse_tasks`` or ``parse_tasks_with_map``
— the heavy lifting lives in ``_parse_tasks_impl``.  The manifest-store leg
(``_resolve_manifest_tasks``) delegates run selection to
``manifest_resolution.select_runs`` — the same repeatable-``--run`` +
AND-filter selection ``enge report``/``compare`` use — then flattens the
selected runs' task_ids (de-duplicated, order preserved).  A selector
combination matching no runs raises ``ValidationError`` (exit 2).
"""

import logging
import os
import re
import uuid
from datetime import datetime
from pathlib import Path

from enge.utils import parse_date_arg
from enge.utils.errors import ValidationError
from enge.utils.manifest import ManifestReader

LOGGER = logging.getLogger(__name__)

_ARCHIVE_TS_RE = re.compile(r"enge_jobs_archive_(\d{14})")


def _file_in_date_range(
    filename: str,
    since: "datetime | None",
    until: "datetime | None",
) -> bool:
    """Check whether an archive file's timestamp falls within a date range.

    Extracts the ``YYYYMMDDHHMMSS`` timestamp embedded in the standard
    archive filename.  Files that don't match the naming convention are
    passed through unfiltered.
    """
    m = _ARCHIVE_TS_RE.search(filename)
    if not m:
        return True

    file_dt = datetime.strptime(m.group(1), "%Y%m%d%H%M%S")
    if since and file_dt < since:
        return False
    if until and file_dt > until:
        return False
    return True


def _latest_tasks_file(ctx):
    return ctx.archive_tasks_latest


def _resolve_manifest_tasks(ctx):
    """Resolve task IDs from the manifest store via the shared run selection.

    Delegates run/filter selection to ``manifest_resolution.select_runs`` --
    the same repeatable ``--run`` + AND-composed filter selection ``enge
    report``/``compare`` use -- then flattens the selected runs' task_ids
    (de-duplicated, order preserved).  Source lineage is preserved for a lone
    ``--run`` (``manifest:<run_id>``); multi-run or any filter collapses to
    the ``manifest:filter`` sentinel.  Returns None only when no selector at
    all is present; an empty selection with selectors raises ``ValidationError``.
    """
    from enge.utils.manifest_resolution import _as_list, select_runs

    cli_args = ctx.cli_args

    run_values = _as_list(getattr(cli_args, "run", None))
    has_filters = any(
        getattr(cli_args, attr, None)
        for attr in ("filter_set", "filter_tier", "filter_arch", "filter_tag")
    )
    has_date = bool(
        getattr(cli_args, "since", None) or getattr(cli_args, "until", None)
    )

    if not (run_values or has_filters or has_date):
        return None

    selected = select_runs(ctx)
    LOGGER.info(f"Selected run(s): {', '.join(m['run_id'] for m in selected)}")

    task_ids = []
    seen = set()
    for manifest in selected:
        for tid in ManifestReader.get_task_ids(manifest):
            if tid not in seen:
                seen.add(tid)
                task_ids.append(tid)

    single_run = (
        len(selected) == 1 and len(run_values) == 1 and not has_filters and not has_date
    )
    if single_run:
        source = f"manifest:{selected[0]['run_id']}"
    else:
        source = "manifest:filter"
    return task_ids, source


def _parse_tasks_impl(ctx):  # noqa: C901
    request_url_list = []
    uuid_source_map = {}
    cli_args = ctx.cli_args

    def _get_tasks_source_data():  # noqa: C901
        source = None
        source_data = []
        if getattr(cli_args, "input", None):
            LOGGER.debug("Getting tasks from command line input arguments")
            source_data.extend([(line, None) for line in cli_args.input])

        if cli_args.file:
            source = cli_args.file
            for file in source:
                if os.path.exists(file):
                    with open(file) as fh:
                        task_ids = fh.readlines()
                    source_data.extend([(line, file) for line in task_ids])
                else:
                    LOGGER.critical(f"Given path {cli_args.file} does not exist!")

                    raise ValidationError("Input file does not exist")

        since_str = getattr(cli_args, "since", None)
        until_str = getattr(cli_args, "until", None)
        has_tags = bool(cli_args.get_tag)
        if has_tags:
            LOGGER.warning(
                "--get-tag is deprecated; use --tag for native manifests. "
                "--get-tag only queries legacy pre-migration archives."
            )
        has_date_filter = bool(since_str or until_str)

        has_manifest_filters = any(
            getattr(cli_args, attr, None)
            for attr in (
                "filter_set",
                "filter_tier",
                "filter_arch",
                "filter_tag",
                "run",
            )
        )

        if (has_tags or has_date_filter) and not has_manifest_filters:
            default_path = ctx.archive_tasks_default
            if not os.path.exists(default_path):
                LOGGER.critical(f"The given path {default_path} does not exist!")

                raise ValidationError("Archive path does not exist")

            compiled_patterns = []
            if has_tags:
                for tag in cli_args.get_tag:
                    try:
                        compiled_patterns.append(re.compile(tag))
                    except re.error as e:
                        LOGGER.error(f"Invalid regex pattern '{tag}': {e}")

                        raise ValidationError("Invalid regex in --get-tag")

            source = []
            for file in os.listdir(default_path):
                if compiled_patterns:
                    file_extension = file.split(".", 1)[-1] if "." in file else ""
                    if not any(
                        pattern.search(file_extension) or pattern.search(file)
                        for pattern in compiled_patterns
                    ):
                        continue
                source.append(file)

            if has_date_filter:
                since_dt = parse_date_arg(since_str) if since_str else None
                until_dt = (
                    parse_date_arg(until_str).replace(hour=23, minute=59, second=59)
                    if until_str
                    else None
                )
                before = len(source)
                source = [
                    f for f in source if _file_in_date_range(f, since_dt, until_dt)
                ]
                if len(source) != before:
                    LOGGER.info(
                        f"Date filter narrowed {before} archive "
                        f"file(s) to {len(source)}"
                    )

            for file in source:
                file = os.path.join(default_path, file)
                with open(file) as fh:
                    task_ids = fh.readlines()
                source_data.extend([(line, file) for line in task_ids])

        if not any(
            (
                cli_args.file,
                getattr(cli_args, "input", None),
                has_tags,
                has_date_filter,
                has_manifest_filters,
            )
        ):
            manifest_latest = Path(ctx.manifest_latest)
            manifest_data = ManifestReader.load_latest(manifest_latest)
            if manifest_data:
                task_ids = ManifestReader.get_task_ids(manifest_data)
                source = f"manifest:{manifest_data.get('run_id', 'latest')}"
                source_data = [(tid, None) for tid in task_ids]
            else:
                latest = _latest_tasks_file(ctx)
                if os.path.exists(latest):
                    LOGGER.debug("Falling back to legacy latest file: %s", latest)
                    source = latest
                    with open(source) as fh:
                        source_data = [(line, source) for line in fh.readlines()]
                else:
                    LOGGER.critical(
                        "No manifest store or legacy latest file found. "
                        "Use --file, --input, or --run to specify tasks."
                    )
                    raise ValidationError("No task source available")

        use_manifest_resolve = has_manifest_filters or (
            has_date_filter and not has_tags
        )
        if use_manifest_resolve and not source_data:
            manifest_result = _resolve_manifest_tasks(ctx)
            if manifest_result:
                task_ids, manifest_source = manifest_result
                source = manifest_source
                source_data = [(tid, None) for tid in task_ids]

        return source, source_data

    tasks_source, tasks_source_data = _get_tasks_source_data()

    uuid_pattern = re.compile(
        r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
    )

    for task_line, source_file in tasks_source_data:
        task = task_line.strip().rstrip("/")
        if not task:
            continue

        match = uuid_pattern.search(task)
        if not match:
            LOGGER.debug(f"Cannot parse the UUID from {task}")
            continue

        matched_uuid = match.group(0)
        task_url = os.path.join(
            str(ctx.testing_farm_endpoint.api_endpoint_url), matched_uuid
        )

        task_id = None
        try:
            task_id = task_url.split("/")[-1]
            uuid.UUID(task_id)
        except ValueError:
            raise ValueError(task_id)

        request_url_list.append(task_url)
        uuid_source_map[task_url] = source_file
        uuid_source_map[matched_uuid] = source_file

    return request_url_list, tasks_source, uuid_source_map


def parse_tasks(ctx):
    """Parse task IDs from CLI input/files/archives."""
    req, src, _ = _parse_tasks_impl(ctx)
    return req, src


def parse_tasks_with_map(ctx):
    return _parse_tasks_impl(ctx)
