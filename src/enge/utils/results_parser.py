import json
import os
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Union

from enge.utils.errors import ValidationError


class Verdict(str, Enum):
    """The results.json verdict enum -- shared by the root verdict and every
    per-test verdict. Contract-pinned; do not add members without
    maintainer sign-off (see CLAUDE.md "Results.json format")."""

    PASSED = "PASSED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"
    ERROR = "ERROR"
    CANCELED = "CANCELED"


_VALID_VERDICTS = {member.value for member in Verdict}

_TEST_REQUIRED_FIELDS = ("name", "verdict", "duration_seconds")
_ROOT_REQUIRED_FIELDS = (
    "run_id",
    "request_timestamp",
    "set",
    "tier",
    "arch",
    "source",
    "target",
    "verdict",
    "tests",
    "total_duration_seconds",
)


def _require_fields(
    data: Dict[str, Any], fields: Sequence[str], *, context: str
) -> None:
    missing = [f for f in fields if f not in data]
    if missing:
        raise ValidationError(
            f"{context}: missing required field(s): {', '.join(missing)}"
        )


def _validate_verdict(value: Any, *, context: str) -> str:
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


def _validate_duration(value: Any, *, context: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError(
            f"{context}: 'duration_seconds' must be a number, got {value!r}"
        )
    return float(value)


def _validate_timestamp(value: Any, *, context: str) -> str:
    if not isinstance(value, str):
        raise ValidationError(
            f"{context}: 'request_timestamp' must be an ISO 8601 string, "
            f"got {value!r}"
        )
    try:
        datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValidationError(
            f"{context}: 'request_timestamp' is not a valid ISO 8601 "
            f"timestamp: {value!r}"
        ) from exc
    return value


@dataclass(frozen=True)
class TestResult:
    """One entry of the results.json `tests` array."""

    name: str
    verdict: str
    duration_seconds: float
    output: Optional[str] = None
    error_detail: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "name": self.name,
            "verdict": self.verdict,
            "duration_seconds": self.duration_seconds,
        }
        if self.output is not None:
            payload["output"] = self.output
        if self.error_detail is not None:
            payload["error_detail"] = self.error_detail
        return payload

    @classmethod
    def from_dict(cls, data: Any) -> "TestResult":
        if not isinstance(data, dict):
            raise ValidationError(f"test entry: expected an object, got {data!r}")
        _require_fields(data, _TEST_REQUIRED_FIELDS, context="test entry")

        name = _validate_string(data["name"], "name", context="test entry")
        verdict = _validate_verdict(data["verdict"], context=f"test '{name}'")
        duration = _validate_duration(
            data["duration_seconds"], context=f"test '{name}'"
        )
        output = _validate_optional_string(
            data.get("output"), "output", context=f"test '{name}'"
        )
        error_detail = _validate_optional_string(
            data.get("error_detail"), "error_detail", context=f"test '{name}'"
        )
        return cls(
            name=name,
            verdict=verdict,
            duration_seconds=duration,
            output=output,
            error_detail=error_detail,
        )


