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
import requests
from pathlib import Path
from typing import List



from .extractor import extract_testdetails
from .comparer import compare_tables, _table_to_df
from .html_report import generate_report

log = logging.getLogger(__name__)



def build_parser() -> argparse.ArgumentParser:
    """Create argument parser.
    Supports providing either HTML files directly or directories containing a
    specific subdirectory (e.g., cts) where the file
    ``test_result_failures_suite.html`` resides.
    """
    """Create and return the argument parser used by the CLI."""
    parser = argparse.ArgumentParser(
        description="Compare 'testdetails' tables from two HTML sources."
    )
    parser.add_argument("left", help="Path or URL of the left HTML file or directory")
    parser.add_argument("right", help="Path or URL of the right HTML file or directory")
    parser.add_argument("-s", "--subdir", default="cts",
                        help="Subdirectory name under each directory where 'test_result_failures_suite.html' is located (default: %(default)s)")
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
    # Resolve directories to specific HTML file if needed
    def _resolve(arg: str) -> str:
        if arg.startswith(("http://", "https://")):
            # If the URL points to a directory (ends with slash), try to locate an HTML file inside
            if arg.endswith('/'):
                # Helper to recursively search for an HTML file
                def _search(url, depth=0):
                    if depth > 3:
                        return None
                    # Try conventional file name first
                    cand = url.rstrip('/') + "/test_result_failures_suite.html"
                    try:
                        resp = requests.head(cand, timeout=10)
                        if resp.status_code == 200:
                            return cand
                    except Exception:
                        pass
                    # Fetch directory listing
                    try:
                        resp = requests.get(url, timeout=10)
                        resp.raise_for_status()
                        import bs4
                        soup = bs4.BeautifulSoup(resp.text, "html.parser")
                        # Look for direct .html links
                        for a in soup.find_all('a', href=True):
                            href = a['href']
                            if href.lower().endswith('.html'):
                                # Build absolute URL if needed
                                full_url = href if href.startswith('http') else url.rstrip('/') + '/' + href.lstrip('/')
                                try:
                                    page_resp = requests.get(full_url, timeout=10)
                                    if page_resp.ok and 'testdetails' in page_resp.text:
                                        return full_url
                                except Exception:
                                    pass
                                # If not suitable, fallback to first .html
                                if href.startswith('http'):
                                    return href
                                else:
                                    base = url.rstrip('/') + '/'
                                    return base + href.lstrip('/')
                        # If not found, recurse into subdirectories
                        for a in soup.find_all('a', href=True):
                            href = a['href']
                            if href.endswith('/'):
                                sub_url = href if href.startswith('http') else url.rstrip('/') + '/' + href.lstrip('/')
                                found = _search(sub_url, depth+1)
                                if found:
                                    return found
                    except Exception:
                        pass
                    return None
                found_url = _search(arg)
                if found_url:
                    return found_url
            return arg
        p = Path(arg)
        if p.is_dir():
            # Look for the file inside the specified subdirectory, recursively
            search_path = p / args.subdir
            if search_path.is_dir():
                # First try the expected file name
                for file in search_path.rglob('test_result_failures_suite.html'):
                    if file.is_file():
                        return str(file)
                # Fallback: pick the first HTML file found under the subdirectory
                for file in search_path.rglob('*.html'):
                    if file.is_file():
                        return str(file)
        return arg
    left_path = _resolve(args.left)
    right_path = _resolve(args.right)
    log.debug(f"Resolved left path: {left_path}")
    log.debug(f"Resolved right path: {right_path}")

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
    )

    # Directly extract tables and titles from the provided sources.
    # ``extract_testdetails`` handles loading from both local files and URLs.
    left_title, left_tables = extract_testdetails(left_path)
    right_title, right_tables = extract_testdetails(right_path)

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
    # Generate report using the resolved file paths for summary extraction
    result_path = generate_report(
        left_dfs,
        right_dfs,
        diffs,
        left_title,
        right_title,
        output_path,
        left_path,
        right_path,
    )
    log.info("Diff report written to %s", result_path)


if __name__ == "__main__":
    main()
