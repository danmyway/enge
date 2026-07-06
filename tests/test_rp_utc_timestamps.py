"""Verify RP timestamp sites emit genuine UTC, not local time labeled as UTC."""

import unittest
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

from tests._helpers import make_app_context

FROZEN_DT = datetime(2026, 7, 6, 15, 30, 45, 123456, tzinfo=timezone.utc)
FROZEN_ISO_MS = "2026-07-06T15:30:45.123Z"
FROZEN_EPOCH_MS = str(int(FROZEN_DT.timestamp() * 1000))


def _rp_launch():
    from enge.reportportal.__main__ import ReportPortalLaunch

    ctx = make_app_context(
        action="reportportal",
        extra_config={
            "reportportal": {
                "url": "http://rp.example.com",
                "token": "fake-token",
                "project": "test-project",
            }
        },
    )
    return ReportPortalLaunch(ctx)


class TestConvertToIsoFormatFallbackUTC(unittest.TestCase):
    """reportportal/utils.py — convert_to_iso_format unparseable fallback."""

    def test_fallback_is_utc(self):
        mock_dt = MagicMock(wraps=datetime)
        mock_dt.now.return_value = FROZEN_DT
        with patch("enge.reportportal.utils.datetime", mock_dt):
            from enge.reportportal.utils import convert_to_iso_format

            result = convert_to_iso_format("totally-not-a-date")
        self.assertEqual(result, FROZEN_ISO_MS)


class TestBuildLaunchesEndTimeFallbackUTC(unittest.TestCase):
    """reportportal/operations.py:339 — end_time fallback in build_launches_from_tasks."""

    def test_end_time_fallback_is_utc(self):
        from enge.reportportal.operations import resolve_from_tasks

        mock_dt = MagicMock(wraps=datetime)
        mock_dt.now.return_value = FROZEN_DT

        task_result = MagicMock()
        task_result.request_uuid = "aabb"
        task_result.request_state = "complete"
        task_result.xunit_content = None
        task_result.results_xml_url = None

        mock_parser = MagicMock()
        mock_parser.__enter__ = MagicMock(return_value=mock_parser)
        mock_parser.__exit__ = MagicMock(return_value=False)
        mock_parser._fetch_task_info.return_value = task_result
        mock_parser._fetch_xml_results.return_value = task_result

        tmt_ctx = {"tier": "tier0", "architecture": "x86_64", "uniq_id": "aabb"}

        rp = MagicMock()
        rp.find_launch_by_uniq_id.return_value = "launch-uuid-1"
        rp.ctx = make_app_context(action="reportportal")

        with (
            patch("enge.reportportal.operations.datetime", mock_dt),
            patch(
                "enge.reportportal.operations.parse_tasks",
                return_value=(["http://task1"], "cli"),
            ),
            patch(
                "enge.reportportal.operations.ConcurrentRequestParser",
                return_value=mock_parser,
            ),
            patch(
                "enge.reportportal.operations.extract_tmt_context_from_task",
                return_value=tmt_ctx,
            ),
        ):
            launches = resolve_from_tasks(rp, rp.ctx)

        self.assertEqual(len(launches), 1)
        self.assertEqual(launches[0]["_end_time"], FROZEN_ISO_MS)


class TestNormalizeForFinishFallbackUTC(unittest.TestCase):
    """reportportal/operations.py:727 — fallback_time in normalize_for_finish."""

    def test_fallback_time_is_utc(self):
        from enge.reportportal.operations import normalize_for_finish

        mock_dt = MagicMock(wraps=datetime)
        mock_dt.now.return_value = FROZEN_DT

        rp = MagicMock()
        rp.ctx = make_app_context(action="reportportal")

        items = [{"status": "PASSED", "type": "STEP"}]
        rp.get_launch_test_items.return_value = items

        raw = [{"_launch_uuid": "uuid-1", "name": "Test", "id": 101}]

        with (
            patch("enge.reportportal.operations.datetime", mock_dt),
            patch(
                "enge.reportportal.operations.has_in_progress_items", return_value=False
            ),
            patch(
                "enge.reportportal.operations.extract_artifacts_url_from_items",
                return_value=None,
            ),
            patch(
                "enge.reportportal.operations.latest_end_time_from_items",
                return_value=None,
            ),
            patch(
                "enge.reportportal.operations._derive_status_from_tf_or_items",
                return_value="PASSED",
            ),
        ):
            result = normalize_for_finish(rp, raw)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["_end_time"], FROZEN_ISO_MS)


class TestGenerateLaunchPayloadUTC(unittest.TestCase):
    """reportportal/__main__.py — description isoformat + startTime epoch ms."""

    def test_description_contains_utc_isoformat(self):
        mock_dt = MagicMock(wraps=datetime)
        mock_dt.now.return_value = FROZEN_DT
        with patch("enge.reportportal.__main__.datetime", mock_dt):
            rp = _rp_launch()
            payload = rp.generate_launch_payload(name="TEST")
        self.assertIn(FROZEN_DT.isoformat(), payload["description"])

    def test_start_time_is_utc_epoch_ms(self):
        mock_dt = MagicMock(wraps=datetime)
        mock_dt.now.return_value = FROZEN_DT
        with patch("enge.reportportal.__main__.datetime", mock_dt):
            rp = _rp_launch()
            payload = rp.generate_launch_payload(name="TEST")
        self.assertEqual(payload["startTime"], int(FROZEN_DT.timestamp() * 1000))


class TestLogUploadTimeUTC(unittest.TestCase):
    """reportportal/__main__.py:673 — log entry 'time' field."""

    def test_log_time_is_utc_epoch_ms(self):
        import json

        mock_dt = MagicMock(wraps=datetime)
        mock_dt.now.return_value = FROZEN_DT

        artifact = MagicMock()
        artifact.name = "test.log"
        artifact.url = "http://example.com/test.log"
        artifact.relative_path = "test.log"

        mapped_artifacts = [(artifact, "item-uuid-1")]

        with (
            patch("enge.reportportal.__main__.datetime", mock_dt),
            patch("enge.reportportal.__main__.http_post") as mock_post,
            patch("enge.reportportal.__main__.http_get") as mock_get,
            patch(
                "enge.reportportal.__main__.should_skip_artifact", return_value=False
            ),
            patch(
                "enge.reportportal.__main__.get_artifact_log_level", return_value="INFO"
            ),
        ):
            mock_get.return_value = MagicMock(
                status_code=200,
                text="log content here",
                headers={"content-length": "16"},
            )
            mock_post.return_value = MagicMock(status_code=200)
            rp = _rp_launch()
            rp.upload_logs_to_rp("launch-uuid-1", mapped_artifacts)

        self.assertTrue(mock_post.called)
        files_arg = mock_post.call_args[1].get("files", [])
        json_part = files_arg[0][1][1]
        posted_body = json.loads(json_part)
        self.assertTrue(len(posted_body) > 0)
        self.assertEqual(posted_body[0]["time"], FROZEN_EPOCH_MS)


if __name__ == "__main__":
    unittest.main()
