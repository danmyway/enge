import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

from enge.utils.app_context import AppContext
from enge.utils.legacy_archive import extract_tags_from_filename, read_task_ids
from enge.utils.ulid import generate_ulid

LOGGER = logging.getLogger(__name__)

_ARCHIVE_TS_RE = re.compile(r"enge_jobs_archive_(\d{14})")

_KNOWN_ARCHS = {"x86_64", "aarch64", "ppc64le", "s390x"}
_KNOWN_TIER_RE = re.compile(r"^tier\d+(?:only)?$")


def _parse_context_from_tags(tags):
    context = {}
    set_name = None
    tier = None
    arch = None

    for tag in tags:
        if tag in _KNOWN_ARCHS:
            arch = tag
        elif _KNOWN_TIER_RE.match(tag):
            tier = tag
        elif tag.startswith("rerun") or tag == "migrated":
            continue
        elif not set_name:
            set_name = tag

    if set_name:
        context["set"] = set_name
    if tier:
        context["tiers"] = [tier]
    if arch:
        context["architectures"] = [arch]
    return context


def main(ctx: AppContext) -> int:
    archive_dir = Path(ctx.archive_tasks_default)
    runs_dir = Path(ctx.manifest_runs_dir)
    log_base = str(ctx.testing_farm_endpoint.log_artifact_baseurl)

    if not archive_dir.exists():
        LOGGER.info(
            f"Legacy archive directory {archive_dir} does not exist. Nothing to migrate."
        )
        return 0

    migrated = 0
    skipped = 0

    for entry in sorted(archive_dir.iterdir()):
        if not entry.is_file():
            continue
        if entry.name.endswith(".migrated"):
            continue

        touchfile = entry.parent / f"{entry.name}.migrated"
        if touchfile.exists():
            skipped += 1
            continue

        task_ids = read_task_ids(entry)
        if not task_ids:
            LOGGER.debug(f"Skipping empty file: {entry.name}")
            skipped += 1
            continue

        ts_match = _ARCHIVE_TS_RE.search(entry.name)
        if ts_match:
            created_at = (
                datetime.strptime(ts_match.group(1), "%Y%m%d%H%M%S")
                .replace(tzinfo=timezone.utc)
                .strftime("%Y-%m-%dT%H:%M:%SZ")
            )
        else:
            created_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        tags = extract_tags_from_filename(entry)
        context = _parse_context_from_tags(tags)

        run_id = generate_ulid()
        manifest = {
            "schema_version": 1,
            "run_id": run_id,
            "created_at": created_at,
            "command": "test",
            "argv": [],
            "tags": tags,
            "parent_run_id": None,
            "origin": "migrated",
            "context": context,
            "requests": [],
        }

        for task_id in task_ids:
            task_id = task_id.strip()
            artifacts_url = f"{log_base.rstrip('/')}/{task_id}" if log_base else None
            manifest["requests"].append(
                {
                    "task_id": task_id,
                    "set": context.get("set"),
                    "tier": (
                        context.get("tiers", [None])[0]
                        if context.get("tiers")
                        else None
                    ),
                    "arch": (
                        context.get("architectures", [None])[0]
                        if context.get("architectures")
                        else None
                    ),
                    "plan": None,
                    "source_compose": None,
                    "target_compose": None,
                    "artifacts_url": artifacts_url,
                    "dispatched_at": created_at,
                }
            )

        runs_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = runs_dir / f"{run_id}.json"
        manifest_path.write_text(json.dumps(manifest, indent=2))

        touchfile.touch()
        migrated += 1
        LOGGER.info(f"Migrated {entry.name} → {run_id}.json ({len(task_ids)} task(s))")

    LOGGER.info(f"Migration complete: {migrated} migrated, {skipped} skipped.")
    return 0
