from types import SimpleNamespace

from enge.rerun.__main__ import _collect_inherited_tags


def test_collect_inherited_tags_from_file_inputs():
    cli_args = SimpleNamespace(
        file=["/tmp/archive/enge_jobs_archive_1.tier0.x86_64"], get_tag=None
    )
    tags = _collect_inherited_tags(
        task_source=None,
        cli_args=cli_args,
        archive_default_path="/tmp/archive",
    )
    assert tags == ["tier0", "x86_64", "rerun"]


def test_collect_inherited_tags_from_get_tag_sources(tmp_path):
    archive_file_names = [
        "enge_jobs_archive_1.pre-release.x86_64.tier0",
        "enge_jobs_archive_1.pre-release.aarch64.tier0",
    ]
    cli_args = SimpleNamespace(file=None, get_tag=["pre-release"])
    tags = _collect_inherited_tags(
        task_source=archive_file_names,
        cli_args=cli_args,
        archive_default_path=str(tmp_path),
    )
    # Tags are deduplicated but order is preserved
    assert tags == ["pre-release", "x86_64", "tier0", "aarch64", "rerun"]


def test_collect_inherited_tags_without_suffix():
    cli_args = SimpleNamespace(file=["/tmp/archive/enge_jobs_archive_1"], get_tag=None)
    tags = _collect_inherited_tags(
        task_source=None,
        cli_args=cli_args,
        archive_default_path="/tmp/archive",
    )
    assert tags == ["rerun"]
