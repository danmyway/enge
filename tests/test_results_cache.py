"""RED tests for the report-side results.json cache writer
(enge.report.results_cache).

Write policy under test (see CLAUDE.md "Results.json format" and the
fire-time coordinator ruling in the feat/report-results-cache session log):

- Manifest-backed report invocations (--run, --set/--tier/--arch/--tag
  filters, or no-args-latest) gap-fill <run_id>.json + verbatim xunit for
  EVERY matched run.
- Raw-input invocations (--file, --input) never write a cache -- no
  resolvable run_id.
- Only TERMINAL tasks get a results.json entry; CANCELED is terminal for
  this purpose (unlike reportportal.operations._is_tf_task_incomplete,
  which treats it as incomplete).
- Unknown xunit verdicts map to ERROR with a WARNING log.
- A task_id present in TF results but absent from the manifest's
  requests[] is skipped with a WARNING -- never fabricated.
- Caching failures (ConflictError from a corrupted prior cache) must not
  raise out of cache_report_results -- the report command's table output
  must never be at risk.
- Xunit is stored byte-verbatim from TaskResult.xunit_bytes, never
  re-derived from the decoded TaskResult.xunit_content string (fire-time
  ruling: str.encode() is not a safe inverse of response.text's decode).
"""

import json
import tempfile
import unittest
from pathlib import Path

from tests._helpers import make_app_context
from enge.report.concurrent_parser import TaskResult
from enge.utils.results_parser import parse_results_json


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


def _request(
    task_id,
    *,
    set_name="verification_99_103_ctc2-ver-9to10",
    tier="tier1",
    arch="x86_64",
    source_compose="RHEL-9.9.0-20260629.0",
    target_compose=None,
    dispatched_at="2026-07-07T10:35:13Z",
):
    return {
        "task_id": task_id,
        "set": set_name,
        "tier": tier,
        "arch": arch,
        "plan": None,
        "source_compose": source_compose,
        "target_compose": target_compose,
        "artifacts_url": None,
        "dispatched_at": dispatched_at,
        "launch_uuid": None,
    }


def _write_manifest(runs_dir, run_id, requests, context=None, created_at=None):
    manifest = {
        "schema_version": 1,
        "run_id": run_id,
        "created_at": created_at or "2026-07-07T10:35:07Z",
        "command": "test",
        "argv": ["enge", "test"],
        "tags": [],
        "parent_run_id": None,
        "origin": "native",
        "context": (
            context
            if context is not None
            else {"event": "preliminary", "source": "9.9", "target": "10.3"}
        ),
        "requests": requests,
    }
    Path(runs_dir).mkdir(parents=True, exist_ok=True)
    (Path(runs_dir) / f"{run_id}.json").write_text(json.dumps(manifest))
    return manifest


def _xunit_bytes(
    plan_name="/plans/newstyle/nondestructive/verification_99_103_ctc2",
    plan_result="passed",
    tests=None,
    encoding="utf-8",
):
    """Build minimal xunit bytes. `tests` is a list of
    (name, result, time, start_time, end_time) tuples; any of the last
    three may be None to omit that attribute."""
    if tests is None:
        tests = [("/tests/basic", plan_result, "10.0", None, None)]

    testcases = []
    for name, result, time_attr, start_time, end_time in tests:
        attrs = f'name="{name}" result="{result}" time="{time_attr}"'
        if start_time:
            attrs += f' start-time="{start_time}"'
        if end_time:
            attrs += f' end-time="{end_time}"'
        testcases.append(f"<testcase {attrs}/>")

    xml = (
        f'<?xml version="1.0" encoding="{encoding}"?>\n'
        f'<testsuites overall-result="{plan_result}">'
        f'<testsuite name="{plan_name}" result="{plan_result}">'
        f'{"".join(testcases)}'
        f"</testsuite></testsuites>"
    )
    return xml.encode(encoding)


# ISO-8859-1 fixture whose raw bytes are NOT stable under a decode(guessed)
# -> encode("utf-8") round trip. 0xE9 is 'é' in Latin-1; UTF-8 encodes the
# same character as the two bytes 0xC3 0xA9. Any writer that re-derives
# bytes from the decoded string via .encode("utf-8") produces a file that
# differs from the original wire bytes -- exactly the defect the fire-time
# ruling on xunit byte retention exists to prevent.
_ISO8859_XUNIT_BYTES = (
    b'<?xml version="1.0" encoding="ISO-8859-1"?>\n'
    b'<testsuites overall-result="passed">'
    b'<testsuite name="/plans/p1" result="passed">'
    b'<testcase name="/tests/t\xe9" result="passed" time="1.0"/>'
    b"</testsuite></testsuites>"
)


