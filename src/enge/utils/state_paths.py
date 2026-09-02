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


def results_dir(config: Dict[str, Any]) -> Path:
    """Resolve the results.json/xunit storage directory and ensure it
    exists.

    Follows the same XDG_DATA_HOME + config-override pattern as
    resolve_runs_dir (results.json is sibling data to the run manifests,
    both under the enge XDG_DATA_HOME root) -- not the literal
    `~/.enge/results/` path floated informally elsewhere, which would be
    inconsistent with the manifest store's XDG convention. See
    docs/results-json-schema.md for the full storage-layout contract.

    Unlike the resolve_* functions above, this also creates the directory
    on first call (mkdir -p): results_parser.py's gap-fill writers
    (init_results_json, write_xunit) each take their own output_dir
    explicitly rather than sharing a single flush step, so the resolver
    takes ownership of directory creation instead.
    """
    override = config.get("common", {}).get("results_dir", "")
    if override:
        path = Path(os.path.expanduser(override))
    else:
        path = _xdg("XDG_DATA_HOME", ".local/share") / "enge" / "results"
    path.mkdir(parents=True, exist_ok=True)
    return path
