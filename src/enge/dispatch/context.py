from dataclasses import dataclass
from typing import Any, Dict, Optional

from enge.dispatch.set_flow import RequestSpec


@dataclass
class RequestContext:
    """Everything about one dispatch request — replaces the 14-parameter signatures."""

    spec: RequestSpec
    config: Dict[str, Any]
    cli_args: Any
    api_key: Optional[str]
    event: Optional[str]
    auto_env_vars: Dict[str, str]
    set_env_vars: Dict[str, str]
    cli_env_vars: Dict[str, str]
    set_reportportal_config: Dict[str, Any]
    artifact_type: str

    @property
    def set_name(self) -> Optional[str]:
        return self.spec.set_name

    @property
    def tier(self) -> Optional[str]:
        return self.spec.tier

    @property
    def arch(self) -> str:
        return self.spec.arch

    @property
    def source_spec(self) -> Dict[str, Any]:
        return self.spec.source_spec

    @property
    def target_spec(self) -> Dict[str, Any]:
        return self.spec.target_spec

    @property
    def upgrade_path(self) -> str:
        return self.spec.upgrade_path

    @property
    def effective_values(self) -> Dict[str, Any]:
        return self.spec.effective_values

    @property
    def source_release(self) -> str:
        return f"{self.source_spec['major']}.{self.source_spec['minor']}"

    @property
    def target_release(self) -> str:
        return f"{self.target_spec['major']}.{self.target_spec['minor']}"

    @property
    def source_compose(self) -> str:
        return self.source_spec.get("compose_name", "")

    @property
    def target_compose(self) -> str:
        return self.target_spec.get("compose_name", "")
