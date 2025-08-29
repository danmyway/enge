import unittest
from unittest.mock import patch, MagicMock

from enge.utils.validators import validate_git_repository, validate_plan_filters
from enge.utils.errors import ValidationError, NetworkError


class TestValidators(unittest.TestCase):
    @patch("enge.utils.validators.http_get")
    def test_validate_git_repository_ok(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_get.return_value = mock_resp
        validate_git_repository("https://example.com/repo.git")

    @patch("enge.utils.validators.http_get")
    def test_validate_git_repository_404(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_get.return_value = mock_resp
        with self.assertRaises(ValidationError):
            validate_git_repository("https://example.com/repo.git")

    @patch("enge.utils.validators.http_get")
    def test_validate_git_repository_network(self, mock_get):
        mock_get.side_effect = Exception("boom")
        with self.assertRaises(Exception):
            validate_git_repository("https://example.com/repo.git")

    def test_validate_plan_filters_empty(self):
        with self.assertRaises(ValidationError):
            validate_plan_filters([], None, None, None, None)

    def test_validate_plan_filters_conflict(self):
        with self.assertRaises(ValidationError):
            validate_plan_filters(
                ["p1", "p2"],
                cli_planfilter="name~p",
                generated_planfilter=None,
                cli_testfilter=None,
                cli_test_name=None,
            )


if __name__ == "__main__":
    unittest.main()
