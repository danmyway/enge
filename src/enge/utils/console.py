"""
Shared Rich console for enge.

Provides a module-level Console instance with a semantic theme
that maps the codebase's color conventions to rich styles.
Call configure_console() after parsing CLI args to adjust for
json/jira output modes.
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

console = Console(theme=ENGE_THEME)


def configure_console(output_format: str) -> None:
    """Reconfigure the shared console for the given output format."""
    global console
    if output_format == "json":
        console = Console(theme=ENGE_THEME, no_color=True, quiet=True)
    elif output_format == "gitlab":
        console = Console(theme=ENGE_THEME, no_color=True, highlight=False)
    else:
        console = Console(theme=ENGE_THEME)


_LEVEL_STYLES = {
    "DEBUG": "dim",
    "VERBOSE": "dim",
    "INFO": "",
    "WARNING": "yellow",
    "ERROR": "red",
    "CRITICAL": "bold red",
}


class EngeLogHandler(logging.Handler):
    """Log handler that colors the entire line (level + message) uniformly.

    Supports per-message style overrides via ``extra={"style": "bold"}``.
    """

    def emit(self, record):
        try:
            msg = self.format(record)
            style = getattr(record, "style", "") or _LEVEL_STYLES.get(
                record.levelname, ""
            )
            level = f"{record.levelname:<8}"
            console.print(
                f"{level} {msg}",
                style=style,
                highlight=False,
                markup=False,
                soft_wrap=True,
            )
        except Exception:
            self.handleError(record)
