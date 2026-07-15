import json
import os
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Union

from enge.utils.errors import ConflictError, ValidationError


class Verdict(str, Enum):
    """The results.json verdict enum -- shared by the root verdict and every
    task/plan/test verdict. Contract-pinned; do not add members without
    maintainer sign-off (see CLAUDE.md "Results.json format")."""

    PASSED = "PASSED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"
    ERROR = "ERROR"
    CANCELED = "CANCELED"


_VALID_VERDICTS = {member.value for member in Verdict}

# Severity ranking for root-verdict derivation (finalize_root_verdict):
# highest-severity task verdict present wins. All-SKIPPED naturally yields
# SKIPPED since it is the lowest rank and no higher rank is present.
_SEVERITY_RANK = {
    Verdict.SKIPPED.value: 0,
    Verdict.PASSED.value: 1,
    Verdict.CANCELED.value: 2,
    Verdict.FAILED.value: 3,
    Verdict.ERROR.value: 4,
}

# Xunit result -> schema verdict mapping contract. This module does NOT
# parse xunit (report-layer work, later branch); the map is exported so the
# future parser and this schema contract cannot drift. Mapping applies only
# to TERMINAL tasks -- unknown xunit values are report-layer policy (map to
# ERROR + WARNING log), not a schema concern. The xunit `stage` attribute is
# deliberately not stored here; the verbatim xunit archive retains it.
XUNIT_RESULT_MAP = {
    "passed": "PASSED",
    "failed": "FAILED",
    "error": "ERROR",
    "skipped": "SKIPPED",
    "undefined": "ERROR",  # terminal task, plan never completed (e.g. stage=guest-provisioning)
    "pending": "ERROR",  # terminal task, test never ran; duration_seconds = 0
}

_TEST_REQUIRED_FIELDS = ("name", "verdict", "duration_seconds")
_PLAN_REQUIRED_FIELDS = ("name", "verdict", "tests")
_TASK_REQUIRED_FIELDS = (
    "task_id",
    "set",
    "tier",
    "arch",
    "source_compose",
    "target_compose",
    "dispatched_at",
    "verdict",
    "total_duration_seconds",
    "plans",
)
_ROOT_REQUIRED_FIELDS = (
    "schema_version",
    "run_id",
    "created_at",
    "event",
    "source",
    "target",
    "verdict",
    "results",
)


def _require_fields(
    data: Dict[str, Any], fields: Sequence[str], *, context: str
) -> None:
    missing = [f for f in fields if f not in data]
    if missing:
        raise ValidationError(
            f"{context}: missing required field(s): {', '.join(missing)}"
        )


def _validate_verdict(value: Any, *, context: str, nullable: bool = False) -> Any:
    if value is None:
        if nullable:
            return None
        raise ValidationError(f"{context}: verdict is required and cannot be null")
    if isinstance(value, Verdict):
        value = value.value
    if not isinstance(value, str) or value not in _VALID_VERDICTS:
        raise ValidationError(
            f"{context}: invalid verdict {value!r}; must be one of "
            f"{sorted(_VALID_VERDICTS)}"
        )
    return value


def _validate_string(value: Any, field_name: str, *, context: str) -> str:
    if not isinstance(value, str):
        raise ValidationError(
            f"{context}: field '{field_name}' must be a string, got {value!r}"
        )
    return value


def _validate_optional_string(
    value: Any, field_name: str, *, context: str
) -> Optional[str]:
    if value is None:
        return None
    return _validate_string(value, field_name, context=context)


