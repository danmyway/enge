from types import SimpleNamespace

from enge.utils.app_context import AppContext
from enge.utils.opt_manager import TestingFarmEndpoint


def make_app_context(
    action="cancel",
    api_key="test-api-key",
    api_endpoint_url="https://tf.example.com/api",
    log_artifact_baseurl="https://tf.example.com/artifacts",
    archive_tasks_latest="/tmp/enge_latest_jobs",
    archive_tasks_default="/tmp/enge_archive",
    extra_cli=None,
    extra_config=None,
    parsed_opts_ref=None,
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
        _parsed_opts_ref=parsed_opts_ref,
    )
