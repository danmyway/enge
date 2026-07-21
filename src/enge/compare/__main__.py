"""`enge compare` subcommand: consolidate or flakiness-compare results.json
caches across runs.

Read-only consumer of the results.json contract -- never parses xunit,
never calls Testing Farm, never writes a cache (see CLAUDE.md "Results.json
format"). Grouping/consolidation is delegated entirely to
`enge.compare.engine` (pure) via `enge.compare.loader` (I/O); this module
is display only: turning `engine.ComparisonTable` objects into rich
tables/footers and deriving the process exit code.
"""

import logging

from rich import box
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from enge.compare import engine
from enge.compare.loader import load_columns
from enge.report.__main__ import colorize
from enge.utils.app_context import AppContext
from enge.utils.console import console
from enge.utils.globals import ExitCode

LOGGER = logging.getLogger(__name__)

_UPGRADE_ARROW = "→"


def _column_header(table: "engine.ComparisonTable", index: int) -> str:
    if table.mode == "flakiness":
        col = table.columns[index]
        return f"{col.arch} {col.source}{_UPGRADE_ARROW}{col.target} ({index + 1})"
    return f"({index + 1})"


def _render_table(table: "engine.ComparisonTable") -> Table:
    title = f"tier: {table.tier}"
    if table.mode == "consolidation":
        title = f"{table.arch} {table.source}{_UPGRADE_ARROW}{table.target} | {title}"

    rich_table = Table(box=box.ROUNDED, title=title)
    rich_table.add_column("Name", justify="left")
    for i in range(len(table.columns)):
        rich_table.add_column(_column_header(table, i), justify="left")
    if table.mode == "consolidation":
        rich_table.add_column("Consolidated", justify="left")

    current_plan = None
    blank_padding = len(table.columns) + (1 if table.mode == "consolidation" else 0)
    for row in table.rows:
        if row.plan_label is not None and row.plan_label != current_plan:
            current_plan = row.plan_label
            rich_table.add_row(escape(current_plan), *([""] * blank_padding))

        label = row.label if row.plan_label is None else f"{'*' * 4} {row.label}"
        cells = [
            colorize(v) if v != engine.ABSENT else engine.ABSENT for v in row.per_column
        ]
        row_cells = [escape(label), *cells]
        if table.mode == "consolidation":
            row_cells.append(colorize(row.consolidated) if row.consolidated else "")
        rich_table.add_row(*row_cells)

    return rich_table


def _footer_entries(table: "engine.ComparisonTable"):
    for index, col in enumerate(table.columns, start=1):
        yield {
            "index": index,
            "run_id": col.run_id,
            "task_id": col.task_id,
            "arch": col.arch,
            "path": f"{col.source}{_UPGRADE_ARROW}{col.target}",
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
    flakiness = getattr(ctx.cli_args, "flakiness", False)

    columns, error_code = load_columns(ctx, flakiness=flakiness)
    if error_code is not None:
        if flakiness:
            LOGGER.error(
                "compare: no comparable result columns for this selector; "
                "nothing to compare."
            )
        else:
            LOGGER.error(
                "compare: fewer than 2 result sources with a usable results "
                "cache for this selector; nothing to compare."
            )
        return error_code

    if flakiness:
        tables = engine.build_flakiness_tables(columns, show_tests=show_tests)
        retval = ExitCode.SUCCESS
    else:
        tables = engine.build_consolidation_tables(columns, show_tests=show_tests)
        retval = engine.derive_exit_code(tables)

    has_content = False
    for table in tables:
        if not table.rows:
            continue
        rich_table = _render_table(table)

        console.print()
        console.print("~~~ COMPARE RESULT ~~~~~~~~~~~~~~~~", style="dim")
        _print_table(ctx, rich_table)

        console.print()
        console.print("~~~ TASK REFERENCE ~~~~~~~~~~~~~~~~", style="dim")
        for entry in _footer_entries(table):
            console.print(
                f"({entry['index']}) {entry['arch']} {entry['path']} "
                f"set={entry['set']} run={entry['run_id']} "
                f"task={entry['task_id']} {entry['artifacts_url'] or ''}",
                style="dim",
            )
        has_content = True

    if not has_content:
        LOGGER.info("Nothing to compare!")

    return retval
