#!/usr/bin/env python3
"""
Standalone utilities for the ReportPortal module.

Contains pure functions, constants, and dataclasses that have no dependency
on the ReportPortalLaunch class instance.
"""

import logging
import os
import re
from typing import Optional, Dict, Any, List, Tuple
from datetime import datetime
from dataclasses import dataclass

import lxml.etree
from rich.markup import escape

LOGGER = logging.getLogger(__name__)

# Default max artifact file size for enrichment (5 MB)
DEFAULT_ENRICH_MAX_FILE_SIZE = 5 * 1024 * 1024

# File extensions that are likely binary and should be skipped
BINARY_EXTENSIONS = frozenset(
    {
        ".core",
        ".dump",
        ".gz",
        ".bz2",
        ".xz",
        ".zst",
        ".tar",
        ".zip",
        ".rpm",
        ".img",
        ".iso",
        ".bin",
        ".o",
        ".so",
        ".pyc",
    }
)


def parse_size_string(size_str: str) -> int:
    """Parse a human-readable size string (e.g., '5 MB', '500 kB') into bytes."""
    size_str = size_str.strip()
    match = re.match(r"(\d+(?:\.\d+)?)\s*(B|kB|KB|MB|GB|TB)", size_str, re.IGNORECASE)
    if not match:
        LOGGER.warning(f"Could not parse size string '{size_str}', using default")
        return DEFAULT_ENRICH_MAX_FILE_SIZE
    value = float(match.group(1))
    unit = match.group(2).upper()
    multipliers = {
        "B": 1,
        "KB": 1024,
        "MB": 1024**2,
        "GB": 1024**3,
        "TB": 1024**4,
    }
    return int(value * multipliers.get(unit, 1))


@dataclass
class ArtifactFile:
    """Represents a single artifact log discovered from results.xml."""

    name: str
    url: str
    relative_path: str
    parent_type: str = ""
    parent_name: str = ""


# ===================================================================
# Artifact log-level mapping and skip list
# ===================================================================

# Maps RP log level → artifact names uploaded at that level.
# Anything not listed uses DEFAULT_ARTIFACT_LOG_LEVEL.
ARTIFACT_LOG_LEVELS: Dict[str, tuple] = {
    "ERROR": (
        "tmt-verbose-log",
        "testout.log",
        "test_debug.log" "leapp-preupgrade.log" "leapp.out",
        "leapp-report.txt",
        "leapp-report.json",
    ),
    "WARN": ("tmt-log"),
}

DEFAULT_ARTIFACT_LOG_LEVEL = "INFO"

# Artifacts listed here are silently dropped and never uploaded.
SKIP_ARTIFACT_LOGS: set = {"tmt-reproducer", "failures.yaml"}

# Invert ARTIFACT_LOG_LEVELS for fast name → level lookup
_ARTIFACT_LEVEL_LOOKUP: Dict[str, str] = {
    name: level for level, names in ARTIFACT_LOG_LEVELS.items() for name in names
}


def _match_artifact_name(name: str, candidates) -> Optional[str]:
    """
    Match an artifact name against a collection of known names.

    Extracts the basename first (in case the name contains a path),
    then tries exact match, then strips a leading numeric prefix
    (e.g. ``01.``, ``03.``) and retries.  Returns the matched key
    or ``None``.
    """
    basename = os.path.basename(name)
    if basename in candidates:
        return basename
    # Strip leading "NN." prefix (e.g. 01.leapp-report.txt)
    stripped = re.sub(r"^\d+\.", "", basename)
    if stripped != basename and stripped in candidates:
        return stripped
    return None


def get_artifact_log_level(name: str) -> str:
    """Return the RP log level for a given artifact name."""
    matched = _match_artifact_name(name, _ARTIFACT_LEVEL_LOOKUP)
    if matched:
        return _ARTIFACT_LEVEL_LOOKUP[matched]
    return DEFAULT_ARTIFACT_LOG_LEVEL


def should_skip_artifact(name: str) -> bool:
    """Return True if the artifact should be excluded from upload."""
    return _match_artifact_name(name, SKIP_ARTIFACT_LOGS) is not None


