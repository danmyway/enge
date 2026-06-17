import unittest
from types import SimpleNamespace
from unittest.mock import patch

from enge.dispatch import tf_send_request


class TestBuildPayload(unittest.TestCase):
    def test_build_payload_normalizes_compose_context(self):
        parsed_stub = SimpleNamespace(
            cli_args=SimpleNamespace(dryrun=False),
            environment_variables={},
            tmt_context={},
            architectures=["x86_64"],
            testing_farm_endpoint=SimpleNamespace(
                log_artifact_baseurl="http://logs", api_endpoint_url="http://api"
            ),
        )
        with patch.object(tf_send_request, "parsed_opts", parsed_stub):
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
            self.assertEqual(context["source_compose"], "AlmaLinux-OS-9.8.20260526")
            self.assertEqual(
                payload["environments"][0]["os"]["compose"],
                "AlmaLinux OS 9.8.20260526 x86_64",
            )


class TestSkipGuestSetup(unittest.TestCase):
    def _make_submit(self, parsed_stub):
        with patch.object(tf_send_request, "parsed_opts", parsed_stub):
            submit = tf_send_request.SubmitTest()
        submit.api_key = "token"
        submit.tests_git_url = "https://example.com/tests.git"
        submit.tests_git_ref = "main"
        submit.plan = "/plans/smoke"
        submit.compose = "RHEL-8-rhui"
        submit.business_unit_tag = "eng"
        submit.tmt_distro = "rhel-8"
        submit.parallel_limit = 1
        submit.set_specific_data(
            architectures=["x86_64"],
            environment_variables={},
            tmt_context={"distro": "rhel-8"},
        )
        return submit

    def _parsed_stub(self):
        return SimpleNamespace(
            cli_args=SimpleNamespace(dryrun=False),
            environment_variables={},
            tmt_context={},
            architectures=["x86_64"],
            testing_farm_endpoint=SimpleNamespace(
                log_artifact_baseurl="http://logs", api_endpoint_url="http://api"
            ),
        )

    def test_skip_guest_setup_in_payload_when_enabled(self):
        stub = self._parsed_stub()
        submit = self._make_submit(stub)
        submit.skip_guest_setup = True
        with patch.object(tf_send_request, "parsed_opts", stub):
            _, payload = submit.build_payload()

        env_settings = payload["environments"][0]["settings"]
        self.assertIn("pipeline", env_settings)
        self.assertTrue(env_settings["pipeline"]["skip_guest_setup"])

    def test_no_pipeline_in_env_settings_when_disabled(self):
        stub = self._parsed_stub()
        submit = self._make_submit(stub)
        submit.skip_guest_setup = False
        with patch.object(tf_send_request, "parsed_opts", stub):
            _, payload = submit.build_payload()

        env_settings = payload["environments"][0]["settings"]
        self.assertNotIn("pipeline", env_settings)

    def test_skip_guest_setup_default_is_false(self):
        with patch.object(
            tf_send_request,
            "parsed_opts",
            self._parsed_stub(),
        ):
            submit = tf_send_request.SubmitTest()
        self.assertFalse(submit.skip_guest_setup)


if __name__ == "__main__":
    unittest.main()
