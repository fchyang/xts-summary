from pathlib import Path
import pandas as pd
import tempfile
import requests
import io
import logging

log = logging.getLogger(__name__)
import re
import bs4
from typing import List, Optional, Union

# Global counter for unique chart IDs across merged reports
import itertools

_chart_counter = itertools.count(1)

# -------------------------------------------------
# 常量区（HTML 结构、CSS、模板）
# -------------------------------------------------
HTML_HEADER = """<!DOCTYPE html>
<html><head><meta charset='utf-8'><title>Table Diff</title><base target="_blank">
<style>
/*added for single column layout*/
.single-col {flex:0 0 72%; max-width:72%; padding:10px; box-sizing:border-box; overflow-y:auto; overflow-x:hidden; min-width:0; margin:0 auto;}
.single-col h2 {margin-left:auto; margin-right:auto; text-align:center;}
.filepath {margin-top:0.2em; margin-bottom:0.5em; text-align:center; font-size:0.96em; color:#555; word-break:break-all; overflow-wrap:anywhere;}
.single-col .summary, .single-col .testdetails, .single-col .incompletemodules {margin-left:auto; margin-right:auto; text-align:left;}

.single-col .summary th, .single-col .summary td {text-align:left;}
.failuredetails {white-space:normal; word-break:break-all;}

body {font-family:Arial, sans-serif; margin:0; padding:0;}
.container {display:flex; flex-wrap:wrap; width:100%; overflow-x:auto;}
 .col {flex:0 0 50%; max-width:50%; padding:10px; box-sizing:border-box; overflow-y:auto; overflow-x:hidden; min-width:0;}
.col + .col {border-left:1px dashed #aaa;}
/* vertical divider between columns */

table {border-collapse:collapse; margin:10px 0; width:100%;}
/* make cells wrap content */
.testdetails td {white-space:normal; word-break:break-all;}

th, td {border:1px solid #aaa; padding:4px 8px;}
h2 {margin-top:0.5em;}
.testdetails td.module   {background:none;}
.testdetails th.module   {background:none;}
.testdetails td.testname {background:#d4e9a9;}
.testdetails td.failed   {background:#fa5858; text-align:center; font-weight:bold;}
.testdetails td.failuredetails {background:#d4e9a9;}
.testdetails th          {background:#a5c639 !important;}
.testdetails th.module   {background:none !important;}
    .summary-header {background:#a5c639 !important; border: solid 1px #aaa; text-align:left;}
    .summary-data {background:#d4e9a9; word-break:break-all; white-space:normal;}
    .summary td {max-width:490px; word-break:break-all; white-space:normal; overflow-wrap:anywhere;}

    /* Horizontal divider with centered label */
    .horizontal-divider {border-top:1px dashed #aaa; margin:-0.25em 0 12px 0; position:relative; text-align:center; width:calc(200% + 20px); left:-50%; margin-left:-10px; margin-right:-10px;}
    .horizontal-divider span {background:#fff; padding:0 8px; position:relative; top:-0.6em; font-weight:bold; font-size:1.3em;}

        .col + .col .summary {margin-left:0;}
    .summary-wrapper {display:flex; align-items:flex-start; gap:10px; margin-top:-0.5em;}
    .col + .col .summary-wrapper {justify-content:flex-start;}
    .col + .col .left-summary {margin-left:0;}
    .col + .col .right-summary {visibility:visible; width:auto;}
    .summary-wrapper .right-summary {display:flex; flex-direction:column; gap:5px;}
    .cts-diff {background:orange; padding:4px; font-weight:bold; text-align:center; margin-top:12px;}
    .degrade-modules {color:#b22222;background:none;font-size:0.9em;}
    .chart {margin-top:-0.5em;}
    .suspicious-label {color:black;font-weight:bold;background:none;}
    /* Align file path with title in double‑column mode */
    .col .filepath {text-align:left; margin-left:0;}

</style></head><body>
<div class='container'>"""
HTML_FOOTER = """</div></body></html>"""
MODULE_ROW_TMPL = (
    '<tr><th colspan="3" class="module" style="text-align:left;">{module}</th></tr>'
)
TABLE_HEADER = "<tr><th>Test</th><th>Result</th><th>Details</th></tr>"