# ===================================================================
# Timestamp parsing
# ===================================================================


def parse_timestamp(timestamp_str: str) -> Optional[datetime]:
    """
    Parse timestamp string into datetime object.

    Supports ISO-8601 variants and Unix timestamps.
    """
    primary_format = "%Y-%m-%dT%H:%M:%S.%f+00:00"

    try:
        return datetime.strptime(timestamp_str.strip(), primary_format)
    except ValueError:
        pass

    fallback_formats = [
        "%Y-%m-%dT%H:%M:%S.%fZ",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S.%f",
        "%Y-%m-%d %H:%M:%S",
    ]

    for fmt in fallback_formats:
        try:
            return datetime.strptime(timestamp_str.strip(), fmt)
        except ValueError:
            continue

    try:
        return datetime.fromtimestamp(float(timestamp_str))
    except (ValueError, OverflowError):
        pass

    LOGGER.debug(f"Could not parse timestamp: {timestamp_str}")
    return None


def convert_to_iso_format(timestamp_str: str) -> str:
    """Convert timestamp to ISO format required by ReportPortal."""
    parsed_time = parse_timestamp(timestamp_str)
    if parsed_time:
        return parsed_time.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    else:
        LOGGER.warning(
            f"Could not parse timestamp '{timestamp_str}', using current time"
        )
        return datetime.now().strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def extract_latest_timestamp_from_xml(xml_content: str) -> Optional[str]:
    """
    Extract the latest timestamp (end-time) from parsed XML content.

    Returns:
        ISO formatted timestamp string or None
    """
    if not xml_content or not xml_content.strip():
        LOGGER.warning("No XML content provided for timestamp extraction")
        return None

    try:
        LOGGER.debug(f"Parsing XML content (length: {len(xml_content)} chars)")
        xml = lxml.etree.fromstring(xml_content.encode())
        latest_timestamp = None
        latest_datetime = None

        elements_to_check = xml.xpath("//testsuite | //testcase")
        LOGGER.debug(
            f"Found {len(elements_to_check)} XML elements to check " f"for timestamps"
        )

        if not elements_to_check:
            LOGGER.warning("No testsuite or testcase elements found in XML")
            all_elements = xml.xpath("//*")
            LOGGER.debug(f"Total XML elements found: {len(all_elements)}")
            elements_to_check = all_elements

        for elem in elements_to_check:
            if "end-time" in elem.attrib:
                timestamp_str = elem.attrib["end-time"]
                LOGGER.debug(
                    f"Found timestamp in {elem.tag}.end-time: " f"{timestamp_str}"
                )
                parsed_time = parse_timestamp(timestamp_str)
                if parsed_time and (
                    latest_datetime is None or parsed_time > latest_datetime
                ):
                    latest_datetime = parsed_time
                    latest_timestamp = timestamp_str
                    LOGGER.debug(f"New latest timestamp: {timestamp_str}")

        if latest_timestamp is None:
            LOGGER.debug(
                "No timestamps found in attributes, " "checking element text content"
            )
            timestamp_elements = xml.xpath("//timestamp | //time | //end-time")
            for elem in timestamp_elements:
                if elem.text:
                    LOGGER.debug(f"Found timestamp element {elem.tag}: {elem.text}")
                    parsed_time = parse_timestamp(elem.text)
                    if parsed_time and (
                        latest_datetime is None or parsed_time > latest_datetime
                    ):
                        latest_datetime = parsed_time
                        latest_timestamp = elem.text

        if latest_timestamp:
            iso_timestamp = convert_to_iso_format(latest_timestamp)
            LOGGER.info(f"Latest timestamp extracted from XML: {iso_timestamp}")
            return iso_timestamp
        else:
            LOGGER.warning("No timestamps found in XML content")
            LOGGER.debug("XML structure preview:")
            LOGGER.debug(
                lxml.etree.tostring(xml, pretty_print=True, encoding="unicode")[:500]
                + "..."
            )
            return None

    except lxml.etree.XMLSyntaxError as e:
        LOGGER.error(f"XML parsing error: {e}")
        LOGGER.debug(f"XML content preview: {xml_content[:200]}...")
        return None
    except Exception as e:
        LOGGER.error(f"Error extracting timestamp from XML: {e}")
        return None