def _make_task_result(**overrides):
    defaults = dict(
        request_uuid="5d67eecf-a02d-46b7-aee2-9ffb673f40df",
        request_source_compose="RHEL-9.9.0-20260629.0",
        request_target_release="10.3",
        request_upgrade_path="9.9 to 10.3",
        request_arch="x86_64",
        request_state="COMPLETE",
        request_datetime_created="2026-07-07T10:35:13Z",
        request_plan="",
        request_plan_filter="",
        request_summary="Undefined",
        request_result_overall="Undefined",
        results_xml_url="https://tf.example.com/artifacts/task/results.xml",
        url="https://tf.example.com/api/task",
    )
    defaults.update(overrides)
    return TaskResult(**defaults)


class _CacheTestCase(unittest.TestCase):
    def _ctx(self, runs_dir, results_dir_path, extra_cli=None, manifest_latest=None):
        return make_app_context(
            action="report",
            extra_cli=extra_cli or {},
            extra_config={"common": {"results_dir": str(results_dir_path)}},
            manifest_runs_dir=str(runs_dir),
            manifest_latest=str(manifest_latest) if manifest_latest else "/nonexistent",
        )

    def _tmp_dirs(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        runs_dir = Path(tmp.name) / "runs"
        results_dir_path = Path(tmp.name) / "results"
        return runs_dir, results_dir_path

    def _write_latest_pointer(self, tmp_root, manifest_path):
        latest_path = Path(tmp_root) / "latest"
        latest_path.write_text(str(manifest_path))
        return latest_path


class TestManifestBackedVsRawInput(_CacheTestCase):
    def test_run_flag_invocation_writes_cache(self):
        from enge.report.results_cache import cache_report_results

        runs_dir, results_dir_path = self._tmp_dirs()
        run_id = "01RUNIDAAAAAAAAAAAAAAAAAAA"
        task_id = "5d67eecf-a02d-46b7-aee2-9ffb673f40df"
        _write_manifest(runs_dir, run_id, [_request(task_id)])
        ctx = self._ctx(runs_dir, results_dir_path, extra_cli={"run": run_id})

        task_result = _make_task_result(
            request_uuid=task_id, xunit_bytes=_xunit_bytes()
        )
        cache_report_results(ctx, [task_result])

        results_path = results_dir_path / f"{run_id}.json"
        self.assertTrue(results_path.exists())
        schema = parse_results_json(results_path)
        self.assertEqual(len(schema.results), 1)
        self.assertEqual(schema.results[0].task_id, task_id)

    def test_filter_flag_invocation_writes_cache(self):
        from enge.report.results_cache import cache_report_results

        runs_dir, results_dir_path = self._tmp_dirs()
        run_id = "01RUNIDBBBBBBBBBBBBBBBBBBB"
        task_id = "aaaaaaaa-0000-0000-0000-000000000001"
        _write_manifest(
            runs_dir,
            run_id,
            [_request(task_id, set_name="myset")],
            context={
                "event": "preliminary",
                "source": "9.9",
                "target": "10.3",
                "set": "myset",
            },
        )
        ctx = self._ctx(runs_dir, results_dir_path, extra_cli={"filter_set": "myset"})

        task_result = _make_task_result(
            request_uuid=task_id, xunit_bytes=_xunit_bytes()
        )
        cache_report_results(ctx, [task_result])

        results_path = results_dir_path / f"{run_id}.json"
        self.assertTrue(results_path.exists())

    def test_file_flag_invocation_does_not_write_cache(self):
        # Mutation-check target (c): if raw-input invocations were made to
        # write a cache, this test would fail because a results.json would
        # appear despite --file having no resolvable run_id. A real latest
        # manifest is seeded so the assertion is discriminating: without
        # the --file guard firing FIRST, _resolve_manifests_for_report
        # would otherwise fall through to (and find) this latest manifest.
        from enge.report.results_cache import cache_report_results

        runs_dir, results_dir_path = self._tmp_dirs()
        run_id = "01RUNIDXXFILEGUARDXXXXXXXX"
        task_id = "5d67eecf-a02d-46b7-aee2-9ffb673f40df"
        manifest_path = runs_dir / f"{run_id}.json"
        _write_manifest(runs_dir, run_id, [_request(task_id)])
        latest_pointer = self._write_latest_pointer(runs_dir.parent, manifest_path)

        ctx = self._ctx(
            runs_dir,
            results_dir_path,
            extra_cli={"file": ["/tmp/some_tasks.txt"]},
            manifest_latest=latest_pointer,
        )

        task_result = _make_task_result(
            request_uuid=task_id, xunit_bytes=_xunit_bytes()
        )
        cache_report_results(ctx, [task_result])

        self.assertFalse(any(results_dir_path.glob("*.json")))

    def test_input_flag_invocation_does_not_write_cache(self):
        # Same discriminating setup as the --file test above: a real
        # latest manifest exists (and would match this task_id) so the
        # --input guard is what's actually being pinned, not an absent
        # fallback.
        from enge.report.results_cache import cache_report_results

        runs_dir, results_dir_path = self._tmp_dirs()
        run_id = "01RUNIDXXINPUTGUARDXXXXXXX"
        task_id = "5d67eecf-a02d-46b7-aee2-9ffb673f40df"
        manifest_path = runs_dir / f"{run_id}.json"
        _write_manifest(runs_dir, run_id, [_request(task_id)])
        latest_pointer = self._write_latest_pointer(runs_dir.parent, manifest_path)

        ctx = self._ctx(
            runs_dir,
            results_dir_path,
            extra_cli={"input": [task_id]},
            manifest_latest=latest_pointer,
        )

        task_result = _make_task_result(
            request_uuid=task_id, xunit_bytes=_xunit_bytes()
        )
        cache_report_results(ctx, [task_result])

        self.assertFalse(any(results_dir_path.glob("*.json")))
        self.assertFalse(results_dir_path.exists() and any(results_dir_path.iterdir()))


class TestTerminalityGate(_CacheTestCase):
    def test_running_task_produces_no_entry_yet(self):
        from enge.report.results_cache import cache_report_results

        runs_dir, results_dir_path = self._tmp_dirs()
        run_id = "01RUNIDCCCCCCCCCCCCCCCCCCC"
        task_id = "bbbbbbbb-0000-0000-0000-000000000001"
        _write_manifest(runs_dir, run_id, [_request(task_id)])
        ctx = self._ctx(runs_dir, results_dir_path, extra_cli={"run": run_id})

        task_result = _make_task_result(request_uuid=task_id, request_state="RUNNING")
        cache_report_results(ctx, [task_result])

        results_path = results_dir_path / f"{run_id}.json"
        self.assertTrue(results_path.exists())
        schema = parse_results_json(results_path)
        self.assertEqual(schema.results, [])

    def test_canceled_task_is_terminal_and_cached(self):
        # Mutation-check target (a): if _is_task_terminal excluded CANCELED,
        # this test would fail (no entry would be written).
        from enge.report.results_cache import cache_report_results

        runs_dir, results_dir_path = self._tmp_dirs()
        run_id = "01RUNIDDDDDDDDDDDDDDDDDDDD"
        task_id = "cccccccc-0000-0000-0000-000000000001"
        _write_manifest(runs_dir, run_id, [_request(task_id)])
        ctx = self._ctx(runs_dir, results_dir_path, extra_cli={"run": run_id})

        task_result = _make_task_result(request_uuid=task_id, request_state="CANCELED")
        cache_report_results(ctx, [task_result])

        schema = parse_results_json(results_dir_path / f"{run_id}.json")
        self.assertEqual(len(schema.results), 1)
        entry = schema.results[0]
        self.assertEqual(entry.verdict, "CANCELED")
        self.assertEqual(entry.plans, [])

    def test_error_state_task_is_terminal(self):
        from enge.report.results_cache import cache_report_results

        runs_dir, results_dir_path = self._tmp_dirs()
        run_id = "01RUNIDEEEEEEEEEEEEEEEEEEE"
        task_id = "dddddddd-0000-0000-0000-000000000001"
        _write_manifest(runs_dir, run_id, [_request(task_id)])
        ctx = self._ctx(runs_dir, results_dir_path, extra_cli={"run": run_id})

        task_result = _make_task_result(request_uuid=task_id, request_state="ERROR")
        cache_report_results(ctx, [task_result])

        schema = parse_results_json(results_dir_path / f"{run_id}.json")
        self.assertEqual(len(schema.results), 1)


class TestNoXunitErrorEntry(_CacheTestCase):
    def test_terminal_task_without_xunit_is_error_with_empty_plans(self):
        from enge.report.results_cache import cache_report_results

        runs_dir, results_dir_path = self._tmp_dirs()
        run_id = "01RUNIDFFFFFFFFFFFFFFFFFFF"
        task_id = "eeeeeeee-0000-0000-0000-000000000001"
        _write_manifest(runs_dir, run_id, [_request(task_id)])
        ctx = self._ctx(runs_dir, results_dir_path, extra_cli={"run": run_id})

        task_result = _make_task_result(
            request_uuid=task_id, request_state="ERROR", xunit_bytes=None
        )
        cache_report_results(ctx, [task_result])

        schema = parse_results_json(results_dir_path / f"{run_id}.json")
        entry = schema.results[0]
        self.assertEqual(entry.verdict, "ERROR")
        self.assertEqual(entry.plans, [])


class TestVerdictMapping(_CacheTestCase):
    def test_known_verdicts_map_through_xunit_result_map(self):
        from enge.report.results_cache import cache_report_results

        runs_dir, results_dir_path = self._tmp_dirs()
        run_id = "01RUNIDGGGGGGGGGGGGGGGGGGG"
        task_id = "ffffffff-0000-0000-0000-000000000001"
        _write_manifest(runs_dir, run_id, [_request(task_id)])
        ctx = self._ctx(runs_dir, results_dir_path, extra_cli={"run": run_id})

        task_result = _make_task_result(
            request_uuid=task_id,
            xunit_bytes=_xunit_bytes(
                plan_result="failed",
                tests=[("/tests/basic", "failed", "5.0", None, None)],
            ),
        )
        cache_report_results(ctx, [task_result])

        schema = parse_results_json(results_dir_path / f"{run_id}.json")
        entry = schema.results[0]
        self.assertEqual(entry.plans[0].verdict, "FAILED")
        self.assertEqual(entry.plans[0].tests[0].verdict, "FAILED")

    def test_unknown_xunit_verdict_maps_to_error_with_warning(self):
        # Mutation-check target (b): if unknown verdicts were mapped to
        # PASSED instead of ERROR, this test would fail.
        from enge.report.results_cache import cache_report_results

        runs_dir, results_dir_path = self._tmp_dirs()
        run_id = "01RUNIDHHHHHHHHHHHHHHHHHHH"
        task_id = "11111111-0000-0000-0000-000000000001"
        _write_manifest(runs_dir, run_id, [_request(task_id)])
        ctx = self._ctx(runs_dir, results_dir_path, extra_cli={"run": run_id})

        task_result = _make_task_result(
            request_uuid=task_id,
            xunit_bytes=_xunit_bytes(
                plan_result="needs_inspection",
                tests=[("/tests/basic", "needs_inspection", "5.0", None, None)],
            ),
        )
        with self.assertLogs("enge.report.results_cache", level="WARNING") as cm:
            cache_report_results(ctx, [task_result])

        schema = parse_results_json(results_dir_path / f"{run_id}.json")
        entry = schema.results[0]
        self.assertEqual(entry.plans[0].verdict, "ERROR")
        self.assertEqual(entry.plans[0].tests[0].verdict, "ERROR")
        self.assertTrue(any("unknown xunit verdict" in msg for msg in cm.output))

    def test_duration_and_timestamps_copied_verbatim(self):
        from enge.report.results_cache import cache_report_results

        runs_dir, results_dir_path = self._tmp_dirs()
        run_id = "01RUNIDIIIIIIIIIIIIIIIIIII"
        task_id = "22222222-0000-0000-0000-000000000001"
        _write_manifest(runs_dir, run_id, [_request(task_id)])
        ctx = self._ctx(runs_dir, results_dir_path, extra_cli={"run": run_id})

        task_result = _make_task_result(
            request_uuid=task_id,
            xunit_bytes=_xunit_bytes(
                tests=[
                    (
                        "/tests/basic",
                        "passed",
                        "75.0",
                        "2026-07-07T10:51:27.205058+00:00",
                        "2026-07-07T10:52:48.504964+00:00",
                    )
                ]
            ),
        )
        cache_report_results(ctx, [task_result])

        schema = parse_results_json(results_dir_path / f"{run_id}.json")
        test_entry = schema.results[0].plans[0].tests[0]
        self.assertEqual(test_entry.duration_seconds, 75.0)
        self.assertEqual(test_entry.start_time, "2026-07-07T10:51:27.205058+00:00")
        self.assertEqual(test_entry.end_time, "2026-07-07T10:52:48.504964+00:00")
        self.assertEqual(schema.results[0].total_duration_seconds, 75.0)


class TestMetadataFromManifest(_CacheTestCase):
    def test_task_metadata_copied_verbatim_from_manifest_request(self):
        from enge.report.results_cache import cache_report_results

        runs_dir, results_dir_path = self._tmp_dirs()
        run_id = "01RUNIDJJJJJJJJJJJJJJJJJJJ"
        task_id = "33333333-0000-0000-0000-000000000001"
        _write_manifest(
            runs_dir,
            run_id,
            [
                _request(
                    task_id,
                    set_name="verification_99_103_ctc2-ver-9to10",
                    tier="tier3",
                    arch="s390x",
                    source_compose="RHEL-9.9.0-20260629.0",
                    target_compose=None,
                    dispatched_at="2026-07-07T10:35:13Z",
                )
            ],
        )
        ctx = self._ctx(runs_dir, results_dir_path, extra_cli={"run": run_id})

        task_result = _make_task_result(
            request_uuid=task_id, xunit_bytes=_xunit_bytes()
        )
        cache_report_results(ctx, [task_result])

        entry = parse_results_json(results_dir_path / f"{run_id}.json").results[0]
        self.assertEqual(entry.set, "verification_99_103_ctc2-ver-9to10")
        self.assertEqual(entry.tier, "tier3")
        self.assertEqual(entry.arch, "s390x")
        self.assertEqual(entry.source_compose, "RHEL-9.9.0-20260629.0")
        self.assertIsNone(entry.target_compose)
        self.assertEqual(entry.dispatched_at, "2026-07-07T10:35:13Z")

    def test_envelope_fields_copied_from_manifest(self):
        from enge.report.results_cache import cache_report_results

        runs_dir, results_dir_path = self._tmp_dirs()
        run_id = "01RUNIDKKKKKKKKKKKKKKKKKKK"
        task_id = "44444444-0000-0000-0000-000000000001"
        _write_manifest(
            runs_dir,
            run_id,
            [_request(task_id)],
            context={"event": "candidate", "source": "9.9", "target": "10.3"},
            created_at="2026-07-01T00:00:00Z",
        )
        ctx = self._ctx(runs_dir, results_dir_path, extra_cli={"run": run_id})

        task_result = _make_task_result(
            request_uuid=task_id, xunit_bytes=_xunit_bytes()
        )
        cache_report_results(ctx, [task_result])

        schema = parse_results_json(results_dir_path / f"{run_id}.json")
        self.assertEqual(schema.run_id, run_id)
        self.assertEqual(schema.created_at, "2026-07-01T00:00:00Z")
        self.assertEqual(schema.event, "candidate")
        self.assertEqual(schema.source, "9.9")
        self.assertEqual(schema.target, "10.3")


class TestUnknownTaskIdSkip(_CacheTestCase):
    def test_task_id_absent_from_manifest_is_skipped_with_warning(self):
        from enge.report.results_cache import cache_report_results

        runs_dir, results_dir_path = self._tmp_dirs()
        run_id = "01RUNIDLLLLLLLLLLLLLLLLLLL"
        known_task_id = "55555555-0000-0000-0000-000000000001"
        unknown_task_id = "66666666-0000-0000-0000-000000000002"
        _write_manifest(runs_dir, run_id, [_request(known_task_id)])
        ctx = self._ctx(runs_dir, results_dir_path, extra_cli={"run": run_id})

        known = _make_task_result(
            request_uuid=known_task_id, xunit_bytes=_xunit_bytes()
        )
        unknown = _make_task_result(
            request_uuid=unknown_task_id, xunit_bytes=_xunit_bytes()
        )
        with self.assertLogs("enge.report.results_cache", level="WARNING") as cm:
            cache_report_results(ctx, [known, unknown])

        schema = parse_results_json(results_dir_path / f"{run_id}.json")
        task_ids = {t.task_id for t in schema.results}
        self.assertEqual(task_ids, {known_task_id})
        self.assertTrue(any(unknown_task_id in msg for msg in cm.output))


class TestGapFillAcrossInvocations(_CacheTestCase):
    def test_partial_then_complete_finalizes_root_verdict(self):
        from enge.report.results_cache import cache_report_results

        runs_dir, results_dir_path = self._tmp_dirs()
        run_id = "01RUNIDMMMMMMMMMMMMMMMMMMM"
        task_a = "77777777-0000-0000-0000-000000000001"
        task_b = "88888888-0000-0000-0000-000000000002"
        _write_manifest(
            runs_dir,
            run_id,
            [_request(task_a, tier="tier1"), _request(task_b, tier="tier2")],
        )
        ctx = self._ctx(runs_dir, results_dir_path, extra_cli={"run": run_id})

        # First invocation: only task_a has completed.
        result_a = _make_task_result(
            request_uuid=task_a,
            xunit_bytes=_xunit_bytes(
                plan_result="passed", tests=[("/tests/a", "passed", "1.0", None, None)]
            ),
        )
        result_b_running = _make_task_result(
            request_uuid=task_b, request_state="RUNNING"
        )
        cache_report_results(ctx, [result_a, result_b_running])

        schema_after_first = parse_results_json(results_dir_path / f"{run_id}.json")
        self.assertEqual(len(schema_after_first.results), 1)
        self.assertIsNone(schema_after_first.verdict)

        # Second invocation: task_b has now completed with a failure.
        result_b_done = _make_task_result(
            request_uuid=task_b,
            xunit_bytes=_xunit_bytes(
                plan_result="failed", tests=[("/tests/b", "failed", "2.0", None, None)]
            ),
        )
        cache_report_results(ctx, [result_a, result_b_done])

        schema_final = parse_results_json(results_dir_path / f"{run_id}.json")
        self.assertEqual(len(schema_final.results), 2)
        self.assertEqual(schema_final.verdict, "FAILED")


class TestCachingFailureDoesNotBreakReport(_CacheTestCase):
    def test_conflicting_cached_entry_is_logged_and_does_not_raise(self):
        from enge.report.results_cache import cache_report_results

        runs_dir, results_dir_path = self._tmp_dirs()
        run_id = "01RUNIDNNNNNNNNNNNNNNNNNNN"
        task_a = "99999999-0000-0000-0000-000000000001"
        task_b = "aaaaaaaa-1111-0000-0000-000000000002"
        _write_manifest(runs_dir, run_id, [_request(task_a), _request(task_b)])
        ctx = self._ctx(runs_dir, results_dir_path, extra_cli={"run": run_id})

        # Seed a cached entry for task_a that will conflict with what this
        # invocation is about to compute (same task_id, different content).
        result_a_v1 = _make_task_result(
            request_uuid=task_a,
            xunit_bytes=_xunit_bytes(
                plan_result="passed", tests=[("/tests/a", "passed", "1.0", None, None)]
            ),
        )
        cache_report_results(ctx, [result_a_v1])

        result_a_v2_conflicting = _make_task_result(
            request_uuid=task_a,
            xunit_bytes=_xunit_bytes(
                plan_result="failed", tests=[("/tests/a", "failed", "1.0", None, None)]
            ),
        )
        result_b = _make_task_result(
            request_uuid=task_b,
            xunit_bytes=_xunit_bytes(
                plan_result="passed", tests=[("/tests/b", "passed", "1.0", None, None)]
            ),
        )

        try:
            with self.assertLogs("enge.report.results_cache", level="WARNING"):
                cache_report_results(ctx, [result_a_v2_conflicting, result_b])
        except AssertionError:
            self.fail("cache_report_results raised instead of logging and continuing")

        schema = parse_results_json(results_dir_path / f"{run_id}.json")
        task_ids = {t.task_id for t in schema.results}
        # task_a keeps its original (v1) cached content; task_b still lands
        # despite task_a's conflict.
        self.assertEqual(task_ids, {task_a, task_b})
        entry_a = next(t for t in schema.results if t.task_id == task_a)
        self.assertEqual(entry_a.plans[0].verdict, "PASSED")

    def test_finalized_run_reupsert_logs_debug_not_warning(self):
        # Split target: re-reporting an already-finalized run is the
        # expected steady state, not a data-drift signal -- every task's
        # upsert hits the unconditional finalized-file guard regardless of
        # content, so before the split this logged one WARNING per task on
        # every single invocation, forever.
        from enge.report.results_cache import cache_report_results

        runs_dir, results_dir_path = self._tmp_dirs()
        run_id = "01RUNIDFFFFFFFFFFFFFFFFFFF"
        task_a = "99999999-0000-0000-0000-00000000000f"
        _write_manifest(runs_dir, run_id, [_request(task_a)])
        ctx = self._ctx(runs_dir, results_dir_path, extra_cli={"run": run_id})

        result_a = _make_task_result(
            request_uuid=task_a,
            xunit_bytes=_xunit_bytes(
                plan_result="passed", tests=[("/tests/a", "passed", "1.0", None, None)]
            ),
        )
        cache_report_results(ctx, [result_a])
        schema = parse_results_json(results_dir_path / f"{run_id}.json")
        self.assertIsNotNone(schema.verdict)  # sanity: run is finalized

        with self.assertLogs("enge.report.results_cache", level="DEBUG") as cm:
            cache_report_results(ctx, [result_a])

        levels = [record.levelname for record in cm.records]
        self.assertNotIn("WARNING", levels)
        self.assertIn("DEBUG", levels)
        debug_messages = [
            record.getMessage() for record in cm.records if record.levelname == "DEBUG"
        ]
        self.assertTrue(
            any(run_id in msg for msg in debug_messages),
            f"expected a DEBUG message naming the run, got: {debug_messages}",
        )


class TestVerbatimXunitBytes(_CacheTestCase):
    def test_write_xunit_stores_byte_identical_copy(self):
        from enge.report.results_cache import cache_report_results

        runs_dir, results_dir_path = self._tmp_dirs()
        run_id = "01RUNIDOOOOOOOOOOOOOOOOOOO"
        task_id = "bbbbbbbb-2222-0000-0000-000000000001"
        _write_manifest(runs_dir, run_id, [_request(task_id)])
        ctx = self._ctx(runs_dir, results_dir_path, extra_cli={"run": run_id})

        raw = _xunit_bytes()
        task_result = _make_task_result(request_uuid=task_id, xunit_bytes=raw)
        cache_report_results(ctx, [task_result])

        xml_path = results_dir_path / run_id / f"{task_id}.xml"
        self.assertEqual(xml_path.read_bytes(), raw)

    def test_write_xunit_uses_wire_bytes_not_reencoded_text(self):
        # Discriminating fixture (fire-time ruling condition 4): raw bytes
        # are ISO-8859-1 with a non-ASCII byte that is NOT stable under a
        # decode -> UTF-8 encode round trip. Mutation-check target (d): if
        # the writer derived bytes from xunit_content.encode("utf-8")
        # instead of using xunit_bytes directly, this assertion fails.
        from enge.report.results_cache import cache_report_results

        runs_dir, results_dir_path = self._tmp_dirs()
        run_id = "01RUNIDPPPPPPPPPPPPPPPPPPP"
        task_id = "cccccccc-3333-0000-0000-000000000001"
        _write_manifest(runs_dir, run_id, [_request(task_id)])
        ctx = self._ctx(runs_dir, results_dir_path, extra_cli={"run": run_id})

        decoded_text = _ISO8859_XUNIT_BYTES.decode("ISO-8859-1")
        task_result = _make_task_result(
            request_uuid=task_id,
            xunit_bytes=_ISO8859_XUNIT_BYTES,
            xunit_content=decoded_text,
        )
        cache_report_results(ctx, [task_result])

        xml_path = results_dir_path / run_id / f"{task_id}.xml"
        on_disk = xml_path.read_bytes()
        self.assertEqual(on_disk, _ISO8859_XUNIT_BYTES)
        # Sanity check that this fixture actually discriminates: the
        # naive re-encode would have produced different bytes.
        self.assertNotEqual(on_disk, decoded_text.encode("utf-8"))

    def test_no_xunit_task_does_not_write_xml_file(self):
        from enge.report.results_cache import cache_report_results

        runs_dir, results_dir_path = self._tmp_dirs()
        run_id = "01RUNIDQQQQQQQQQQQQQQQQQQQ"
        task_id = "dddddddd-4444-0000-0000-000000000001"
        _write_manifest(runs_dir, run_id, [_request(task_id)])
        ctx = self._ctx(runs_dir, results_dir_path, extra_cli={"run": run_id})

        task_result = _make_task_result(
            request_uuid=task_id, request_state="ERROR", xunit_bytes=None
        )
        cache_report_results(ctx, [task_result])

        run_subdir = results_dir_path / run_id
        self.assertFalse(run_subdir.exists() and any(run_subdir.iterdir()))


if __name__ == "__main__":
    unittest.main()
