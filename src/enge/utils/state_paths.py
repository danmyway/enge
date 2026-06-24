import os
from pathlib import Path
from typing import Any, Dict


def _xdg(env_var: str, fallback: str) -> Path:
    return Path(os.environ.get(env_var) or Path.home() / fallback)


def resolve_runs_dir(config: Dict[str, Any]) -> Path:
    override = config.get("common", {}).get("manifest_runs_dir", "")
    if override:
        return Path(os.path.expanduser(override))
    return _xdg("XDG_DATA_HOME", ".local/share") / "enge" / "runs"


def resolve_latest_pointer(config: Dict[str, Any]) -> Path:
    override = config.get("common", {}).get("manifest_latest", "")
    if override:
        return Path(os.path.expanduser(override))
    return _xdg("XDG_STATE_HOME", ".local/state") / "enge" / "latest"


def resolve_logs_dir(config: Dict[str, Any]) -> Path:
    override = config.get("common", {}).get("logs_directory", "")
    if override:
        return Path(os.path.expanduser(override))
    return _xdg("XDG_STATE_HOME", ".local/state") / "enge" / "logs"