def _make_table(df: pd.DataFrame) -> str:
    """Convert a DataFrame into the custom HTML table with proper CSS classes.
    The first row is treated as the module name, subsequent rows contain test, result, details.
    Missing columns are padded with empty strings to avoid unpack errors.
    """
    rows = df.values.tolist()
    # Handle "Incomplete Modules" table (header plus list of modules)
    if rows and (
        str(rows[0][0]).replace("\xa0", " ").strip().lower() == "incomplete modules"
        or (
            len(df.columns) == 1
            and str(df.columns[0]).replace("\xa0", " ").strip().lower()
            == "incomplete modules"
        )
    ):
        # Determine header text
        header = (
            rows[0][0]
            if str(rows[0][0]).replace("\xa0", " ").strip().lower()
            == "incomplete modules"
            else df.columns[0]
        )
        parts = [
            f"<tr><th colspan='3' class='module' style='text-align:left;background:#a5c639 !important;color:black;font-weight:bold;'>{header}</th></tr>"
        ]
        # Data rows start after header if header is in first row, otherwise all rows are data
        data_start = (
            1
            if str(rows[0][0]).replace("\xa0", " ").strip().lower()
            == "incomplete modules"
            else 0
        )
        for row in rows[data_start:]:
            module_name = str(row[0]) if row else ""
            if module_name:
                parts.append(
                    f"<tr><td colspan='3' class='module' style='background:#d4e9a9;color:black;'>{module_name}</td></tr>"
                )
        return f"<table class='incompletemodules' style='width:auto;'>{''.join(parts)}</table>"
    if not rows:
        return "<table class='testdetails'></table>"

    # Module title (left‑aligned, no background)
    module_name = rows[0][0]
    parts = [MODULE_ROW_TMPL.format(module=module_name), TABLE_HEADER]

    for row in rows[1:]:
        # Ensure three columns
        test, result, details = (list(row) + ["", "", ""])[:3]
        # Skip possible extra header rows and empty rows
        if (
            test == "Test" and result == "Result" and details == "Details"
        ) or not test.strip():
            continue
        col_class = "testname"
        test_td = f'<td class="{col_class}">{test}</td>'
        if result.strip().lower() == "fail":
            result_td = f'<td class="failed">{result}</td>'
            # Truncate failure details to 350 characters for display
            display_details = details if len(details) <= 350 else details[:350]
            details_td = f'<td class="failuredetails">{display_details}</td>'
        else:
            result_td = f"<td>{result}</td>"
            details_td = f"<td>{details}</td>"
        parts.append(f"<tr>{test_td}{result_td}{details_td}</tr>")

    
    return "<table class='testdetails'>" + "".join(parts) + "</table>"



def _make_summary_table(source: Optional[Union[Path, str]]) -> List[str]:
    """Extract the standard <table class='summary'> from the source HTML.
    Returns a list containing the HTML string of the summary table, or empty list.
    """
    """Extract a <table class='summary'> from *source* (local file or URL).
    Returns a list with the generated HTML string or empty list if not found.
    """
    if not source:
        return []
    # Load HTML content
    if isinstance(source, str) and source.startswith(("http://", "https://")):
        try:
            resp = requests.get(source, timeout=10)
            resp.raise_for_status()
            html = resp.text
        except Exception:
            return []
    else:
        try:
            html = Path(source).read_text(encoding="utf-8")
        except Exception:
            return []
    try:
        dfs = pd.read_html(io.StringIO(html), attrs={"class": "summary"})
    except Exception:
        return []
    if not dfs:
        return []
    df = dfs[0]
    rows = []
    # Header: keep first column name, blank others to avoid duplicate "Summary.1"
    header_cells = []
    for i, col in enumerate(df.columns):
        header = col if i == 0 else ""
        header_cells.append(f'<th class="summary-header">{header}</th>')
    rows.append("<tr>" + "".join(header_cells) + "</tr>")
    for row in df.itertuples(index=False, name=None):
        cells = [f'<td class="summary-data">{cell}</td>' for cell in row]
        rows.append("<tr>" + "".join(cells) + "</tr>")
    table_html = "<table class='summary'>" + "".join(rows) + "</table>"
    return [table_html]


