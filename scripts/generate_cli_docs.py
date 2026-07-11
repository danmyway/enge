#!/usr/bin/env python3
"""Generate docs/cli.md from the enge argparse parser tree.

Stdlib only. Walks the parser tree built by
enge.utils.arg_parser.build_parser() and renders it as Markdown. Prints
to stdout by default; pass --write to update docs/cli.md in place.
"""

import argparse
import sys
from pathlib import Path

from enge.utils.arg_parser import build_parser

DOCS_PATH = Path(__file__).resolve().parent.parent / "docs" / "cli.md"


def _metavar(action: argparse.Action):
    if action.nargs == 0:
        return None
    if action.metavar:
        return action.metavar
    if action.choices:
        return "{" + ",".join(str(c) for c in action.choices) + "}"
    return action.dest.upper()


def _option_header(action: argparse.Action) -> str:
    header = ", ".join(action.option_strings)
    metavar = _metavar(action)
    if metavar:
        header += f" {metavar}"
    return header


def _default_note(action: argparse.Action):
    if action.nargs == 0 or action.default is None:
        return None
    return f" (default: `{action.default}`)"


def _visible_options(parser: argparse.ArgumentParser):
    return [
        action
        for action in parser._actions
        if not isinstance(action, argparse._SubParsersAction)
        and action.help != argparse.SUPPRESS
    ]


def _render_options(
    parser: argparse.ArgumentParser,
    heading_level: int,
    lines: list,
    title: str = "Options",
) -> None:
    options = _visible_options(parser)
    regular = [a for a in options if not (a.help and "Deprecated" in a.help)]
    deprecated = [a for a in options if a.help and "Deprecated" in a.help]

    if regular:
        lines.append(f"{'#' * heading_level} {title}")
        lines.append("")
        for action in regular:
            default = _default_note(action) or ""
            lines.append(f"- `{_option_header(action)}` — {action.help}{default}")
        lines.append("")

    if deprecated:
        lines.append(f"{'#' * heading_level} Deprecated")
        lines.append("")
        for action in deprecated:
            lines.append(f"- `{_option_header(action)}` — {action.help}")
        lines.append("")


def _find_subparsers_action(parser: argparse.ArgumentParser):
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action
    return None


def _subcommand_help(sub_action: argparse._SubParsersAction, name: str):
    return next(
        (c.help for c in sub_action._choices_actions if c.dest == name),
        None,
    )


def _render_subcommand_index(
    sub_action: argparse._SubParsersAction, heading_level: int, lines: list
) -> None:
    lines.append(f"{'#' * heading_level} Subcommands")
    lines.append("")
    for name in sub_action.choices:
        help_text = _subcommand_help(sub_action, name)
        lines.append(f"- `{name}` — {help_text}" if help_text else f"- `{name}`")
    lines.append("")


def _render_command(
    name: str, parser: argparse.ArgumentParser, heading_level: int, lines: list
) -> None:
    lines.append(f"{'#' * heading_level} {name}")
    lines.append("")
    if parser.description:
        lines.append(parser.description)
        lines.append("")

    _render_options(parser, heading_level + 1, lines)

    if parser.epilog:
        lines.append(f"{'#' * (heading_level + 1)} Examples")
        lines.append("")
        lines.append("```")
        lines.append(parser.epilog.rstrip("\n"))
        lines.append("```")
        lines.append("")

    sub_action = _find_subparsers_action(parser)
    if sub_action:
        _render_subcommand_index(sub_action, heading_level + 1, lines)
        for sub_name, sub_parser in sub_action.choices.items():
            _render_command(sub_name, sub_parser, heading_level + 2, lines)


def generate_markdown() -> str:
    parser = build_parser()
    lines = ["# enge CLI Reference", ""]
    if parser.description:
        lines.append(parser.description)
        lines.append("")

    _render_options(parser, 2, lines, title="Global Options")

    sub_action = _find_subparsers_action(parser)
    _render_subcommand_index(sub_action, 2, lines)

    for name, sub_parser in sub_action.choices.items():
        _render_command(name, sub_parser, 2, lines)

    return "\n".join(lines).rstrip("\n") + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Generate enge CLI reference docs.")
    ap.add_argument(
        "--write",
        action="store_true",
        help=f"Write output to {DOCS_PATH} instead of stdout.",
    )
    args = ap.parse_args(argv)

    markdown = generate_markdown()
    if args.write:
        DOCS_PATH.parent.mkdir(parents=True, exist_ok=True)
        DOCS_PATH.write_text(markdown)
    else:
        sys.stdout.write(markdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
