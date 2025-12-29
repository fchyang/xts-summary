#!/usr/bin/env python
"""Command‑line interface for the *diff_tool* package.

The tool compares ``testdetails`` tables extracted from two HTML sources –
either local files or remote URLs – and produces an HTML diff report.

Key features added:
* Full type‑hints and docstrings for better IDE support.
* Robust URL handling via :pymod:`requests`.
* Structured logging instead of ``print`` statements.
* ``--verbose`` flag for debug output.
* A ``main(argv: list[str] | None = None)`` signature that makes the entry
  point easily testable.
"""

import argparse
import logging
from pathlib import Path
from typing import List



from .extractor import extract_testdetails
from .comparer import compare_tables, _table_to_df
from .html_report import generate_report

log = logging.getLogger(__name__)



def build_parser() -> argparse.ArgumentParser:
    """Create and return the argument parser used by the CLI."""
    parser = argparse.ArgumentParser(
        description="Compare 'testdetails' tables from two HTML sources."
    )
    parser.add_argument("left", help="Path or URL of the left HTML file")
    parser.add_argument("right", help="Path or URL of the right HTML file")
    parser.add_argument(
        "-o",
        "--output",
        default="diff.html",
        help="Output HTML report file (default: %(default)s)",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable verbose (debug) logging",
    )

    return parser


def main(argv: List[str] | None = None) -> None:
    """Entry point for the ``diff_tool`` CLI.

    Parameters
    ----------
    argv:
        Optional list of argument strings.  If ``None`` (the default) the
        arguments are taken from ``sys.argv`` as usual.  Supplying a list makes
        the function convenient to call from tests.
    """
    parser = build_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
    )

    # Directly extract tables and titles from the provided sources.
    # ``extract_testdetails`` handles loading from both local files and URLs.
    left_title, left_tables = extract_testdetails(args.left)
    right_title, right_tables = extract_testdetails(args.right)

    # If either side lacks 'testdetails' tables, we still generate a report –
    # the diff will simply contain no testdetail tables.
    if not left_tables or not right_tables:
        log.warning("Could not find a 'testdetails' table in one of the inputs; proceeding without testdetail diff.")
        # Continue with empty lists so generate_report can render summaries only.
        left_tables = left_tables or []
        right_tables = right_tables or []

    # Convert each extracted table to a DataFrame and compute diffs
    left_dfs, right_dfs, diffs = [], [], []
    for lt, rt in zip(left_tables, right_tables):
        left_df, right_df, diff_df = compare_tables(lt, rt)
        left_dfs.append(left_df)
        right_dfs.append(right_df)
        diffs.append(diff_df)

    # Preserve any extra tables that appear only on one side
    if len(left_tables) > len(right_tables):
        left_dfs.extend(_table_to_df(t) for t in left_tables[len(right_tables) :])
    elif len(right_tables) > len(left_tables):
        right_dfs.extend(_table_to_df(t) for t in right_tables[len(left_tables) :])

    output_path = Path(args.output)
    generate_report(
        left_dfs,
        right_dfs,
        diffs,
        left_title,
        right_title,
        output_path,
        args.left,
        args.right,
    )
    result_path = generate_report(
        left_dfs,
        right_dfs,
        diffs,
        left_title,
        right_title,
        output_path,
        args.left,
        args.right,
    )
    log.info("Diff report written to %s", result_path)


if __name__ == "__main__":
    main()