def _extract_testsummary_table(source: Optional[Union[Path, str]]) -> List[str]:
    """Extract a custom "testsummary" table.
    This function attempts to locate a table that represents a test summary.
    It skips tables with class "summary" or "testdetails" and then looks
    for a table that contains keywords such as "test summary" in its textual
    content, or has a class name that explicitly indicates a test summary.
    If found, the raw HTML of that table is returned.
    """
    if not source:
        return []
    try:
        if isinstance(source, str) and source.startswith(("http://", "https://")):
            resp = requests.get(source, timeout=10)
            resp.raise_for_status()
            html = resp.text
        else:
            html = Path(source).read_text(encoding="utf-8")
    except Exception:
        return []
    # Parse HTML with BeautifulSoup for more reliable table detection
    soup = bs4.BeautifulSoup(html, "html.parser")
    for tbl in soup.find_all("table"):
        # Skip known summary or testdetails tables based on class attribute
        cls = tbl.get("class") or []
        # Normalize class names to lower case for comparison
        lower_cls = [c.lower() for c in cls]
        if any(c in ("summary", "testdetails") for c in lower_cls):
            continue
        # If the table explicitly has a class indicating a test summary, accept it
        if any("testsummary" in c for c in lower_cls):
            return [str(tbl)]
        # Check textual content for keywords indicating a test summary
        text = tbl.get_text(separator=" ", strip=True).lower()
        if "test summary" in text or "summary" in text:
            # Return the HTML string of the table
            return [str(tbl)]
    return []


def _extract_version(fingerprint: str) -> str | None:
    # (unchanged)
    """Extract a version number like 672 from a fingerprint string.
    The version is defined as the token that appears after the fourth '/' and
    before any ':' that may follow. If the pattern cannot be found, return None.
    """
    if not fingerprint:
        return None
    # Find the part after the fourth '/'
    parts = fingerprint.split("/")
    if len(parts) < 5:
        return None
    candidate = parts[4]
    # Remove any trailing ':' and following text
    candidate = candidate.split(":")[0]
    # Keep only digits (the version number)
    m = re.search(r"(\d+)", candidate)
    return m.group(1) if m else None


def _extract_suite_from_summary(source: str) -> str | None:
    """Extract the suite identifier from a summary table.
    Returns the whole "Suite / Plan" cell (e.g., "CTS / cts-on-gsi")
    so that different suites (CTS, CTS on GSI, etc.) can be distinguished.
    """
    if not source:
        return None
    try:
        if source.startswith(("http://", "https://")):
            resp = requests.get(source, timeout=10)
            resp.raise_for_status()
            html = resp.text
        else:
            html = Path(source).read_text(encoding="utf-8")
    except Exception:
        return None
    # Load summary tables and look for a row containing "Suite / Plan"
    try:
        dfs = pd.read_html(io.StringIO(html), attrs={"class": "summary"})
    except Exception:
        return None
    for df in dfs:
        # Look for a row where the first column is exactly "Suite / Plan"
        for _, row in df.iterrows():
            if len(row) >= 2 and str(row.iloc[0]).strip() == "Suite / Plan":
                suite_cell = str(row.iloc[1]).strip()
                # Return the full cell (e.g., "CTS / cts-on-gsi")
                return suite_cell
    return None


from dataclasses import dataclass, field


@dataclass
class ReportConfig:
    """Configuration for generate_report.

    All fields correspond to the keyword arguments of ``generate_report``.
    ``left_dfs`` and ``right_dfs`` are not included because they are positional
    inputs. ``diff_dfs`` is kept for compatibility but defaults to an empty list.
    """

    diff_dfs: List[pd.DataFrame] = field(default_factory=list)
    left_title: str = ""
    right_title: str = ""
    output_path: Path = Path("diff.html")
    left_summary_source: Optional[Union[Path, str]] = None
    right_summary_source: Optional[Union[Path, str]] = None


