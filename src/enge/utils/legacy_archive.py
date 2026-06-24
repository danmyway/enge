# Deprecated: read-only bridge for pre-manifest archive files.
# Remove once pre-migration runs (~/.enge/jobs_archive/) are no longer needed.

import re
from datetime import datetime
from pathlib import Path
from typing import List, Optional

_ARCHIVE_TS_RE = re.compile(r"enge_jobs_archive_(\d{14})")


def read_task_ids(path: Path) -> List[str]:
    if not path.exists():
        return []
    return [line.strip() for line in path.read_text().splitlines() if line.strip()]


def _in_date_range(
    name: str, since: Optional[datetime], until: Optional[datetime]
) -> bool:
    m = _ARCHIVE_TS_RE.search(name)
    if not m:
        return True
    file_dt = datetime.strptime(m.group(1), "%Y%m%d%H%M%S")
    if since and file_dt < since:
        return False
    if until and file_dt > until:
        return False
    return True


def find_archive_files(
    archive_dir: Path,
    *,
    tag_patterns: Optional[List[str]] = None,
    since: Optional[datetime] = None,
    until: Optional[datetime] = None,
) -> List[Path]:
    if not archive_dir.exists():
        return []

    compiled = [re.compile(pat) for pat in (tag_patterns or [])]

    matched = []
    for entry in sorted(archive_dir.iterdir()):
        if not entry.is_file() or entry.name.endswith(".migrated"):
            continue
        name = entry.name

        if compiled:
            ext = name.split(".", 1)[-1] if "." in name else ""
            if not any(p.search(ext) or p.search(name) for p in compiled):
                continue

        if (since or until) and not _in_date_range(name, since, until):
            continue

        matched.append(entry)
    return matched


def extract_tags_from_filename(path: Path) -> List[str]:
    name = path.name
    if "." not in name:
        return []
    _, *tag_parts = name.split(".")
    return [part for part in tag_parts if part]
