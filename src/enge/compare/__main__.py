"""`enge compare` subcommand: unified comparison + consolidation view over
results.json caches across runs.

Read-only consumer of the results.json contract -- never parses xunit,
never calls Testing Farm, never writes a cache (see
docs/results-json-schema.md). Grouping/consolidation is delegated entirely to
`enge.compare.engine` (pure) via `enge.compare.loader` (I/O); this module
is display only: turning `engine.ComparisonTable` objects into rich
tables/footers. The exit code is always SUCCESS once the floor is met
(R4) -- table content (FAILED/ERROR rows) never changes the retval;
CONFIG_ERROR is reserved for the loader's floor failure, which is not a
comparison result.

`--lineage` (item 5.2, contract v3 §5a) swaps in the lineage loader and
engine and renders one column per rerun lineage; `--retry-count` only
exists with it. Normal-mode output is unchanged by either flag.
"""

import logging
from typing import Any, List, Optional, Union

from rich import box
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from enge.compare import engine
from enge.compare.loader import load_columns, load_lineage_columns
from enge.lineage import VERDICT_INITIALS
from enge.report.__main__ import colorize, _short_name
from enge.utils.app_context import AppContext
from enge.utils.console import console
from enge.utils.errors import ValidationError
from enge.utils.globals import ExitCode

LOGGER = logging.getLogger(__name__)

_UPGRADE_ARROW = "→"

_AnyTable = Union["engine.ComparisonTable", "engine.LineageTable"]

LINEAGE_LEGEND = (
    "Legend: P passed, F failed, E error or unparseable, S skipped, "
    "C canceled, . covered without a result, — not covered; [X] exhausted, "
    "[?] unparseable twice, [-] canceled (terminal)"
)

# Selectors lineage mode rejects, as (argparse dest, flag), in message order.
_LINEAGE_FORBIDDEN_SELECTORS = (
    ("filter_set", "--set"),
    ("filter_tier", "--tier"),
    ("filter_arch", "--arch"),
    ("filter_tag", "--tag"),
    ("since", "--since"),
    ("until", "--until"),
)

_INITIAL_VERDICTS = {initial: verdict for verdict, initial in VERDICT_INITIALS.items()}


def _descriptor(value):
    return value if value is not None else engine.ABSENT


def _table_title(table: _AnyTable) -> str:
    parts = []
    if table.arch is not None:
        parts.append(table.arch)
    if table.source is not None or table.target is not None:
        parts.append(
            f"{_descriptor(table.source)}{_UPGRADE_ARROW}{_descriptor(table.target)}"
        )
    parts.append(f"tier: {table.tier}" if table.tier is not None else "tier: untiered")
    return " | ".join(parts)


def _column_header(table: _AnyTable, index: int) -> str:
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
        label = display if row.plan_label is None else f"{' ' * 4}{display}"
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


def _validate_lineage_args(cli_args: Any) -> None:
    """Reject flag combinations before anything is loaded (exit 2).

    Both flags use `default=argparse.SUPPRESS`, so they are read with
    `getattr` and are absent from a namespace unless given.
    """
    lineage = getattr(cli_args, "lineage", False)
    retry_count = getattr(cli_args, "retry_count", None)
    if retry_count is not None and not lineage:
        raise ValidationError("--retry-count is only valid with --lineage")
    if retry_count is not None and retry_count < 0:
        raise ValidationError("--retry-count must be an integer >= 0")
    if lineage:
        offending = [
            flag
            for dest, flag in _LINEAGE_FORBIDDEN_SELECTORS
            if getattr(cli_args, dest, None)
        ]
        if offending:
            raise ValidationError(
                "--lineage selects runs with --run only; drop " + ", ".join(offending)
            )


def _lineage_caption(retry_count: Optional[int]) -> str:
    if retry_count is None:
        return (
            "lineage view; no retry policy given, so [X] (exhausted) is not evaluated"
        )
    return f"lineage view; retry policy: --retry-count {retry_count}"


