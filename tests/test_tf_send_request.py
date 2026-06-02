from types import SimpleNamespace

from enge.dispatch import tf_send_request


def test_build_payload_normalizes_compose_context(monkeypatch):
    parsed_stub = SimpleNamespace(
        cli_args=SimpleNamespace(dryrun=False),
        environment_variables={},
        tmt_context={},
        architectures=["x86_64"],
        testing_farm_endpoint=SimpleNamespace(
            log_artifact_baseurl="http://logs", api_endpoint_url="http://api"
        ),
    )
    monkeypatch.setattr(tf_send_request, "parsed_opts", parsed_stub)

    submit = tf_send_request.SubmitTest()
    submit.api_key = "token"
    submit.tests_git_url = "https://example.com/tests.git"
    submit.tests_git_ref = "main"
    submit.plan = "/plans/smoke"
    submit.compose = "AlmaLinux OS 9.8.20260526 x86_64"
    submit.business_unit_tag = "eng"
    submit.tmt_distro = "alma-9.8"
    submit.parallel_limit = 1
    submit.set_specific_data(
        architectures=["x86_64"],
        environment_variables={},
        tmt_context={
            "distro": "alma-9.8",
            "source_compose": "AlmaLinux OS 9.8.20260526",
            "upgrade_path": "9to10",
        },
    )

    _, payload = submit.build_payload()
    context = payload["environments"][0]["tmt"]["context"]

    assert context["source_compose"] == "AlmaLinux-OS-9.8.20260526"
    assert payload["environments"][0]["os"]["compose"] == (
        "AlmaLinux OS 9.8.20260526 x86_64"
    )