# ===================================================================
# Artifact / XML parsing
# ===================================================================


def extract_artifacts_url(task_data: Dict[str, Any]) -> Optional[str]:
    """Extract artifacts URL from request JSON run section."""
    try:
        run_data = task_data.get("run", {})
        if not isinstance(run_data, dict):
            LOGGER.warning("No 'run' section found in task data")
            return None

        artifacts = run_data.get("artifacts")
        if not artifacts:
            LOGGER.warning("No 'artifacts' found in run section")
            return None

        if isinstance(artifacts, str):
            LOGGER.debug(f"Found artifacts URL: {artifacts}")
            return artifacts
        elif isinstance(artifacts, dict):
            url_keys = ["url", "link", "href", "artifacts_url"]
            for key in url_keys:
                if key in artifacts and artifacts[key]:
                    LOGGER.debug(f"Found artifacts URL in '{key}': {artifacts[key]}")
                    return artifacts[key]
            LOGGER.warning(f"No URL found in artifacts dict: {artifacts}")
            return None
        else:
            LOGGER.warning(f"Unexpected artifacts type: {type(artifacts)}")
            return None

    except Exception as e:
        LOGGER.error(f"Error extracting artifacts URL: {e}")
        return None


def discover_artifacts_from_xml(
    xml_content: str,
) -> List[ArtifactFile]:
    """
    Discover artifact files by parsing the results.xml from Testing Farm.

    The results.xml contains ``<log>`` elements with direct ``href`` URLs
    at both the testsuite and testcase levels.
    """
    artifacts: List[ArtifactFile] = []

    try:
        xml = lxml.etree.fromstring(xml_content.encode())

        for suite_elem in xml.xpath("//testsuite"):
            suite_name = suite_elem.get("name", "")

            for log_elem in suite_elem.xpath("./logs/log"):
                href = log_elem.get("href", "")
                name = log_elem.get("name", "")
                if not href:
                    continue
                if name in ("workdir", "data", "log_dir"):
                    continue
                ext = os.path.splitext(href)[1].lower()
                if ext in BINARY_EXTENSIONS:
                    LOGGER.debug(f"Skipping binary artifact: {name}")
                    continue
                artifacts.append(
                    ArtifactFile(
                        name=name,
                        url=href,
                        relative_path=(f"[suite] {suite_name} / {name}"),
                        parent_type="testsuite",
                        parent_name=suite_name,
                    )
                )

            for tc_elem in suite_elem.xpath("./testcase"):
                tc_name = tc_elem.get("name", "")
                for log_elem in tc_elem.xpath("./logs/log"):
                    href = log_elem.get("href", "")
                    name = log_elem.get("name", "")
                    if not href:
                        continue
                    if name in ("workdir", "data", "log_dir"):
                        continue
                    ext = os.path.splitext(href)[1].lower()
                    if ext in BINARY_EXTENSIONS:
                        LOGGER.debug(f"Skipping binary artifact: {name}")
                        continue
                    artifacts.append(
                        ArtifactFile(
                            name=name,
                            url=href,
                            relative_path=(f"[test] {tc_name} / {name}"),
                            parent_type="testcase",
                            parent_name=tc_name,
                        )
                    )

    except lxml.etree.XMLSyntaxError as e:
        LOGGER.error(f"Failed to parse results.xml: {e}")
    except Exception as e:
        LOGGER.error(f"Error discovering artifacts from XML: {e}")

    LOGGER.info(f"Discovered {len(artifacts)} artifact(s) from results.xml")
    return artifacts