@dataclass(frozen=True)
class ResultsJsonSchema:
    """The results.json MVP schema (contract-pinned, see CLAUDE.md).

    A plain dataclass + manual validators, not pydantic: pydantic is not a
    project dependency, is used nowhere else in the codebase, and this
    module's scope (schema + local storage only) does not warrant adding
    a new hard runtime dependency. See DEBRIEF.md for the full rationale.
    """

    run_id: str
    request_timestamp: str
    set: str
    tier: str
    arch: str
    source: str
    target: str
    verdict: str
    tests: List[TestResult]
    total_duration_seconds: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "request_timestamp": self.request_timestamp,
            "set": self.set,
            "tier": self.tier,
            "arch": self.arch,
            "source": self.source,
            "target": self.target,
            "verdict": self.verdict,
            "tests": [t.to_dict() for t in self.tests],
            "total_duration_seconds": self.total_duration_seconds,
        }

    @classmethod
    def from_dict(cls, data: Any) -> "ResultsJsonSchema":
        if not isinstance(data, dict):
            raise ValidationError(f"results.json: expected an object, got {data!r}")
        _require_fields(data, _ROOT_REQUIRED_FIELDS, context="results.json")

        run_id = _validate_string(data["run_id"], "run_id", context="results.json")
        request_timestamp = _validate_timestamp(
            data["request_timestamp"], context="results.json"
        )
        set_name = _validate_string(data["set"], "set", context="results.json")
        tier = _validate_string(data["tier"], "tier", context="results.json")
        arch = _validate_string(data["arch"], "arch", context="results.json")
        source = _validate_string(data["source"], "source", context="results.json")
        target = _validate_string(data["target"], "target", context="results.json")
        verdict = _validate_verdict(data["verdict"], context="results.json")
        total_duration_seconds = _validate_duration(
            data["total_duration_seconds"], context="results.json"
        )

        raw_tests = data["tests"]
        if not isinstance(raw_tests, list):
            raise ValidationError(
                f"results.json: 'tests' must be an array, got {raw_tests!r}"
            )
        tests = [TestResult.from_dict(t) for t in raw_tests]

        if verdict == Verdict.CANCELED.value and tests:
            raise ValidationError(
                "results.json: verdict is CANCELED but 'tests' is "
                "non-empty; CANCELED runs must have an empty tests array"
            )

        return cls(
            run_id=run_id,
            request_timestamp=request_timestamp,
            set=set_name,
            tier=tier,
            arch=arch,
            source=source,
            target=target,
            verdict=verdict,
            tests=tests,
            total_duration_seconds=total_duration_seconds,
        )


def parse_results_json(path: Union[str, Path]) -> ResultsJsonSchema:
    """Read and validate a results.json file. Raises ValidationError on any
    schema mismatch (including malformed JSON)."""
    text = Path(path).read_text()
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValidationError(f"{path}: not valid JSON: {exc}") from exc
    return ResultsJsonSchema.from_dict(data)


def write_results_json(
    run_id: str,
    verdict: str,
    tests: Sequence[Union[TestResult, Dict[str, Any]]],
    request_timestamp: str,
    set: str,  # noqa: A002 -- mirrors the results.json "set" field verbatim
    tier: str,
    arch: str,
    source: str,
    target: str,
    total_duration_seconds: float,
    output_path: Union[str, Path],
    xunit_bytes: Optional[bytes] = None,
) -> Path:
    """Validate and write results.json to `<output_path>/<run_id>.json`.

    Metadata (set/tier/arch/source/target) is accepted explicitly here --
    manifest lookup with fallback-to-explicit-values is a report-layer
    concern (see CLAUDE.md "Results.json format" / DEBRIEF.md "Report
    integration scope"), not this module's.

    When `xunit_bytes` is given, the raw bytes are also written
    byte-for-byte to `<output_path>/<run_id>.xml` -- no re-encoding, no
    transformation, a verbatim copy of the TF artifact input.

    Raises ValidationError on schema violation.
    """
    tests_payload: List[Any] = [
        t.to_dict() if isinstance(t, TestResult) else t for t in tests
    ]
    schema = ResultsJsonSchema.from_dict(
        {
            "run_id": run_id,
            "request_timestamp": request_timestamp,
            "set": set,
            "tier": tier,
            "arch": arch,
            "source": source,
            "target": target,
            "verdict": verdict,
            "tests": tests_payload,
            "total_duration_seconds": total_duration_seconds,
        }
    )

    out_dir = Path(output_path)
    out_dir.mkdir(parents=True, exist_ok=True)

    results_path = out_dir / f"{run_id}.json"
    tmp_path = out_dir / f"{run_id}.json.tmp"
    tmp_path.write_text(json.dumps(schema.to_dict(), indent=2))
    os.rename(tmp_path, results_path)

    if xunit_bytes is not None:
        xunit_path = out_dir / f"{run_id}.xml"
        xunit_tmp_path = out_dir / f"{run_id}.xml.tmp"
        xunit_tmp_path.write_bytes(xunit_bytes)
        os.rename(xunit_tmp_path, xunit_path)

    return results_path
