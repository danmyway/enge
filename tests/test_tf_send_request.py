import unittest
from types import SimpleNamespace

from enge.dispatch import tf_send_request


class TestBuildPayload(unittest.TestCase):
    def test_build_payload_normalizes_compose_context(self):
        ctx = SimpleNamespace(
            cli_args=SimpleNamespace(dryrun=False),
            environment_variables={},
            tmt_context={},
            architectures=["x86_64"],
            pool=None,
            testing_farm_endpoint=SimpleNamespace(
                log_artifact_baseurl="http://logs", api_endpoint_url="http://api"
            ),
        )
        submit = tf_send_request.SubmitTest(ctx)
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
    def _make_submit(self, ctx):
        submit = tf_send_request.SubmitTest(ctx)
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

    def _make_ctx(self):
        return SimpleNamespace(
            cli_args=SimpleNamespace(dryrun=False),
            environment_variables={},
            tmt_context={},
            architectures=["x86_64"],
            pool=None,
            testing_farm_endpoint=SimpleNamespace(
                log_artifact_baseurl="http://logs", api_endpoint_url="http://api"
            ),
        )

    def test_skip_guest_setup_in_payload_when_enabled(self):
        ctx = self._make_ctx()
        submit = self._make_submit(ctx)
        submit.skip_guest_setup = True
        _, payload = submit.build_payload()

        env_settings = payload["environments"][0]["settings"]
        self.assertIn("pipeline", env_settings)
        self.assertTrue(env_settings["pipeline"]["skip_guest_setup"])

    def test_no_pipeline_in_env_settings_when_disabled(self):
        ctx = self._make_ctx()
        submit = self._make_submit(ctx)
        submit.skip_guest_setup = False
        _, payload = submit.build_payload()

        env_settings = payload["environments"][0]["settings"]
        self.assertNotIn("pipeline", env_settings)

    def test_skip_guest_setup_default_is_false(self):
        ctx = self._make_ctx()
        submit = tf_send_request.SubmitTest(ctx)
        self.assertFalse(submit.skip_guest_setup)


if __name__ == "__main__":
    unittest.main()
