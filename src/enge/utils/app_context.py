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

    copr_api: Dict[str, Any] = field(default_factory=dict)
    brew_api: Dict[str, Any] = field(default_factory=dict)
    tmt_context: Dict[str, Any] = field(default_factory=dict)
    source_spec: Dict[str, Any] = field(default_factory=dict)
    target_spec: Dict[str, Any] = field(default_factory=dict)
    upgrade_path_alias: str = ""
    architectures: List[str] = field(default_factory=list)
    copr_reference: Optional[str] = None
    brew_reference: Optional[str] = None
    copr_references: List[str] = field(default_factory=list)
    brew_references: List[str] = field(default_factory=list)
    plans: List[str] = field(default_factory=list)
    effective_tiers: Optional[List[str]] = None
    plan_filter: Optional[str] = None
    parallel_limit: Optional[int] = None
    individual_test_sets: Optional[List[Dict[str, Any]]] = None
    environment_variables: Dict[str, str] = field(default_factory=dict)
    pool: Optional[str] = None

    @classmethod
    def from_parsed_opts(cls, po) -> "AppContext":
        derived = {}
        if getattr(po.cli_args, "action", None) == "test":
            from enge.utils.test_attribute_builder import build_test_attributes

            derived = build_test_attributes(po.cli_args, po.config)
        return cls(
            cli_args=po.cli_args,
            config=po.config,
            testing_farm_endpoint=po.testing_farm_endpoint,
            archive_tasks_latest=po.archive_tasks_latest,
            archive_tasks_default=po.archive_tasks_default,
            **derived,
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
