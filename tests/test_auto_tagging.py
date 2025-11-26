from types import SimpleNamespace

from enge.utils.source_target_parser import generate_detailed_upgrade_path_alias


def test_generate_detailed_upgrade_path_alias():
    source = {"major": 9, "minor": 8}
    target = {"major": 10, "minor": 2}
    assert generate_detailed_upgrade_path_alias(source, target) == "98to102"


def test_set_auto_tags_includes_upgrade_path(monkeypatch):
    from enge.dispatch import tf_send_request

    parsed_stub = SimpleNamespace(
        cli_args=SimpleNamespace(set_tag=None, auto_tag=True),
        testing_farm_endpoint=SimpleNamespace(
            log_artifact_baseurl="http://logs", api_endpoint_url="http://api"
        ),
    )
    monkeypatch.setattr(tf_send_request, "parsed_opts", parsed_stub)

    submit = tf_send_request.SubmitTest()
    submit.set_auto_tags(
        set_name="pre-release",
        architecture="x86_64",
        tier="tier0",
        upgrade_path_tag="98to102",
    )

    assert submit.auto_generated_tags[-1] == "98to102"
    assert "pre-release" in ".".join(submit.auto_generated_tags)