def map_artifacts_to_items(
    artifacts: List[ArtifactFile],
    test_items: List[Dict[str, Any]],
) -> List[Tuple[ArtifactFile, Optional[str]]]:
    """
    Map artifact files to ReportPortal test item UUIDs.

    Uses the ``parent_name`` set during XML parsing and matches against
    RP test item names.  Unmatched artifacts map to ``None``
    (launch-level logs).
    """
    mapped: List[Tuple[ArtifactFile, Optional[str]]] = []

    if not test_items:
        LOGGER.warning(
            "No RP test items found -- " "all artifacts will go to launch level"
        )
        return [(a, None) for a in artifacts]

    item_by_full: Dict[str, str] = {}
    item_by_tail: Dict[str, str] = {}

    for item in test_items:
        item_name = item.get("name", "")
        item_uuid = item.get("uuid", "")
        if not item_name or not item_uuid:
            continue

        normalised_full = item_name.strip("/").lower()
        item_by_full[normalised_full] = item_uuid

        tail = normalised_full.rsplit("/", 1)[-1]
        item_by_tail[tail] = item_uuid

    unique_parents = {a.parent_name for a in artifacts}
    LOGGER.debug(f"XML parent names to match: {unique_parents}")
    LOGGER.debug(f"RP item full names: {list(item_by_full.keys())}")

    for artifact in artifacts:
        parent = artifact.parent_name.strip("/").lower()

        matched = item_by_full.get(parent)

        if not matched:
            parent_tail = parent.rsplit("/", 1)[-1]
            matched = item_by_tail.get(parent_tail)

        if not matched:
            for rp_name, rp_uuid in item_by_full.items():
                if parent in rp_name or rp_name in parent:
                    matched = rp_uuid
                    break

        if not matched:
            LOGGER.debug(f"No RP item match for parent: " f"{artifact.parent_name!r}")

        mapped.append((artifact, matched))

    matched_count = sum(1 for _, uid in mapped if uid is not None)
    LOGGER.info(
        f"Mapped {matched_count}/{len(artifacts)} artifacts to test items "
        f"({len(artifacts) - matched_count} will be attached at "
        f"launch level)"
    )
    return mapped


# ===================================================================
# RP test-item helpers
# ===================================================================


def extract_artifacts_url_from_items(
    items: List[Dict[str, Any]],
) -> Optional[str]:
    """
    Extract an artifacts URL from RP test-item descriptions.

    The TMT ReportPortal plugin typically includes the Testing Farm
    artifacts URL in the test-item description.  This function scans
    all items and returns the first ``http(s)://`` URL found that
    looks like an artifacts link.
    """
    import re

    url_pattern = re.compile(r"(https?://\S*artifacts\S*)", re.IGNORECASE)

    for item in items:
        desc = item.get("description") or ""
        match = url_pattern.search(desc)
        if match:
            url = match.group(1).rstrip(")")
            LOGGER.debug(
                f"Found artifacts URL in test item " f"'{item.get('name', '?')}': {url}"
            )
            return url

    return None


def has_in_progress_items(items: List[Dict[str, Any]]) -> bool:
    """Return True if any test item is still IN_PROGRESS."""
    return any((item.get("status") or "").upper() == "IN_PROGRESS" for item in items)


def derive_status_from_items(
    items: List[Dict[str, Any]],
) -> str:
    """
    Derive an overall launch status from RP test item statuses.

    Returns ``"PASSED"``, ``"FAILED"``, ``"STOPPED"``, or
    ``"IN_PROGRESS"`` when items are still running.
    """
    if not items:
        return "STOPPED"

    statuses = {item.get("status", "").upper() for item in items}

    if "IN_PROGRESS" in statuses:
        return "IN_PROGRESS"
    if "FAILED" in statuses:
        return "FAILED"
    if "INTERRUPTED" in statuses:
        return "STOPPED"
    if statuses <= {"PASSED", "SKIPPED", "INFO"}:
        return "PASSED"
    return "FAILED"