def _validate_duration(value: Any, field_name: str, *, context: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError(
            f"{context}: '{field_name}' must be a number, got {value!r}"
        )
    return float(value)


def _validate_timestamp(value: Any, field_name: str, *, context: str) -> str:
    if not isinstance(value, str):
        raise ValidationError(
            f"{context}: '{field_name}' must be an ISO 8601 string, got {value!r}"
        )
    try:
        datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValidationError(
            f"{context}: '{field_name}' is not a valid ISO 8601 timestamp: "
            f"{value!r}"
        ) from exc
    return value


def _validate_optional_timestamp(
    value: Any, field_name: str, *, context: str
) -> Optional[str]:
    if value is None:
        return None
    return _validate_timestamp(value, field_name, context=context)


def _validate_schema_version(value: Any, *, context: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value != 1:
        raise ValidationError(f"{context}: 'schema_version' must be 1, got {value!r}")
    return value


@dataclass(frozen=True)
class TestEntry:
    """One entry of a plan's `tests` array."""

    name: str
    verdict: str
    duration_seconds: float
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    output: Optional[str] = None
    error_detail: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "name": self.name,
            "verdict": self.verdict,
            "duration_seconds": self.duration_seconds,
        }
        if self.start_time is not None:
            payload["start_time"] = self.start_time
        if self.end_time is not None:
            payload["end_time"] = self.end_time
        if self.output is not None:
            payload["output"] = self.output
        if self.error_detail is not None:
            payload["error_detail"] = self.error_detail
        return payload

    @classmethod
    def from_dict(cls, data: Any) -> "TestEntry":
        if not isinstance(data, dict):
            raise ValidationError(f"test entry: expected an object, got {data!r}")
        _require_fields(data, _TEST_REQUIRED_FIELDS, context="test entry")

        name = _validate_string(data["name"], "name", context="test entry")
        context = f"test '{name}'"
        verdict = _validate_verdict(data["verdict"], context=context)
        duration = _validate_duration(
            data["duration_seconds"], "duration_seconds", context=context
        )
        start_time = _validate_optional_timestamp(
            data.get("start_time"), "start_time", context=context
        )
        end_time = _validate_optional_timestamp(
            data.get("end_time"), "end_time", context=context
        )
        output = _validate_optional_string(
            data.get("output"), "output", context=context
        )
        error_detail = _validate_optional_string(
            data.get("error_detail"), "error_detail", context=context
        )
        return cls(
            name=name,
            verdict=verdict,
            duration_seconds=duration,
            start_time=start_time,
            end_time=end_time,
            output=output,
            error_detail=error_detail,
        )


@dataclass(frozen=True)
class PlanEntry:
    """One entry of a task's `plans` array -- one xunit testsuite."""

    name: str
    verdict: str
    tests: List[TestEntry] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "verdict": self.verdict,
            "tests": [t.to_dict() for t in self.tests],
        }

    @classmethod
    def from_dict(cls, data: Any) -> "PlanEntry":
        if not isinstance(data, dict):
            raise ValidationError(f"plan entry: expected an object, got {data!r}")
        _require_fields(data, _PLAN_REQUIRED_FIELDS, context="plan entry")

        name = _validate_string(data["name"], "name", context="plan entry")
        context = f"plan '{name}'"
        verdict = _validate_verdict(data["verdict"], context=context)

        raw_tests = data["tests"]
        if not isinstance(raw_tests, list):
            raise ValidationError(
                f"{context}: 'tests' must be an array, got {raw_tests!r}"
            )
        tests = [TestEntry.from_dict(t) for t in raw_tests]

        # Validity rule 4: PASSED/FAILED plans must have non-empty tests;
        # SKIPPED/ERROR plans may have an empty tests array.
        if verdict in (Verdict.PASSED.value, Verdict.FAILED.value) and not tests:
            raise ValidationError(
                f"{context}: verdict is {verdict} but 'tests' is empty; "
                "PASSED/FAILED plans must have at least one test"
            )

        return cls(name=name, verdict=verdict, tests=tests)


