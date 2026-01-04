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
    Supports providing either HTML files directly or directories containing
    specific subdirectories (e.g., cts, ctsongsi, gts, …) where the file
    ``test_result_failures_suite.html`` resides.
    """
    parser = argparse.ArgumentParser(
        description="Compare 'testdetails' tables from two HTML sources across multiple subdirectories."
    )
    parser.add_argument("left", help="Path or URL of the left root directory or HTML file")
    parser.add_argument("right", nargs='?', default='', help="Path or URL of the right root directory or HTML file (optional). If omitted, single‑column mode is used.")
    parser.add_argument(
        "-s",
        "--subdirs",
        default="cts,ctsongsi,gts,vts,tv_ts,sts",
        help="Comma‑separated list of subdirectory names to compare (default: %(default)s)",
    )
    parser.add_argument(
        "-o",
        "--output",
        default="diff.html",
        help="Final merged HTML report file (default: %(default)s)",
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
    # ----- Multi‑subdir processing -----
    import re
    from pathlib import Path
    from .html_report import HTML_HEADER, HTML_FOOTER

    # If only left path provided, we operate in single‑column mode (no comparison)
    single_mode = not args.right
    subdirs = [s.strip() for s in args.subdirs.split(',') if s.strip()]
    temp_dir = Path.cwd() / "tmp_diff_reports"
    temp_dir.mkdir(parents=True, exist_ok=True)
    generated_files = []
    for sub in subdirs:
        # Resolve left/right paths for this subdirectory
        left_candidate = (Path(args.left) / sub if not args.left.startswith(("http://", "https://")) else f"{args.left.rstrip('/')}/{sub}/")
        right_candidate = (Path(args.right) / sub if args.right and not args.right.startswith(("http://", "https://")) else (f"{args.right.rstrip('/')}/{sub}/" if args.right else ""))
        left_path = _resolve(str(left_candidate))
        right_path = _resolve(str(right_candidate)) if args.right else ""
        log.debug(f"Processing subdir '{sub}': left={left_path}, right={right_path}")

        # Extract tables and titles
        left_title, left_tables = extract_testdetails(left_path)
        # Right side optional – if no right_path, treat as empty
        if right_path:
            right_title, right_tables = extract_testdetails(right_path)
        else:
            right_title, right_tables = "", []
        if not left_tables and not right_tables:
            # No testdetails in either side, but we still want to generate a diff report with summary and version info.
            log.info(f"No testdetails for subdir '{sub}', generating summary-only diff.")
        elif not left_tables or not right_tables:
            # One side missing testdetails – still generate report with whatever tables are present.
            log.info(f"Partial testdetails for subdir '{sub}'.")
        left_dfs, right_dfs, diffs = [], [], []
        for lt, rt in zip(left_tables, right_tables):
            left_df, right_df, diff_df = compare_tables(lt, rt)
            left_dfs.append(left_df)
            right_dfs.append(right_df)
            diffs.append(diff_df)
        # Preserve extra tables
        if len(left_tables) > len(right_tables):
            left_dfs.extend(_table_to_df(t) for t in left_tables[len(right_tables):])
        elif len(right_tables) > len(left_tables):
            right_dfs.extend(_table_to_df(t) for t in right_tables[len(left_tables):])
        out_path = temp_dir / f"{sub}-diff.html"
        generate_report(
            left_dfs,
            right_dfs,
            diffs,
            left_title,
            right_title,
            out_path,
            left_path,
            right_path if right_path else None,
        )
        generated_files.append(out_path)

    # ----- Merge generated reports -----
    if not generated_files:
        log.error("No diff reports were generated.")
        return
    # Determine base report (cts) and other reports
    base_file = None
    other_files = []
    for f in generated_files:
        if f.name.startswith('cts-'):
            base_file = f
        else:
            other_files.append(f)
    if base_file is None:
        # Fallback: use first generated file as base
        base_file = generated_files[0]
        other_files = generated_files[1:]
    # Merge reports by stacking each report's own <div class='container'> block vertically.
    # Remove the common HTML_HEADER and HTML_FOOTER from each generated report, keeping the inner container.
    def extract_container(html: str) -> str:
        """Return the <div class='container'>...</div> block of a report."""
        # Strip the shared header/footer; the remainder starts with the container div.
        return html.replace(HTML_HEADER, "").replace(HTML_FOOTER, "").strip()

    # Header without the opening <div class='container'> (we will keep each report's own container)
    header_no_container = HTML_HEADER.split("<div class='container'>")[0]
    # Footer that only closes body and html (no extra </div>)
    footer_snippet = "</body></html>"

    # Concatenate full container blocks from each generated report
    combined_body = ""
    for file in generated_files:
        # Remove the shared HTML_HEADER and HTML_FOOTER, but keep the <div class='container'> wrapper
        content = file.read_text(encoding="utf-8")
        # Strip the outer header up to the container start (inclusive), then keep from there
        # Find the first occurrence of "<div class='container'>"
        start = content.find("<div class='container'>")
        end = content.rfind(HTML_FOOTER)
        if start != -1 and end != -1:
            combined_body += content[start:end]
        else:
            # Fallback: use whole content (unlikely)
            combined_body += content
    final_html = header_no_container + combined_body + footer_snippet

    final_path = Path(args.output)
    final_path.parent.mkdir(parents=True, exist_ok=True)
    final_path.write_text(final_html, encoding="utf-8")
    log.info("Merged diff report written to %s", final_path)
    return



if __name__ == "__main__":
    main()