def latest_end_time_from_items(
    items: List[Dict[str, Any]],
) -> Optional[str]:
    """
    Extract the latest ``endTime`` from RP test items.

    RP stores times as Unix-epoch milliseconds (int) or ISO strings.
    Returns an ISO timestamp suitable for the finish endpoint, or None
    if no end times are found.
    """
    latest_ms: Optional[int] = None

    for item in items:
        end_time = item.get("endTime")
        if end_time is None:
            continue
        # RP typically returns epoch millis as an int
        if isinstance(end_time, (int, float)):
            ms = int(end_time)
        elif isinstance(end_time, str) and end_time.isdigit():
            ms = int(end_time)
        else:
            # Try parsing ISO string via the existing helper
            parsed = parse_timestamp(str(end_time))
            if parsed:
                ms = int(parsed.timestamp() * 1000)
            else:
                continue

        if latest_ms is None or ms > latest_ms:
            latest_ms = ms

    if latest_ms is None:
        return None

    dt = datetime.fromtimestamp(latest_ms / 1000.0)
    iso = dt.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    LOGGER.debug(f"Latest test-item endTime: {iso}")
    return iso


ENRICHED_ATTRIBUTE_KEY = "logs_attached"


def is_launch_enriched(launch: Dict[str, Any]) -> bool:
    """Check whether a launch already has the ``enge_enriched`` attribute."""
    for attr in launch.get("attributes", []):
        if attr.get("key") == ENRICHED_ATTRIBUTE_KEY:
            return True
    return False


def build_enriched_attributes(
    launch: Dict[str, Any],
) -> List[Dict[str, str]]:
    """
    Return the launch's existing attributes with ``enge_enriched=true``
    appended (if not already present).
    """
    attrs = list(launch.get("attributes") or [])
    if not any(a.get("key") == ENRICHED_ATTRIBUTE_KEY for a in attrs):
        attrs.append({"key": ENRICHED_ATTRIBUTE_KEY, "value": "true"})
    return attrs


# ===================================================================
# Deduplication helpers
# ===================================================================


def extract_existing_log_headers(
    logs: List[Dict[str, Any]],
) -> frozenset:
    """
    Extract enrichment artifact headers from existing RP log entries.

    Each enrichment log is written with a ``### \\`name\\`` first line.
    Returns a frozen set of those header lines so callers can skip
    artifacts that were already uploaded.

    Also recognises the pattern when RP strips backticks or wraps the
    name in ``<code>`` tags.
    """
    import re

    headers: set = set()
    for log in logs:
        msg = log.get("message", "")
        if not msg:
            continue
        first_line = msg.split("\n", 1)[0].strip()
        # Exact match: ### `name`
        if first_line.startswith("### `"):
            headers.add(first_line)
            continue
        # Fallback: extract name from ### <code>name</code> or
        # ### name variants produced by RP rendering
        m = re.match(
            r"^###\s+(?:<code>)?[`]?(.+?)[`]?(?:</code>)?\s*$",
            first_line,
        )
        if m:
            headers.add(f"### `{m.group(1)}`")

    if headers:
        LOGGER.info(
            f"Dedup: found {len(headers)} enrichment log header(s) "
            f"in existing launch logs"
        )
        LOGGER.debug(f"Dedup headers: {headers}")
    else:
        if logs:
            # We have logs but no enrichment headers — log a sample
            # so the user can diagnose format mismatches
            sample_msgs = [(log.get("message", "") or "")[:80] for log in logs[:3]]
            LOGGER.debug(
                f"Dedup: {len(logs)} log(s) present but none "
                f"matched enrichment header pattern. "
                f"Sample messages: {sample_msgs}"
            )
    return frozenset(headers)


def filter_already_enriched(
    mapped_artifacts: List[Tuple[ArtifactFile, Optional[str]]],
    existing_headers: frozenset,
) -> List[Tuple[ArtifactFile, Optional[str]]]:
    """
    Remove artifacts whose header is already present in the launch.

    Returns the filtered list and logs how many were skipped.
    """
    if not existing_headers:
        return mapped_artifacts

    filtered = []
    skipped_names = []
    for art, uid in mapped_artifacts:
        if f"### `{art.name}`" in existing_headers:
            skipped_names.append(art.name)
        else:
            filtered.append((art, uid))

    if skipped_names:
        LOGGER.info(
            f"Dedup: skipping {len(skipped_names)} artifact(s) "
            f"already present in launch logs: "
            f"{', '.join(skipped_names[:10])}"
            + (
                f" ... and {len(skipped_names) - 10} more"
                if len(skipped_names) > 10
                else ""
            )
        )
    return filtered