def _lineage_cell_markup(cell: "engine.LineageCell") -> str:
    """Colored by the verdict of the last generation; a cell ending in "."
    has no verdict and stays uncolored. `colorize` escapes the text."""
    verdict = _INITIAL_VERDICTS.get(cell.values[-1])
    if verdict is None:
        return escape(cell.text)
    return colorize(verdict, label=cell.text)


def _render_lineage_table(
    table: "engine.LineageTable", *, short: bool, retry_count: Optional[int]
) -> Table:
    rich_table = Table(
        box=box.ROUNDED,
        title=_table_title(table),
        caption=escape(_lineage_caption(retry_count)),
    )
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
        label = display if row.plan_label is None else f"{' ' * 4}{display}"
        cells = [
            _lineage_cell_markup(cell) if cell is not None else engine.ABSENT
            for cell in row.cells
        ]
        rich_table.add_row(escape(label), *cells, colorize(row.consolidated))

    return rich_table


def _lineage_footer_lines(table: "engine.LineageTable") -> List[str]:
    """One TASK REFERENCE line per lineage column (no `run=` field).

    `({i}) {arch} {source}→{target}`, then `gen{G}={artifacts_url}` for
    every task of the lineage in lineage order (two tasks of one generation
    both appear). When the lineage's source composes are not all equal,
    ` composes: ` follows with `gen{G}={source_compose}` for every task.
    """
    lines = []
    for index, (col, lineage) in enumerate(
        zip(table.columns, table.generations), start=1
    ):
        parts = [
            f"({index}) {col.arch} "
            f"{col.source or engine.ABSENT}{_UPGRADE_ARROW}{col.target or engine.ABSENT}"
        ]
        parts.extend(f"gen{gen}={task.artifacts_url or ''}" for gen, task in lineage)
        if len({task.source_compose for _, task in lineage}) > 1:
            parts.append("composes:")
            parts.extend(
                f"gen{gen}={_descriptor(task.source_compose)}" for gen, task in lineage
            )
        lines.append(" ".join(parts))
    return lines


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


def _main_lineage(
    ctx: AppContext, *, show_tests: bool, splitarch: bool, splitpath: bool, short: bool
) -> int:
    retry_count = getattr(ctx.cli_args, "retry_count", None)

    columns, selected, error_code = load_lineage_columns(ctx)
    if error_code is not None:
        LOGGER.error(
            "compare: no comparable result columns for this selector; "
            "nothing to compare."
        )
        return error_code

    tables = engine.build_lineage_tables(
        columns,
        selected,
        show_tests=show_tests,
        splitarch=splitarch,
        splitpath=splitpath,
        retry_count=retry_count,
    )

    has_content = False
    for table in tables:
        if not table.rows:
            continue
        rich_table = _render_lineage_table(table, short=short, retry_count=retry_count)

        console.print()
        console.print("~~~ COMPARE RESULT ~~~~~~~~~~~~~~~~", style="dim")
        _print_table(ctx, rich_table)
        console.print(escape(LINEAGE_LEGEND), style="dim")

        console.print()
        console.print("~~~ TASK REFERENCE ~~~~~~~~~~~~~~~~", style="dim")
        for line in _lineage_footer_lines(table):
            console.print(escape(line), style="dim")
        has_content = True

    if not has_content:
        LOGGER.info("Nothing to compare!")

    return ExitCode.SUCCESS


def main(ctx: AppContext) -> int:
    _validate_lineage_args(ctx.cli_args)

    show_tests = getattr(ctx.cli_args, "show_tests", False)
    splitarch = getattr(ctx.cli_args, "splitarch", False)
    splitpath = getattr(ctx.cli_args, "splitpath", False)
    short = not bool(getattr(ctx.cli_args, "long", False))

    if getattr(ctx.cli_args, "lineage", False):
        return _main_lineage(
            ctx,
            show_tests=show_tests,
            splitarch=splitarch,
            splitpath=splitpath,
            short=short,
        )

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
