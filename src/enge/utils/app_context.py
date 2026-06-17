from dataclasses import dataclass
from typing import Any, Dict

from enge.utils.opt_manager import TestingFarmEndpoint


@dataclass(frozen=True)
class AppContext:
    cli_args: Any
    config: Dict[str, Any]
    testing_farm_endpoint: TestingFarmEndpoint
    archive_tasks_latest: str
    archive_tasks_default: str

    @classmethod
    def from_parsed_opts(cls, po) -> "AppContext":
        return cls(
            cli_args=po.cli_args,
            config=po.config,
            testing_farm_endpoint=po.testing_farm_endpoint,
            archive_tasks_latest=po.archive_tasks_latest,
            archive_tasks_default=po.archive_tasks_default,
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