def filter_to_failed_items(
    mapped_artifacts: List[Tuple[ArtifactFile, Optional[str]]],
    items: List[Dict[str, Any]],
) -> List[Tuple[ArtifactFile, Optional[str]]]:
    """
    Keep only artifacts mapped to failed test items.

    Unmapped (launch-level) artifacts are retained when at least one
    item in the launch has failed, since suite-level logs may contain
    useful setup/teardown context.

    Args:
        mapped_artifacts: list of ``(ArtifactFile, item_uuid | None)``
        items: RP test-item dicts (must include ``uuid`` and ``status``)

    Returns:
        Filtered list of mapped artifacts.
    """
    if not items:
        return mapped_artifacts

    # Build uuid → status lookup
    status_by_uuid: Dict[str, str] = {
        item["uuid"]: (item.get("status") or "").upper()
        for item in items
        if "uuid" in item
    }

    has_failure = any(s == "FAILED" for s in status_by_uuid.values())

    filtered = []
    skipped = 0
    for art, uid in mapped_artifacts:
        if uid is None:
            # Unmapped (launch/suite level) — keep only if failures
            if has_failure:
                filtered.append((art, uid))
            else:
                skipped += 1
        elif status_by_uuid.get(uid) == "FAILED":
            filtered.append((art, uid))
        else:
            skipped += 1

    if skipped:
        LOGGER.info(
            f"Skipping {skipped} artifact(s) mapped to " f"passed/skipped items"
        )
    return filtered


# ===================================================================
# Dry-run display helpers
# ===================================================================


def show_dryrun_finish_data(
    api_base: str,
    token: str,
    task_uuid: str,
    launch_uuid: str,
    end_time: str,
    status: str,
    description: Optional[str] = None,
    attributes: Optional[List[Dict[str, str]]] = None,
) -> None:
    """Display what would be sent to ReportPortal in dry run mode."""
    import json

    from rich.panel import Panel
    from rich.table import Table

    from enge.utils.console import console

    finish_data: Dict[str, Any] = {
        "endTime": end_time,
        "status": status,
    }
    if description is not None:
        finish_data["description"] = description
    if attributes:
        finish_data["attributes"] = attributes

    kv = Table.grid(padding=(0, 2))
    kv.add_column(style="bold")
    kv.add_column()
    kv.add_row("Task UUID:", task_uuid)
    kv.add_row("Launch UUID:", launch_uuid)
    kv.add_row(
        "API Endpoint:",
        f"PUT {api_base}/launch/{launch_uuid}/finish",
    )
    kv.add_row()
    kv.add_row("Request Headers:", "")
    kv.add_row("", "Authorization: Bearer ***REDACTED***")
    kv.add_row("", "Content-Type: application/json")
    kv.add_row()
    kv.add_row("Request Body:", "")

    if attributes:
        kv.add_row()
        kv.add_row("Attributes Details:", "")
        for attr in attributes:
            kv.add_row("", f"  {escape(attr['key'])}: {escape(attr['value'])}")

    panel = Panel(
        kv,
        title="[bold]DRY RUN - ReportPortal Launch Finish Request[/]",
        subtitle=(
            f"[success]Would finish launch {launch_uuid} " f"for task {task_uuid}[/]"
        ),
        border_style="blue",
    )
    from enge.utils import redact_sensitive

    console.print()
    console.print(panel)
    print(json.dumps(redact_sensitive(finish_data), indent=2, ensure_ascii=False))
    console.print()


