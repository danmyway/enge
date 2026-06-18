from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from enge.utils.opt_manager import TestingFarmEndpoint


@dataclass(frozen=True)
class AppContext:
    cli_args: Any
    config: Dict[str, Any]
    testing_farm_endpoint: TestingFarmEndpoint
    archive_tasks_latest: str
    archive_tasks_default: str
    _parsed_opts_ref: Any = field(default=None, repr=False)

    @classmethod
    def from_parsed_opts(cls, po) -> "AppContext":
        return cls(
            cli_args=po.cli_args,
            config=po.config,
            testing_farm_endpoint=po.testing_farm_endpoint,
            archive_tasks_latest=po.archive_tasks_latest,
            archive_tasks_default=po.archive_tasks_default,
            _parsed_opts_ref=po,
        )

    @property
    def testing_farm(self) -> Dict[str, Any]:
        return self.config["testing_farm"]

    @property
    def project(self) -> Dict[str, Any]:
        return self.config["project"]

    @property
    def common(self) -> Dict[str, Any]:
        return self.config["common"]

    @property
    def tests(self) -> Dict[str, Any]:
        return self.config.get("tests", {})

    @property
    def reportportal(self) -> Dict[str, Any]:
        return self.config.get("reportportal", {})

    # -- Dispatch-required delegation properties --

    @property
    def copr_api(self) -> Dict[str, Any]:
        return getattr(self._parsed_opts_ref, "copr_api", {})

    @property
    def brew_api(self) -> Dict[str, Any]:
        return getattr(self._parsed_opts_ref, "brew_api", {})

    @property
    def tmt_context(self) -> Dict[str, Any]:
        return getattr(self._parsed_opts_ref, "tmt_context", {})

    @property
    def source_spec(self) -> Dict[str, Any]:
        return getattr(self._parsed_opts_ref, "source_spec", {})

    @property
    def target_spec(self) -> Dict[str, Any]:
        return getattr(self._parsed_opts_ref, "target_spec", {})

    @property
    def upgrade_path_alias(self) -> str:
        return getattr(self._parsed_opts_ref, "upgrade_path_alias", "")

    @property
    def architectures(self) -> List[str]:
        return getattr(self._parsed_opts_ref, "architectures", [])

    @property
    def copr_reference(self) -> Optional[str]:
        return getattr(self._parsed_opts_ref, "copr_reference", None)

    @property
    def brew_reference(self) -> Optional[str]:
        return getattr(self._parsed_opts_ref, "brew_reference", None)

    @property
    def copr_references(self) -> List[str]:
        return getattr(self._parsed_opts_ref, "copr_references", [])

    @property
    def brew_references(self) -> List[str]:
        return getattr(self._parsed_opts_ref, "brew_references", [])

    @property
    def plans(self) -> List[str]:
        return getattr(self._parsed_opts_ref, "plans", [])

    @property
    def effective_tiers(self) -> Optional[List[str]]:
        return getattr(self._parsed_opts_ref, "effective_tiers", None)

    @property
    def plan_filter(self) -> Optional[str]:
        return getattr(self._parsed_opts_ref, "plan_filter", None)

    @property
    def parallel_limit(self) -> Optional[int]:
        return getattr(self._parsed_opts_ref, "parallel_limit", None)

    @property
    def individual_test_sets(self) -> Optional[List[Dict[str, Any]]]:
        return getattr(self._parsed_opts_ref, "individual_test_sets", None)

    @property
    def environment_variables(self) -> Dict[str, str]:
        return getattr(self._parsed_opts_ref, "environment_variables", {})

    @property
    def pool(self) -> Optional[str]:
        return getattr(self._parsed_opts_ref, "pool", None)
