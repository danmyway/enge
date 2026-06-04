"""
Shared Rich console for enge.

Provides a module-level Console proxy with a semantic theme.
The proxy delegates attribute access to a mutable backing instance
so that ``from enge.utils.console import console`` always reflects
the current configuration — even when configure_console() is called
after the import.

Logging always goes to stderr via a dedicated Console so that
``-o json`` can silence stdout without losing diagnostics.
"""

import logging

from rich.console import Console
from rich.theme import Theme

ENGE_THEME = Theme(
    {
        # Semantic styles for console.print
        "success": "green",
        "error": "bold red",
        "warning": "yellow",
        "dim": "dim",
        "emphasis": "bold",
        # Task state badges
        "state.complete": "green",
        "state.queued": "dim",
        "state.running": "cyan",
        "state.error": "yellow",
        "state.canceled": "dim",
    }
)

_current = Console(theme=ENGE_THEME)

_log_console = Console(stderr=True, theme=ENGE_THEME)


class _ConsoleProxy:
    """Thin proxy so import-by-value bindings track reconfiguration."""

    def __getattr__(self, name):
        return getattr(_current, name)


console = _ConsoleProxy()


def configure_console(output_format: str) -> None:
    """Reconfigure the shared console for the given output format."""
    global _current
    if output_format == "json":
        _current = Console(theme=ENGE_THEME, no_color=True, quiet=True)
    elif output_format == "gitlab":
        _current = Console(theme=ENGE_THEME, no_color=True, highlight=False)
    else:
        _current = Console(theme=ENGE_THEME)


_LEVEL_STYLES = {
    "DEBUG": "dim",
    "VERBOSE": "dim",
    "INFO": "",
    "WARNING": "yellow",
    "ERROR": "red",
    "CRITICAL": "bold red",
}


class EngeLogHandler(logging.Handler):
    """Log handler that renders to stderr so stdout stays clean for data."""

    def emit(self, record):
        try:
            msg = self.format(record)
            style = getattr(record, "style", "") or _LEVEL_STYLES.get(
                record.levelname, ""
            )
            level = f"{record.levelname:<8}"
            _log_console.print(
                f"{level} {msg}",
                style=style,
                highlight=False,
                markup=False,
                soft_wrap=True,
            )
        except Exception:
            self.handleError(record)
