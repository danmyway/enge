"""`enge compare` subcommand: unified comparison + consolidation view over
results.json caches across runs.

Read-only consumer of the results.json contract -- never parses xunit,
never calls Testing Farm, never writes a cache (see CLAUDE.md "Results.json
format"). Grouping/consolidation is delegated entirely to
`enge.compare.engine` (pure) via `enge.compare.loader` (I/O); this module
is display only: turning `engine.ComparisonTable` objects into rich
tables/footers. The exit code is always SUCCESS once the floor is met
(R4) -- table content (FAILED/ERROR rows) never changes the retval;
CONFIG_ERROR is reserved for the loader's floor failure, which is not a
comparison result.
"""

import logging

from rich import box
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from enge.compare import engine
from enge.compare.loader import load_columns
from enge.report.__main__ import colorize, _short_name
from enge.utils.app_context import AppContext
from enge.utils.console import console
from enge.utils.globals import ExitCode

LOGGER = logging.getLogger(__name__)

_UPGRADE_ARROW = "→"


def _descriptor(value):
    return value if value is not None else engine.ABSENT


def _table_title(table: "engine.ComparisonTable") -> str:
    parts = []
    if table.arch is not None:
        parts.append(table.arch)
    if table.source is not None or table.target is not None:
        parts.append(
            f"{_descriptor(table.source)}{_UPGRADE_ARROW}{_descriptor(table.target)}"
        )
    parts.append(f"tier: {table.tier}" if table.tier is not None else "tier: untiered")
    return " | ".join(parts)


def _column_header(table: "engine.ComparisonTable", index: int) -> str:
    col = table.columns[index]
    parts = []
    if table.arch is None:
        parts.append(col.arch)
    if table.source is None and table.target is None:
        parts.append(
            f"{_descriptor(col.source)}{_UPGRADE_ARROW}{_descriptor(col.target)}"
        )
    parts.append(f"({index + 1})")
    return "\n".join(parts)


def _render_table(table: "engine.ComparisonTable", *, short: bool) -> Table:
    rich_table = Table(box=box.ROUNDED, title=_table_title(table))
    rich_table.add_column("Name", justify="left")
    for i in range(len(table.columns)):
        rich_table.add_column(_column_header(table, i), justify="left")
    rich_table.add_column("Consolidated", justify="left")

    def _label(name):
        return _short_name(name) if short else name

    current_plan = None
    blank_padding = len(table.columns) + 1
    for row in table.rows:
        if row.plan_label is not None and row.plan_label != current_plan:
            current_plan = row.plan_label
            rich_table.add_row(escape(_label(current_plan)), *([""] * blank_padding))

        display = _label(row.label)
        label = display if row.plan_label is None else f"{'*' * 4} {display}"
        cells = [
            colorize(v) if v != engine.ABSENT else engine.ABSENT for v in row.per_column
        ]
        row_cells = [escape(label), *cells, colorize(row.consolidated)]
        rich_table.add_row(*row_cells)

    return rich_table


def _footer_entries(table: "engine.ComparisonTable"):
    for index, col in enumerate(table.columns, start=1):
        yield {
            "index": index,
            "run_id": col.run_id,
            "task_id": col.task_id,
            "arch": col.arch,
            "path": f"{_descriptor(col.source)}{_UPGRADE_ARROW}{_descriptor(col.target)}",
            "set": col.set,
            "artifacts_url": col.artifacts_url,
        }


def _print_table(ctx: AppContext, rich_table: Table) -> None:
    output_fmt = getattr(ctx.cli_args, "output_format", "terminal")
    jira_mode = getattr(ctx.cli_args, "jira", False)
    if jira_mode:
        plain = Console(no_color=True, highlight=False)
        print("{noformat}")
        plain.print(rich_table)
        print("{noformat}")
    elif output_fmt == "gitlab":
        plain = Console(no_color=True, highlight=False)
        print("```")
        plain.print(rich_table)
        print("```")
    else:
        console.print(rich_table)


def main(ctx: AppContext) -> int:
    show_tests = getattr(ctx.cli_args, "show_tests", False)
    splitarch = getattr(ctx.cli_args, "splitarch", False)
    splitpath = getattr(ctx.cli_args, "splitpath", False)
    short = not bool(getattr(ctx.cli_args, "long", False))

    columns, error_code = load_columns(ctx)
    if error_code is not None:
        LOGGER.error(
            "compare: no comparable result columns for this selector; "
            "nothing to compare."
        )
        return error_code

    tables = engine.build_tables(
        columns, show_tests=show_tests, splitarch=splitarch, splitpath=splitpath
    )

    has_content = False
    for table in tables:
        if not table.rows:
            continue
        rich_table = _render_table(table, short=short)

        console.print()
        console.print("~~~ COMPARE RESULT ~~~~~~~~~~~~~~~~", style="dim")
        _print_table(ctx, rich_table)

        console.print()
        console.print("~~~ TASK REFERENCE ~~~~~~~~~~~~~~~~", style="dim")
        for entry in _footer_entries(table):
            console.print(
                f"({entry['index']}) {entry['arch']} {entry['path']} "
                f"run={entry['run_id']} {entry['artifacts_url'] or ''}",
                style="dim",
            )
        has_content = True

    if not has_content:
        LOGGER.info("Nothing to compare!")

    return ExitCode.SUCCESS
