#!/usr/bin/env python
"""Command‑line interface for the *summary_tool* package.

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
import sys


from .extractor import extract_testdetails
from .comparer import compare_tables, _table_to_df
from .html_report import generate_report, HTML_HEADER, HTML_FOOTER
import bs4
from .utils import is_url

log = logging.getLogger(__name__)


def _sub_variants(name: str) -> list[str]:
    """Return a list of possible name variants for a subdirectory.

    Handles:
    * original case
    * lower / upper / title case
    * underscore present or absent (e.g., 'tv_ts' ↔ 'tvts')
    """
    variants: set[str] = {name, name.lower(), name.upper(), name.title()}
    if "_" in name:
        no_us = name.replace("_", "")
        variants.update({no_us, no_us.lower(), no_us.upper(), no_us.title()})
    else:
        # also try adding an underscore variant just in case
        with_us = name + "_"
        variants.update({with_us, with_us.lower(), with_us.upper(), with_us.title()})
    # filter out empty strings
    return [v for v in variants if v]

def _candidate(base: str, sub_name: str) -> str:
    """Return a URL or filesystem path for *sub_name* under *base*.

    Handles both HTTP(S) URLs and local paths.
    """
    if base.startswith(("http://", "https://")):
        return f"{base.rstrip('/')}/{sub_name}/"
    else:
        return str(Path(base) / sub_name)


def _process_remote(left_url: str, subdirs: list[str], temp_dir: Path) -> list[Path]:
    """Process remote HTTP directory.

    Returns a list of generated report file paths.
    """
    generated: list[Path] = []
    base_url = left_url.rstrip("/") + "/"
    for sub in subdirs:
        # Generate all case/underscore variants for the subdirectory name
        sub_variants = _sub_variants(sub)
        html_files: list[str] = []
        for sub_variant in sub_variants:
            sub_url = f"{base_url}{sub_variant}/"
            visited: set[str] = set()

            def _collect(url: str, depth: int = 0) -> list[str]:
                if depth > 5:
                    return []
                results: list[str] = []
                try:
                    resp = requests.get(url, timeout=10)
                    resp.raise_for_status()
                    soup = bs4.BeautifulSoup(resp.text, "html.parser")
                    for a in soup.find_all("a", href=True):
                        href = a["href"]
                        full = (
                            href
                            if href.startswith("http")
                            else url.rstrip("/") + "/" + href.lstrip("/")
                        )
                        if href.endswith("test_result_failures_suite.html"):
                            results.append(full)
                        elif href.endswith("/") and full not in visited:
                            visited.add(full)
                            results.extend(_collect(full, depth + 1))
                except Exception:
                    pass
                return results

            html_files = sorted(set(_collect(sub_url)))
            if html_files:
                break
        if not html_files:
            log.info(
                f"No 'test_result_failures_suite.html' found under remote sub '{sub}'."
            )
            continue
        for idx, left_path in enumerate(html_files, start=1):
            log.debug(f"Processing remote {left_path} for sub '{sub}' (part {idx})")
            left_title, left_tables = extract_testdetails(left_path)
            left_dfs = [_table_to_df(t) for t in left_tables]
            out_path = temp_dir / f"{sub}_{idx}.html"
            generate_report(
                left_dfs,
                [],
                [],
                left_title or f"{sub} – part {idx}",
                "",
                out_path,
                left_path,
                None,
            )
            generated.append(out_path)
    return generated


def _process_local(left_root: str, subdirs: list[str], temp_dir: Path) -> list[Path]:
    """Process a local directory.

    Returns a list of generated report file paths.
    """
    generated: list[Path] = []
    root_dir = Path(left_root)
    for sub in subdirs:
        # Generate all case/underscore variants for the subdirectory name
        candidates = [_candidate(str(root_dir), v) for v in _sub_variants(sub)]
        sub_dir_path = None
        for cand in candidates:
            p = Path(cand)
            if p.is_dir():
                sub_dir_path = p
                break
        if sub_dir_path is None:
            log.info(f"Subdirectory '{sub}' not found (tried variants) under {root_dir}, skipping.")
            continue
        html_files = sorted(sub_dir_path.rglob("test_result_failures_suite.html"))
        if not html_files:
            # fallback to any html file if specific suite not found
            html_files = sorted(sub_dir_path.rglob("*.html"))
            if not html_files:
                log.info(
                    f"No HTML files found under {sub_dir_path}, skipping."
                )
                continue
        for idx, html_path in enumerate(html_files, start=1):
            left_path = str(html_path)
            log.debug(f"Processing {left_path} for sub '{sub}' (part {idx})")
            left_title, left_tables = extract_testdetails(left_path)
            left_dfs = [_table_to_df(t) for t in left_tables]
            out_path = temp_dir / f"{sub}_{idx}.html"
            generate_report(
                left_dfs,
                [],
                [],
                left_title or f"{sub} – part {idx}",
                "",
                out_path,
                left_path,
                None,
            )
            generated.append(out_path)
    return generated


def build_parser() -> argparse.ArgumentParser:
    """Create and return the argument parser, including a ``--version`` flag.

    The version string is taken from :mod:`summary_tool.__version__`, which
    mirrors the version declared in *pyproject.toml*.  ``argparse`` handles the
    ``--version`` output automatically via ``action='version'``.
    """
    """Create argument parser.
    Supports providing two HTML files/URLs (left & right) for comparison, or
    a single directory (left) with ``--recursive`` to automatically locate all
    ``test_result_failures_suite.html`` files under it.
    """
    parser = argparse.ArgumentParser(
        description="Compare 'testdetails' tables from two HTML sources across multiple subdirectories."
    )
    parser.add_argument(
        "left", help="Path or URL of the left root directory or HTML file"
    )
    parser.add_argument(
        "right",
        nargs="?",
        default="",
        help="Path or URL of the right root directory or HTML file (optional). If omitted, single‑column mode is used.",
    )
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
    parser.add_argument(
        "-r",
        "--recursive",
        action="store_true",
        help="(可选)在单列模式下强制递归搜索 `test_result_failures_suite.html`。若省略，若左路径是目录且包含此类文件，仍会自动递归。",
    )

    # ----- version flag -----
    from . import __version__
    parser.add_argument(
        "-V",
        "--version",
        action="version",
        version=f"summary_tool {__version__}",
        help="Show the tool's version and exit.",
    )
    # ----- end version flag -----
    return parser


def main(argv: List[str] | None = None) -> None:
    """Entry point for the ``summary_tool`` CLI.

    Parameters
    ----------
    argv:
        Optional list of argument strings.  If ``None`` (the default) the
        arguments are taken from ``sys.argv`` as usual.  Supplying a list makes
        the function convenient to call from tests.
    """
    parser = build_parser()
    args = parser.parse_args(argv)
    # Adjust default output name for single‑column mode
    if not args.right and args.output == "diff.html":
        args.output = "xts_summary.html"
    # Adjust default output name for dual‑column mode
    if args.right and args.output == "diff.html":
        args.output = "xts-diff_summary.html"
    # Logging configuration is left to the caller. The library uses
    # module‑level ``log = logging.getLogger(__name__)``. If a custom level
    # is desired, the invoking program can configure the root logger (or the
    # ``summary_tool`` logger) before calling ``main``.
    # Example: ``logging.basicConfig(level=logging.DEBUG)``
    # Ensure at least one handler exists so INFO logs are visible by default.
    if not logging.getLogger().hasHandlers():
        logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    # The ``--verbose`` flag is retained for backward compatibility but now
    # only adjusts the library logger's level directly.
    if args.verbose:
        log.setLevel(logging.DEBUG)
    else:
        log.setLevel(logging.INFO)

    # Resolve directories to specific HTML file if needed
    def _resolve(arg: str, subdir: str = "") -> str:
        """Resolve *arg* to a concrete HTML file path.

        - If *arg* is a URL, optionally search within the URL for an HTML file containing
          ``testdetails`` when the URL ends with ``/``.
        - If *arg* is a local directory, look for ``test_result_failures_suite.html``
          inside the directory *or* inside the provided ``subdir`` (if any). If not found,
          fall back to the first ``*.html`` file.
        """
        if arg.startswith(("http://", "https://")):
            # URL handling – if it ends with a slash treat it as a directory
            if arg.endswith("/"):

                def _search(url, depth=0):
                    if depth > 3:
                        return None
                    # Try conventional file name first
                    cand = url.rstrip("/") + "/test_result_failures_suite.html"
                    try:
                        resp = requests.head(cand, timeout=10)
                        if resp.status_code == 200:
                            return cand
                    except Exception:
                        pass
                    # Fetch directory listing and look for .html links
                    try:
                        resp = requests.get(url, timeout=10)
                        resp.raise_for_status()
                        soup = bs4.BeautifulSoup(resp.text, "html.parser")
                        for a in soup.find_all("a", href=True):
                            href = a["href"]
                            if href.lower().endswith(".html"):
                                full_url = (
                                    href
                                    if href.startswith("http")
                                    else url.rstrip("/") + "/" + href.lstrip("/")
                                )
                                try:
                                    page_resp = requests.get(full_url, timeout=10)
                                    if page_resp.ok and "testdetails" in page_resp.text:
                                        return full_url
                                except Exception:
                                    pass
                                # fallback to first html link
                                if href.startswith("http"):
                                    return href
                                else:
                                    base = url.rstrip("/") + "/"
                                    return base + href.lstrip("/")
                        # recurse into sub‑directories
                        for a in soup.find_all("a", href=True):
                            href = a["href"]
                            if href.endswith("/"):
                                sub_url = (
                                    href
                                    if href.startswith("http")
                                    else url.rstrip("/") + "/" + href.lstrip("/")
                                )
                                found = _search(sub_url, depth + 1)
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
            # If a subdir name is provided (dual‑column mode), look inside it
            search_root = p / subdir if subdir else p
            # Prefer the conventional file name
            for file in search_root.rglob("test_result_failures_suite.html"):
                if file.is_file():
                    return str(file)
            # Fallback: first *.html file under the root
            for file in search_root.rglob("*.html"):
                if file.is_file():
                    return str(file)
        return arg

    # ----- Multi‑subdir processing -----

    # If only left path provided, we operate in single‑column mode (no comparison)
    single_mode = not args.right
    subdirs = [s.strip() for s in args.subdirs.split(",") if s.strip()]
    temp_dir = Path.cwd() / "tmp_diff_reports"
    # 每次运行前清空临时报告目录，防止旧文件干扰
    import shutil
    if temp_dir.exists():
        shutil.rmtree(temp_dir)
    temp_dir.mkdir(parents=True, exist_ok=True)
    generated_files = []
    # ---------- Recursive processing (single‑column mode) ----------
    if args.recursive or (
        single_mode and (Path(args.left).is_dir() or is_url(args.left))
    ):
        # Choose remote or local handling based on left argument type
        if is_url(args.left):
            generated_files.extend(_process_remote(args.left, subdirs, temp_dir))
        else:
            generated_files.extend(_process_local(args.left, subdirs, temp_dir))
        # After processing all subdirs, skip the normal per‑subdir loop
        subdirs = []
    # Continue with the original loop (may be empty)
    for sub in subdirs:
        # Resolve left/right paths for this subdirectory
        # Build candidate URLs/paths for the subdirectory, handling case and underscore variants.

        def _resolve_with_all_variants(base: str, sub_name: str) -> str:
            """Try all case/underscore variants of *sub_name*.

            Returns the first resolved HTML path; if none found, returns the original base (so later code may still attempt a direct URL).
            """
            for variant in _sub_variants(sub_name):
                cand = _candidate(base, variant)
                resolved = _resolve(str(cand), variant)
                # If resolution succeeded (i.e., returned something different from the candidate), use it.
                if resolved != str(cand):
                    return resolved
            # Fallback – try the original name without variants
            return _resolve(str(_candidate(base, sub_name)), sub_name)

        left_path = _resolve_with_all_variants(args.left, sub)
        right_path = _resolve_with_all_variants(args.right, sub) if args.right else ""
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
            log.info(
                f"No testdetails for subdir '{sub}', generating summary-only diff."
            )
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
            left_dfs.extend(_table_to_df(t) for t in left_tables[len(right_tables) :])
        elif len(right_tables) > len(left_tables):
            right_dfs.extend(_table_to_df(t) for t in right_tables[len(left_tables) :])
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
        sys.exit(1)
    # Determine base report (cts) and other reports
    base_file = None
    other_files = []
    for f in generated_files:
        if f.name.startswith("cts-"):
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