@dataclass(frozen=True)
class TaskEntry:
    """One entry of the run envelope's `results` array -- one TF request."""

    task_id: str
    set: str  # mirrors the results.json "set" field verbatim
    tier: str
    arch: str
    source_compose: Optional[str]
    target_compose: Optional[str]
    dispatched_at: str
    verdict: str
    total_duration_seconds: float
    plans: List[PlanEntry] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "set": self.set,
            "tier": self.tier,
            "arch": self.arch,
            "source_compose": self.source_compose,
            "target_compose": self.target_compose,
            "dispatched_at": self.dispatched_at,
            "verdict": self.verdict,
            "total_duration_seconds": self.total_duration_seconds,
            "plans": [p.to_dict() for p in self.plans],
        }

    @classmethod
    def from_dict(cls, data: Any) -> "TaskEntry":
        if not isinstance(data, dict):
            raise ValidationError(f"task entry: expected an object, got {data!r}")
        _require_fields(data, _TASK_REQUIRED_FIELDS, context="task entry")

        task_id = _validate_string(data["task_id"], "task_id", context="task entry")
        context = f"task '{task_id}'"
        set_name = _validate_string(data["set"], "set", context=context)
        tier = _validate_string(data["tier"], "tier", context=context)
        arch = _validate_string(data["arch"], "arch", context=context)
        source_compose = _validate_optional_string(
            data["source_compose"], "source_compose", context=context
        )
        target_compose = _validate_optional_string(
            data["target_compose"], "target_compose", context=context
        )
        dispatched_at = _validate_timestamp(
            data["dispatched_at"], "dispatched_at", context=context
        )
        verdict = _validate_verdict(data["verdict"], context=context)
        total_duration_seconds = _validate_duration(
            data["total_duration_seconds"], "total_duration_seconds", context=context
        )

        raw_plans = data["plans"]
        if not isinstance(raw_plans, list):
            raise ValidationError(
                f"{context}: 'plans' must be an array, got {raw_plans!r}"
            )
        plans = [PlanEntry.from_dict(p) for p in raw_plans]

        _validate_task_plans_arity(verdict, plans, context=context)

        return cls(
            task_id=task_id,
            set=set_name,
            tier=tier,
            arch=arch,
            source_compose=source_compose,
            target_compose=target_compose,
            dispatched_at=dispatched_at,
            verdict=verdict,
            total_duration_seconds=total_duration_seconds,
            plans=plans,
        )


def _validate_task_plans_arity(
    verdict: str, plans: List[PlanEntry], *, context: str
) -> None:
    """Validity rules 1-3. SKIPPED task-level verdicts are not covered by
    the ratified rule set and are deliberately left unconstrained here --
    see CLAUDE.md "Results.json format" for the documented gap."""
    if verdict == Verdict.CANCELED.value and plans:
        raise ValidationError(
            f"{context}: verdict is CANCELED but 'plans' is non-empty; "
            "CANCELED tasks are dispatch-level cancels with no partial results"
        )
    if verdict in (Verdict.PASSED.value, Verdict.FAILED.value) and not plans:
        raise ValidationError(
            f"{context}: verdict is {verdict} but 'plans' is empty; "
            "PASSED/FAILED tasks must have at least one plan"
        )


