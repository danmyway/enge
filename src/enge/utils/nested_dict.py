"""Utilities for manipulating nested dicts/lists using dot-notation key paths.

Key paths use dot-separated segments where numeric segments index into lists
(e.g., ``"environments.0.tmt.context.key"``).
"""

from typing import Any, Dict, Iterable, List


def get_nested_value(data: Any, key_path: str) -> Any:
    """Get a nested value from *data*, supporting dot notation and array indices."""
    current = data
    for key in key_path.split("."):
        if key.isdigit():
            idx = int(key)
            if isinstance(current, list) and 0 <= idx < len(current):
                current = current[idx]
            else:
                return None
        else:
            if isinstance(current, dict) and key in current:
                current = current[key]
            else:
                return None
    return current


def set_nested_key(data: Dict[str, Any], key_path: str, value: Any) -> None:
    """Set a value in *data* at *key_path*, creating intermediate dicts as needed."""
    keys = key_path.split(".")
    current = data

    for i, key in enumerate(keys[:-1]):
        if key.isdigit():
            idx = int(key)
            if isinstance(current, list) and 0 <= idx < len(current):
                current = current[idx]
            else:
                return
        else:
            if key not in current or not isinstance(current[key], (dict, list)):
                if i + 1 < len(keys) - 1 and not keys[i + 1].isdigit():
                    current[key] = {}
                else:
                    return
            current = current[key]

    final_key = keys[-1]
    if isinstance(current, dict):
        current[final_key] = value


def drop_nested_key(data: Dict[str, Any], key_path: str) -> None:
    """Remove the key at *key_path* from *data* if it exists."""
    keys = key_path.split(".")
    current = data

    for key in keys[:-1]:
        if key.isdigit():
            idx = int(key)
            if isinstance(current, list) and 0 <= idx < len(current):
                current = current[idx]
            else:
                return
        else:
            if isinstance(current, dict) and key in current:
                current = current[key]
            else:
                return

    final_key = keys[-1]
    if isinstance(current, dict):
        current.pop(final_key, None)


def match_keys(keys: Iterable[str], pattern: str) -> List[str]:
    """Return *keys* matching *pattern* (supports ``*`` wildcards).

    - ``"PREFIX_*"`` – prefix match
    - ``"*_SUFFIX"`` – suffix match
    - ``"*MIDDLE*"`` – contains match
    - ``"exact"`` – exact match
    """
    if not pattern:
        return []

    if pattern.startswith("*") and pattern.endswith("*"):
        substring = pattern[1:-1]
        return [k for k in keys if substring in k]
    if pattern.startswith("*"):
        suffix = pattern[1:]
        return [k for k in keys if k.endswith(suffix)]
    if pattern.endswith("*"):
        prefix = pattern[:-1]
        return [k for k in keys if k.startswith(prefix)]
    return [k for k in keys if k == pattern]