def generate_report(
    left_dfs: List[pd.DataFrame],
    right_dfs: List[pd.DataFrame] = [],
    diff_dfs: List[pd.DataFrame] = [],
    left_title: str = "",
    right_title: str = "",
    output_path: Path = Path("diff.html"),
    left_summary_source: Optional[Union[Path, str]] = None,
    right_summary_source: Optional[Union[Path, str]] = None,
    report_config: Optional[ReportConfig] = None,
    newer_side: str = "",
) -> Path:
    # If a ReportConfig is provided, override individual arguments
    if report_config is not None:
        diff_dfs = report_config.diff_dfs
        left_title = report_config.left_title
        right_title = report_config.right_title
        output_path = report_config.output_path
        left_summary_source = report_config.left_summary_source
        right_summary_source = report_config.right_summary_source

    """Create a two‑column HTML view showing left & right tables.

    * ``left_dfs`` / ``right_dfs`` – DataFrames extracted from the two HTML files.
    * ``diff_dfs`` – kept for API compatibility, not used.
        * Titles are kept for compatibility but not displayed; summary tables include fingerprints.
    """
    # Determine if single column mode (no right side)
    single_mode = not right_dfs and not right_summary_source
    # Build summary tables if sources provided
    left_summary = (
        _make_summary_table(left_summary_source) if left_summary_source else []
    )
    # Determine Modules Total and Modules Done from the summary table (if present)
    modules_total = None
    modules_done = None
    if left_summary:
        # Combine all summary tables into a single HTML string for regex searches
        summary_html = "".join(left_summary)

        # Helper to search for a numeric value given a label (e.g., 'Modules Total')
        def _search_value(label):
            # Try <th>label</th><td>value</td>
            pattern = rf"<th[^>]*>\s*{label}\s*:?\s*</th>\s*<td[^>]*>\s*(\d+)\s*</td>"
            m = re.search(pattern, summary_html, re.IGNORECASE | re.DOTALL)
            if m:
                return int(m.group(1))
            # Try <td class="rowtitle">label</td><td>value</td>
            pattern = rf'<td[^>]*class\s*=\s*["\'].*?rowtitle.*?["\'][^>]*>\s*{label}\s*</td>\s*<td[^>]*>\s*(\d+)\s*</td>'
            m = re.search(pattern, summary_html, re.IGNORECASE | re.DOTALL)
            if m:
                return int(m.group(1))
            # Generic fallback
            pattern = rf"{label}[^<]*</[^>]*>\s*<td[^>]*>\s*(\d+)\s*</td>"
            m = re.search(pattern, summary_html, re.IGNORECASE | re.DOTALL)
            if m:
                return int(m.group(1))
            return None

        modules_total = _search_value("Modules Total")
        modules_done = _search_value("Modules Done")
        log.debug(
            f"Modules Total parsed: {modules_total}, Modules Done parsed: {modules_done}, summary_html length: {len(summary_html)}"
        )
    # Conditional inclusion of testsummary
    testsummary = []
    if not left_dfs:
        # No testdetails; attempt to extract testsummary table
        candidate = (
            _extract_testsummary_table(left_summary_source)
            if left_summary_source
            else []
        )
        # Ensure testsummary table uses the same styling as summary tables
        if candidate:
            # Use BeautifulSoup to add class and optional styles
            soup = bs4.BeautifulSoup(candidate[0], "html.parser")
            table_tag = soup.find("table")
            if table_tag:
                # Add "summary" to class list if not present
                existing_classes = table_tag.get("class") or []
                if "summary" not in existing_classes:
                    existing_classes.append("summary")
                table_tag["class"] = existing_classes
                if single_mode:
                    # Add background style to header cells (th)
                    for th in table_tag.find_all("th"):
                        prev_style = th.get("style", "")
                        th["style"] = (
                            prev_style + ";" if prev_style else ""
                        ) + "background:#a5c639"
                    # Add background style to data cells (td)
                    for td in table_tag.find_all("td"):
                        prev_style = td.get("style", "")
                        td["style"] = (
                            prev_style + ";" if prev_style else ""
                        ) + "background:#d4e9a9"
            candidate = [str(soup)]
        log.debug(f"Testsummary candidate found: {bool(candidate)}")
        # Include only if Modules Total < 20 (and candidate exists)
        if candidate and (modules_total is None or modules_total < 20):
            testsummary = candidate
    # Append any testsummary to left_summary
    left_summary.extend(testsummary)
    # Fallback: if parsing failed, extract raw summary table via regex
    if not left_summary and left_summary_source:
        try:
            raw_html = Path(left_summary_source).read_text(encoding="utf-8")
            m = re.search(
                r"<table[^>]*class=['\"]summary['\"][^>]*>.*?</table>",
                raw_html,
                re.DOTALL,
            )
            if m:
                left_summary = [m.group(0)]
        except Exception:
            pass
    right_summary = (
        _make_summary_table(right_summary_source) if right_summary_source else []
    )
    if not right_summary and right_summary_source:
        try:
            raw_html = Path(right_summary_source).read_text(encoding="utf-8")
            m = re.search(
                r"<table[^>]*class=['\"]summary['\"][^>]*>.*?</table>",
                raw_html,
                re.DOTALL,
            )
            if m:
                right_summary = [m.group(0)]
        except Exception:
            pass
    # Compute overlap statistics of test names between left and right
    # Extract only actual test names (contain a dot) from the first column of each testdetails DataFrame
    left_tests = {
        str(val)
        for df in left_dfs
        for val in df.iloc[:, 0].astype(str).tolist()
        if "." in str(val)
    }
    right_tests = {
        str(val)
        for df in right_dfs
        for val in df.iloc[:, 0].astype(str).tolist()
        if "." in str(val)
    }
    same_count = len(left_tests & right_tests)
    diff_count = len(left_tests ^ right_tests)
    overlap_summary = f"<table class='summary'><tr><th class='summary-header'>Same testnames</th><td class='summary-data'>{same_count}</td></tr><tr><th class='summary-header'>Degrade testnames</th><td class='summary-data' style='background:#fa5858;'>{diff_count}</td></tr></table>"
    # Compute module overlap statistics (excluding Incomplete Modules)
    # Helper to decide whether a DataFrame represents an Incomplete Modules table
    def _is_incomplete_df(df: pd.DataFrame) -> bool:
        if df.empty:
            return False
        # case 1: first cell contains the header
        first_cell = str(df.iloc[0, 0]).strip().lower()
        if first_cell == "incomplete modules":
            return True
        # case 2: single‑column table where the column name is the header
        if len(df.columns) == 1 and "incomplete modules" in str(df.columns[0]).strip().lower():
            return True
        return False

    # Helper to extract module names from an Incomplete Modules table
    def _extract_incomplete_modules(dfs: list[pd.DataFrame]) -> set[str]:
        mods: set[str] = set()
        for df in dfs:
            if df.empty:
                continue
            # Case 1: first cell is the header
            first_cell = str(df.iloc[0, 0]).strip().lower()
            if first_cell == "incomplete modules":
                for val in df.iloc[1:, 0]:
                    name = str(val).strip()
                    if name:
                        mods.add(name)
                continue
            # Case 2: single‑column table where column name is the header
            if len(df.columns) == 1 and "incomplete modules" in str(df.columns[0]).strip().lower():
                for val in df.iloc[:, 0]:
                    name = str(val).strip()
                    if name:
                        mods.add(name)
        return mods

    left_modules = set()
    for df in left_dfs:
        if not _is_incomplete_df(df):
            left_modules.add(str(df.iloc[0, 0]))
    right_modules = set()
    for df in right_dfs:
        if not _is_incomplete_df(df):
            right_modules.add(str(df.iloc[0, 0]))
    same_modules = len(left_modules & right_modules)
    # Determine suspicious modules based on newer_side (dual‑column only)
    if newer_side:
        if newer_side == "left":
            suspicious_set = left_modules - right_modules
        elif newer_side == "right":
            suspicious_set = right_modules - left_modules
        else:
            suspicious_set = left_modules ^ right_modules
    else:
        suspicious_set = left_modules ^ right_modules
    # If no suspicious modules but there are same modules, treat the same modules as suspicious for display
    if not suspicious_set and same_modules:
        suspicious_set = left_modules & right_modules
    # --- NEW: include Incomplete Modules from the newer side into suspicious_set ---
    if newer_side == "left":
        suspicious_set = suspicious_set.union(_extract_incomplete_modules(left_dfs))
    elif newer_side == "right":
        suspicious_set = suspicious_set.union(_extract_incomplete_modules(right_dfs))
    degrade_modules = len(suspicious_set)
    # In single‑column mode, compute "Incomplete modules" from the summary table
    # (Modules Total - Modules Done) and keep the original "Suspicious modules" count.
    if single_mode:
        # Use the previously parsed modules_total and modules_done values for single‑column calculations.
        total_modules = modules_total
        done_modules = modules_done
        # Debug: Log extracted module counts
        log.debug(
            "Extracted total_modules=%s, done_modules=%s", total_modules, done_modules
        )
        if total_modules is not None and done_modules is not None:
            incomplete = max(0, total_modules - done_modules)
        else:
            incomplete = 0
        same_modules = incomplete
        # Keep degrade_modules as originally calculated (suspicious modules)
    module_summary = f"<table class='summary'><tr><th class='summary-header'>Same modules</th><td class='summary-data'>{same_modules}</td></tr><tr><th class='summary-header' style='color:black;font-weight:bold;background:#ff0000 !important;'>Suspicious modules</th><td class='summary-data' style='background:#fa5858;'>{degrade_modules}</td></tr></table>"
    # Prepare a simple pie chart for module comparison
    # Version info needed for a unique chart id (to avoid id clash when multiple reports are merged)
    left_version = _extract_version(left_title)
    right_version = _extract_version(right_title)
    # Determine suite name for chart ID uniqueness
    left_suite = (
        _extract_suite_from_summary(left_summary_source)
        if left_summary_source
        else None
    )
    right_suite = (
        _extract_suite_from_summary(right_summary_source)
        if right_summary_source
        else None
    )
    suite_name = left_suite or right_suite or "CTS"
    # Make a safe ID component (remove spaces and slashes)
    safe_suite = suite_name.replace(" ", "_").replace("/", "_")
    # Use a global counter to guarantee unique IDs across all generated reports
    # obtain a unique chart index
    chart_index = next(_chart_counter)
    # Build chart ID using suite name and counter (and versions if available)
    if left_version and right_version:
        chart_id = (
            f"moduleChart_{safe_suite}_{left_version}_{right_version}_{chart_index}"
        )
    else:
        chart_id = f"moduleChart_{safe_suite}_{chart_index}"

    chart_html = f"<div class='chart' style='margin-top:0.2em'><canvas id='{chart_id}' width='252' height='252' style='width:252px;height:252px;'></canvas></div>"
    # Determine label for pie chart based on mode
    label1 = "Incomplete modules" if single_mode else "Same modules"
    chart_script = (
        "<script src='https://cdn.jsdelivr.net/npm/chart.js'></script>"
        "<script>"
        f"var ctx=document.getElementById('{chart_id}').getContext('2d');"
        f"new Chart(ctx,{{type:'pie',data:{{labels:['{label1} ({same_modules})','Suspicious modules ({degrade_modules})'],datasets:[{{data:[{same_modules},{degrade_modules}],backgroundColor:['#4caf50','#f44336']}}]}} ,options:{{responsive:false,maintainAspectRatio:false}}}});"
        "</script>"
    )
        # Build left summary: include left summary, CTS Diff block, chart, and list of degraded module names
    # Use the suspicious_set (modules only in newer side) for display
    suspicious_set = left_modules.symmetric_difference(right_modules) if not newer_side else suspicious_set
    # Remove the ABI prefix (e.g., "armeabi-v7a") from module names for display
    cleaned_module_names = []
    for name in suspicious_set:
        cleaned = (
            name.replace("armeabi-v7a ", "")
            .replace("armeabi-v7a\u00a0", "")
            .replace("arm64-v8a ", "")
            .replace("arm64-v8a\u00a0", "")
            .strip()
        )
        cleaned_module_names.append(cleaned)
    suspicious_set = set(cleaned_module_names)
    degrade_modules_list_html = (
        "<div class='degrade-modules' style='margin-top:0.5em;'><span class='suspicious-label'>Suspicious modules:</span><br>"
        + "<br>".join(sorted(suspicious_set))
        + "</div>"
    )
    # Gather module names from the "Incomplete modules" table (if present) for single‑column mode
    incomplete_module_names = []
    for df in left_dfs:
        if df.empty:
            continue
        # Case 1: first cell contains the header
        first_cell = str(df.iloc[0, 0]).strip().lower()
        if first_cell == "incomplete modules":
            for val in df.iloc[1:, 0]:
                name = str(val).strip()
                if name:
                    # Strip common ABI prefixes and whitespace
                    cleaned = (
                        name.replace("armeabi-v7a ", "")
                        .replace("armeabi-v7a\u00a0", "")
                        .replace("arm64-v8a ", "")
                        .replace("arm64-v8a\u00a0", "")
                        .strip()
                    )
                    incomplete_module_names.append(cleaned)
        # Case 2: single‑column DataFrame with column name as header
        elif (
            len(df.columns) == 1
            and "incomplete modules" in str(df.columns[0]).strip().lower()
        ):
            for val in df.iloc[:, 0]:
                name = str(val).strip()
                if name:
                    cleaned = (
                        name.replace("armeabi-v7a ", "")
                        .replace("armeabi-v7a\u00a0", "")
                        .replace("arm64-v8a ", "")
                        .replace("arm64-v8a\u00a0", "")
                        .strip()
                    )
                    incomplete_module_names.append(cleaned)
    incomplete_modules_list_html = (
        "<div class='degrade-modules'><span class='suspicious-label'>Incomplete modules:</span><br>"
        + "<br>".join(sorted(incomplete_module_names))
        + "</div>"
    )
    # Build CTS Diff title with version info if available
    left_version = _extract_version(left_title)
    right_version = _extract_version(right_title)
    # Extract suite name from the source html (left/right) if available
    left_suite = (
        _extract_suite_from_summary(left_summary_source)
        if left_summary_source
        else None
    )
    right_suite = (
        _extract_suite_from_summary(right_summary_source)
        if right_summary_source
        else None
    )
    suite_name = left_suite or right_suite or "CTS"
    # Build the diff title using versions (if any) and suite name
    # Horizontal divider label (suite name) will be placed above both columns
    horizontal_divider_html = (
        f"<div class='horizontal-divider'><span>{suite_name}</span></div>"
    )
    if left_version and right_version:
        diff_title = f"v{left_version} Vs v{right_version} Diff"
    else:
        diff_title = "Diff"
        # Divider spanning both columns (placed above each column's summary)
    divider_html = (
        f"<br><div class='horizontal-divider'><span>{suite_name}</span></div>"
    )
    # Build summary section differently for single column mode (only left path provided)
    if single_mode:
        # In single-mode we omit the orange CTS Diff block and show the chart with the module summary on its right.
        left_summary_combined = (
            divider_html + "<div class='summary-wrapper'>"
            "<div class='left-summary'>" + "".join(left_summary) + "</div>"
            "<div class='right-summary' style='display:flex; flex-direction:row; align-items:flex-start; gap:10px; visibility:visible; margin-top:1.5em;'>"
            + "<div class='chart-container'>"
            + chart_html
            + chart_script
            + "</div>"
            + degrade_modules_list_html
            + incomplete_modules_list_html
            + "</div>"
            "</div>"
        )
    else:
        left_summary_combined = (
        divider_html + "<div class='summary-wrapper'>"
        "<div class='left-summary'>" + "".join(left_summary) + "</div>"
        "<div class='right-summary'>"
        f"<div class='cts-diff'>{diff_title}</div>"
        + chart_html
        + chart_script
        # Degraded modules moved to right column's left-summary
        + "</div>"
        "</div>"
    )
    # Right side keeps its summary tables (module summary removed)
    right_placeholder = "<div class='right-summary' style='visibility:hidden;'></div>"
        # Right column: left-summary holds version compare placeholder, right-summary holds summary tables
    same_modules_set = left_modules & right_modules
    # Remove ABI prefixes (armeabi‑v7a, arm64‑v8a) from module names for display
    cleaned_same_modules = []
    for name in same_modules_set:
        cleaned = (
            name.replace("armeabi-v7a ", "")
                .replace("armeabi-v7a\u00a0", "")
                .replace("arm64-v8a ", "")
                .replace("arm64-v8a\u00a0", "")
                .strip()
        )
        cleaned_same_modules.append(cleaned)
    same_modules_set = set(cleaned_same_modules)
    same_modules_list_html = (
        "<div class='degrade-modules' style='margin-top:0.5em;'><span class='suspicious-label'>Same modules:</span><br>"
        + "<br>".join(sorted(same_modules_set))
        + "</div>"
    )
    right_summary_combined = (
            divider_html
            + "<div class='summary-wrapper'>"
            + "<div class='left-summary'>"
            + f"<div class='cts-diff'>{diff_title}</div>"
            + same_modules_list_html
            + degrade_modules_list_html  # Added suspicious modules here
            + "</div>"
            + "<div class='right-summary' style='margin-left:auto;'>"
            + "".join(right_summary)
            + "</div>"
            + "</div>"
        )

    if single_mode:
        # Single column layout: use .single-col class, omit right side elements
        # Prepare optional file path display for single column mode
        left_path_line = (
            f"<div class='filepath'><a href='{left_summary_source}'>{left_summary_source}</a></div>"
            if left_summary_source
            else ""
        )
        parts = [
            HTML_HEADER,
            f"<div class='single-col'>",
            f"<h2>{left_title}</h2>" if left_title else "",
            left_path_line,
            left_summary_combined,
            *[_make_table(df) for df in left_dfs],
            "</div>",
            HTML_FOOTER,
        ]
    else:
        # Double column layout: add file path links under each title
        left_path_line = (
            f"<div class='filepath'><a href='{left_summary_source}'>{left_summary_source}</a></div>"
            if left_summary_source
            else ""
        )
        right_path_line = (
            f"<div class='filepath'><a href='{right_summary_source}'>{right_summary_source}</a></div>"
            if right_summary_source
            else ""
        )
        parts = [
            HTML_HEADER,
            f"<div class='col'>",
            f"<h2>{left_title}</h2>" if left_title else "",
            left_path_line,
            left_summary_combined,
            *[_make_table(df) for df in left_dfs],
            "</div>",
            f"<div class='col'>",
            f"<h2>{right_title}</h2>" if right_title else "",
            right_path_line,
            right_summary_combined,
            *[_make_table(df) for df in right_dfs],
            "</div>",
            HTML_FOOTER,
        ]

    # 写入文件一次性完成
    # Ensure parent directory exists
    # Determine default output filename based on mode if not overridden
    default_name = Path("xts.html") if single_mode else Path("xts-diff.html")
    if output_path == Path("diff.html"):
        output_path = default_name
    # Ensure output directory exists
    output_path.parent.mkdir(parents=True, exist_ok=True)
    final_path = output_path
    try:
        output_path.write_text("\n".join(parts), encoding="utf-8")
    except PermissionError:
        # Fallback to a writable location in /tmp
        fallback_dir = Path(tempfile.gettempdir()) / "diff_output"
        fallback_dir.mkdir(
            parents=True, exist_ok=True
        )  # Ensure writable directory in /tmp
        fallback_path = fallback_dir / f"{output_path.stem}.html"
        logging.warning(
            "Permission denied writing to %s; writing to %s instead.",
            output_path,
            fallback_path,
        )
        fallback_path.write_text("\n".join(parts), encoding="utf-8")
        final_path = fallback_path
    return final_path