@dataclass(frozen=True)
class ResultsJsonSchema:
    """The results.json v3.1 run envelope (contract-pinned as of
    2026-07-14, see CLAUDE.md "Results.json format").

    A plain dataclass + manual validators, not pydantic: pydantic is not a
    project dependency, is used nowhere else in the codebase, and this
    module's scope (schema + local storage only) does not warrant adding a
    new hard runtime dependency.
    """

    schema_version: int
    run_id: str
    created_at: str
    event: str
    source: str
    target: str
    verdict: Optional[str]
    results: List[TaskEntry] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "created_at": self.created_at,
            "event": self.event,
            "source": self.source,
            "target": self.target,
            "verdict": self.verdict,
            "results": [t.to_dict() for t in self.results],
        }

    @classmethod
    def from_dict(cls, data: Any) -> "ResultsJsonSchema":
        if not isinstance(data, dict):
            raise ValidationError(f"results.json: expected an object, got {data!r}")
        _require_fields(data, _ROOT_REQUIRED_FIELDS, context="results.json")

        schema_version = _validate_schema_version(
            data["schema_version"], context="results.json"
        )
        run_id = _validate_string(data["run_id"], "run_id", context="results.json")
        created_at = _validate_timestamp(
            data["created_at"], "created_at", context="results.json"
        )
        event = _validate_string(data["event"], "event", context="results.json")
        source = _validate_string(data["source"], "source", context="results.json")
        target = _validate_string(data["target"], "target", context="results.json")
        verdict = _validate_verdict(
            data["verdict"], context="results.json", nullable=True
        )

        raw_results = data["results"]
        if not isinstance(raw_results, list):
            raise ValidationError(
                f"results.json: 'results' must be an array, got {raw_results!r}"
            )
        results = [TaskEntry.from_dict(t) for t in raw_results]
        _validate_unique_task_ids(results)

        return cls(
            schema_version=schema_version,
            run_id=run_id,
            created_at=created_at,
            event=event,
            source=source,
            target=target,
            verdict=verdict,
            results=results,
        )


def _validate_unique_task_ids(results: List[TaskEntry]) -> None:
    seen = set()
    for task in results:
        if task.task_id in seen:
            raise ValidationError(
                f"results.json: duplicate task_id {task.task_id!r} in 'results'"
            )
        seen.add(task.task_id)


def parse_results_json(path: Union[str, Path]) -> ResultsJsonSchema:
    """Read and validate a results.json file. Raises ValidationError on any
    schema mismatch (including malformed JSON)."""
    text = Path(path).read_text()
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValidationError(f"{path}: not valid JSON: {exc}") from exc
    return ResultsJsonSchema.from_dict(data)


# ---------------------------------------------------------------------------
# Gap-fill write API.
#
# Replaces the old one-shot write_results_json(): one dispatch produces one
# results.json seeded up front (init_results_json) and filled in
# incrementally as each TF task's xunit becomes available
# (upsert_task_result), with the root verdict derived exactly once all
# expected tasks have landed (finalize_root_verdict). Raw xunit is
# colocated separately per task (write_xunit) since a no-xunit ERROR task
# must remain fully representable in results.json with no xml file.
# ---------------------------------------------------------------------------


def _atomic_write_json(path: Path, data: Dict[str, Any]) -> None:
    """Tmp-then-replace atomic write, mirroring the pattern used by
    ManifestWriter.flush() in utils/manifest.py. Duplicated rather than
    imported: this module stays dependency-free of the manifest layer by
    design (see CLAUDE.md "Results.json format")."""
    tmp_path = path.with_name(path.name + ".tmp")
    tmp_path.write_text(json.dumps(data, indent=2))
    os.replace(tmp_path, path)


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    """Byte-mode counterpart of _atomic_write_json, used by write_xunit."""
    tmp_path = path.with_name(path.name + ".tmp")
    tmp_path.write_bytes(data)
    os.replace(tmp_path, path)


def init_results_json(
    run_id: str,
    created_at: str,
    event: str,
    source: str,
    target: str,
    output_dir: Union[str, Path],
) -> Path:
    """Create `<output_dir>/<run_id>.json` with verdict: null, results: [].

    Refuses to overwrite an existing file by raising the builtin
    FileExistsError -- its stdlib semantics ("trying to create a file...
    which already exists") are an exact match for this precondition, and no
    new EngeError subclass is warranted for what is a filesystem
    precondition rather than a data-validation failure.
    """
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    results_path = out_dir / f"{run_id}.json"
    if results_path.exists():
        raise FileExistsError(
            f"{results_path}: results.json already exists; refusing to overwrite"
        )

    schema = ResultsJsonSchema.from_dict(
        {
            "schema_version": 1,
            "run_id": run_id,
            "created_at": created_at,
            "event": event,
            "source": source,
            "target": target,
            "verdict": None,
            "results": [],
        }
    )
    _atomic_write_json(results_path, schema.to_dict())
    return results_path


