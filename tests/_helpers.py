import contextlib
import logging
from types import SimpleNamespace

from enge.utils.app_context import AppContext
from enge.utils.opt_manager import TestingFarmEndpoint


class _ListHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(record)


@contextlib.contextmanager
def captured_logs(logger_name, level=logging.DEBUG):
    """Record every record a logger emits, including none at all.

    unittest's assertLogs() FAILS when no record is emitted, which makes it
    unusable for asserting that a particular WARNING did *not* fire -- the
    code paths under test routinely emit other, unrelated records, or none.

    Whole LogRecords are kept, not rendered messages, so a caller can pin the
    level a line is emitted at: a WARNING demoted to DEBUG still carries the
    same text, and a test that only looks at text cannot tell the two apart.
    """
    logger = logging.getLogger(logger_name)
    handler = _ListHandler()
    previous_level = logger.level
    logger.addHandler(handler)
    logger.setLevel(level)
    try:
        yield handler.records
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous_level)


def matching(records, needle, *, level=None):
    """The recorded messages containing `needle`.

    `level` (a logging level number) additionally restricts the match to
    records emitted at exactly that level; leave it None to match a message
    at any level, which is what a "this never fires" assertion wants.
    """
    return [
        record.getMessage()
        for record in records
        if needle in record.getMessage() and (level is None or record.levelno == level)
    ]


def make_app_context(
    action="cancel",
    api_key="test-api-key",
    api_endpoint_url="https://tf.example.com/api",
    log_artifact_baseurl="https://tf.example.com/artifacts",
    archive_tasks_latest="/tmp/enge_latest_jobs",
    archive_tasks_default="/tmp/enge_archive",
    extra_cli=None,
    extra_config=None,
    **ctx_overrides,
):
    cli_attrs = {
        "action": action,
        "debug": False,
        "dryrun": False,
        "verbose": 0,
        "file": None,
        "input": None,
        "get_tag": [],
    }
    if extra_cli:
        cli_attrs.update(extra_cli)
    cli_args = SimpleNamespace(**cli_attrs)

    config = {
        "testing_farm": {
            "api_key": api_key,
            "api_endpoint_url": api_endpoint_url,
            "log_artifact_baseurl": log_artifact_baseurl,
            "cloud_resources_tag": "test",
            "composes_prod_url": "https://composes.example.com",
        },
        "common": {
            "archive_tasks_latest": archive_tasks_latest,
            "archive_tasks_default": archive_tasks_default,
            "logs_directory": "/tmp/enge_logs",
        },
        "project": {},
        "tests": {},
        "reportportal": {},
    }
    if extra_config:
        for section, values in extra_config.items():
            config.setdefault(section, {}).update(values)

    endpoint = TestingFarmEndpoint(api_endpoint_url, log_artifact_baseurl)

    return AppContext(
        cli_args=cli_args,
        config=config,
        testing_farm_endpoint=endpoint,
        archive_tasks_latest=archive_tasks_latest,
        archive_tasks_default=archive_tasks_default,
        manifest_runs_dir=ctx_overrides.pop("manifest_runs_dir", "/tmp/enge_test_runs"),
        manifest_latest=ctx_overrides.pop("manifest_latest", "/tmp/enge_test_latest"),
        **ctx_overrides,
    )