def show_dryrun_enrichment(
    task_uuid: str,
    launch_uuid: str,
    mapped: List[Tuple[ArtifactFile, Optional[str]]],
) -> None:
    """Display what would be uploaded in dry-run mode."""
    from rich.panel import Panel
    from rich.table import Table

    from enge.utils.console import console

    launch_level = [a for a, uid in mapped if uid is None]
    item_level = [a for a, uid in mapped if uid is not None]

    kv = Table.grid(padding=(0, 2))
    kv.add_column(style="bold")
    kv.add_column()
    kv.add_row("Task UUID:", task_uuid)
    kv.add_row("Launch UUID:", launch_uuid)
    kv.add_row("Total artifacts:", str(len(mapped)))

    if item_level:
        kv.add_row()
        kv.add_row(f"Artifacts mapped to test items ({len(item_level)}):", "")
        for artifact, _ in [(a, u) for a, u in mapped if u is not None]:
            kv.add_row("", f"  -> {artifact.relative_path}")

    if launch_level:
        kv.add_row()
        kv.add_row(f"Artifacts at launch level ({len(launch_level)}):", "")
        for artifact in launch_level:
            kv.add_row("", f"  -> {artifact.relative_path}")

    panel = Panel(
        kv,
        title="[bold]DRY RUN - Log Enrichment Preview[/]",
        subtitle=(
            f"[success]Would upload {len(mapped)} artifact(s) "
            f"to launch {launch_uuid}[/]"
        ),
        border_style="blue",
    )
    console.print()
    console.print(panel)
    console.print()


def show_dryrun_delete_logs(
    task_uuid: str,
    launch_uuid: str,
    logs: List[Dict[str, Any]],
) -> None:
    """Display what would be deleted in dry-run mode."""
    from rich.panel import Panel
    from rich.table import Table

    from enge.utils.console import console

    kv = Table.grid(padding=(0, 2))
    kv.add_column(style="bold")
    kv.add_column()
    kv.add_row("Task UUID:", task_uuid)
    kv.add_row("Launch UUID:", launch_uuid)
    kv.add_row("Total log entries:", str(len(logs)))

    if logs:
        sample_size = min(10, len(logs))
        kv.add_row()
        kv.add_row(
            f"Sample log entries (showing {sample_size} of {len(logs)}):",
            "",
        )
        for log in logs[:sample_size]:
            log_id = log.get("id", "?")
            level = log.get("level", "?")
            message = log.get("message", "")
            if len(message) > 80:
                message = message[:77] + "..."
            message = message.replace("\n", " ")
            kv.add_row(
                "",
                f"  [{escape(str(level))}] (id={escape(str(log_id))}) {escape(message)}",
            )
        if len(logs) > sample_size:
            kv.add_row("", f"  ... and {len(logs) - sample_size} more")

    log_word = "entry" if len(logs) == 1 else "entries"
    panel = Panel(
        kv,
        title="[bold]DRY RUN - Log Deletion Preview[/]",
        subtitle=(
            f"[success]Would delete {len(logs)} log {log_word} "
            f"from launch {launch_uuid}[/]"
        ),
        border_style="blue",
    )
    console.print()
    console.print(panel)
    console.print()


def show_dryrun_delete_stale(launches: List[Dict[str, Any]]) -> None:
    """Display stale launches that would be deleted in dry-run mode."""
    from rich.panel import Panel
    from rich.table import Table

    from enge.utils.console import console

    kv = Table.grid(padding=(0, 2))
    kv.add_column(style="bold")
    kv.add_column()
    kv.add_row(
        "Stale launches (stopped/interrupted, no items):",
        str(len(launches)),
    )
    kv.add_row()
    for launch in launches:
        name = launch.get("name", "Unknown")
        uuid = launch.get("uuid", launch.get("id", "?"))
        lid = launch.get("id", "?")
        kv.add_row(
            "",
            f"  - {escape(str(name))} (id={escape(str(lid))}, uuid={escape(str(uuid))})",
        )

    word = "launch" if len(launches) == 1 else "launches"
    panel = Panel(
        kv,
        title="[bold]DRY RUN - Stale Launch Deletion Preview[/]",
        subtitle=(f"[success]Would delete " f"{len(launches)} stale {word}[/]"),
        border_style="blue",
    )
    console.print()
    console.print(panel)
    console.print()