def upsert_task_result(
    path: Union[str, Path], task_entry: Union[TaskEntry, Dict[str, Any]]
) -> bool:
    """Write-once upsert of one task entry, keyed by task_id.

    - task_id absent -> validate, append, atomic rewrite, return True.
    - task_id present with identical content -> no-op, return False.
    - task_id present with different content -> raise ConflictError (the
      idempotency fence; never silently overwrite).
    - file already finalized (root verdict non-null) -> raise
      ConflictError, regardless of content.
    """
    path = Path(path)
    schema = parse_results_json(path)

    if schema.verdict is not None:
        raise ConflictError(
            f"{path}: results.json is already finalized (verdict="
            f"{schema.verdict!r}); cannot upsert further task results"
        )

    new_entry = (
        task_entry
        if isinstance(task_entry, TaskEntry)
        else TaskEntry.from_dict(task_entry)
    )

    existing = next((t for t in schema.results if t.task_id == new_entry.task_id), None)
    if existing is None:
        updated = replace(schema, results=[*schema.results, new_entry])
        _atomic_write_json(path, updated.to_dict())
        return True

    if existing.to_dict() == new_entry.to_dict():
        return False

    raise ConflictError(
        f"{path}: task_id {new_entry.task_id!r} already has a different "
        "result recorded; refusing to overwrite"
    )


def _derive_root_verdict(task_verdicts: Sequence[str]) -> str:
    """Severity-max derivation: ERROR > FAILED > CANCELED > PASSED >
    SKIPPED. All-SKIPPED naturally yields SKIPPED (lowest rank, nothing
    higher present). `task_verdicts` must be non-empty; finalize_root_
    verdict with expected_count=0 (zero results) is an edge case the
    ratified spec does not address -- see CLAUDE.md "Results.json format"
    for the documented gap."""
    return max(task_verdicts, key=lambda v: _SEVERITY_RANK[v])


def finalize_root_verdict(path: Union[str, Path], expected_count: int) -> Optional[str]:
    """Derive and write the root verdict exactly once, when `results`
    reaches `expected_count` entries.

    - len(results) < expected_count -> no-op, return None.
    - len(results) == expected_count -> derive severity-max and write it
      (unless already finalized, in which case the stored value is
      returned verbatim with no rewrite), return the value.
    - len(results) > expected_count -> raise ValidationError (impossible
      state; something upstream recorded more tasks than expected).
    """
    path = Path(path)
    schema = parse_results_json(path)

    count = len(schema.results)
    if count < expected_count:
        return None
    if count > expected_count:
        raise ValidationError(
            f"{path}: {count} task entries recorded but expected_count="
            f"{expected_count}; more entries than expected is an "
            "impossible state"
        )

    if schema.verdict is not None:
        return schema.verdict

    derived = _derive_root_verdict([task.verdict for task in schema.results])
    updated = replace(schema, verdict=derived)
    _atomic_write_json(path, updated.to_dict())
    return derived


def write_xunit(
    run_id: str,
    task_id: str,
    xunit_bytes: bytes,
    output_dir: Union[str, Path],
) -> Path:
    """Byte-verbatim write to `<output_dir>/<run_id>/<task_id>.xml`.

    Conditionally present by contract: a no-xunit ERROR task has an entry
    in results.json but no xml -- gap-fill logic must key on the JSON
    entry, never on xml presence.

    Write-once: existing file with different bytes -> ConflictError;
    identical -> no-op.
    """
    run_dir = Path(output_dir) / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    xunit_path = run_dir / f"{task_id}.xml"

    if xunit_path.exists():
        existing_bytes = xunit_path.read_bytes()
        if existing_bytes == xunit_bytes:
            return xunit_path
        raise ConflictError(
            f"{xunit_path}: xunit already recorded for task {task_id!r} "
            "with different content"
        )

    _atomic_write_bytes(xunit_path, xunit_bytes)
    return xunit_path
