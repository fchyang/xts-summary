
from pathlib import Path
import pandas as pd
import tempfile
import requests
import io
import logging
import re
from typing import List, Optional, Union

# -------------------------------------------------
# 常量区（HTML 结构、CSS、模板）
# -------------------------------------------------
HTML_HEADER = """<!DOCTYPE html>
<html><head><meta charset='utf-8'><title>Table Diff</title>
<style>
body {font-family:Arial, sans-serif; margin:0; padding:0;}
.container {display:flex; width:100%; overflow-x:auto;}
 .col {flex:1 1 0; padding:10px; box-sizing:border-box; overflow-y:auto; overflow-x:hidden; min-width:0;}
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
    .horizontal-divider {border-top:1px dashed #aaa; margin:-0.5em 0 12px 0; position:relative; text-align:center; width:calc(200% + 20px); left:-50%; margin-left:-10px; margin-right:-10px;}
    .horizontal-divider span {background:#fff; padding:0 8px; position:relative; top:-0.6em; font-weight:bold; font-size:1.3em;}

    .col + .col .summary {margin-left:0;}
    .summary-wrapper {display:flex; align-items:flex-start; gap:10px; margin-top:-0.5em;}
    .col + .col .summary-wrapper {justify-content:flex-start;}
    .col + .col .left-summary {margin-left:0;}
    .col + .col .right-summary {visibility:hidden; width:0;}
    .summary-wrapper .right-summary {display:flex; flex-direction:column; gap:5px;}
    .cts-diff {background:orange; padding:4px; font-weight:bold; text-align:center; margin-top:12px;}
.degrade-modules {color:#b22222;}
.chart {margin-top:-0.5em;}
</style></head><body>
<div class='container'>"""
HTML_FOOTER = """</div></body></html>"""
MODULE_ROW_TMPL = '<tr><th colspan="3" class="module" style="text-align:left;">{module}</th></tr>'
TABLE_HEADER   = '<tr><th>Test</th><th>Result</th><th>Details</th></tr>'

def _make_table(df: pd.DataFrame) -> str:
    """Convert a DataFrame into the custom HTML table with proper CSS classes.
    The first row is treated as the module name, subsequent rows contain test, result, details.
    Missing columns are padded with empty strings to avoid unpack errors.
    """
    rows = df.values.tolist()
    if not rows:
        return "<table class='testdetails'></table>"

    # Module title (left‑aligned, no background)
    module_name = rows[0][0]
    parts = [MODULE_ROW_TMPL.format(module=module_name), TABLE_HEADER]

    for row in rows[1:]:
        # Ensure three columns
        test, result, details = (list(row) + ["", "", ""])[:3]
        # Skip possible extra header rows and empty rows
        if (test == "Test" and result == "Result" and details == "Details") or not test.strip():
            continue
        col_class = "testname" if "." in test else "module"
        test_td = f'<td class="{col_class}">{test}</td>'
        if result.strip().lower() == "fail":
            result_td = f'<td class="failed">{result}</td>'
            details_td = f'<td class="failuredetails">{details}</td>'
        else:
            result_td = f'<td>{result}</td>'
            details_td = f'<td>{details}</td>'
        parts.append(f'<tr>{test_td}{result_td}{details_td}</tr>')

    return "<table class='testdetails'>" + "".join(parts) + "</table>"

    """Convert a DataFrame into the custom HTML table with proper CSS classes.
    The first row is treated as the module name, subsequent rows contain test, result, details.
    Missing columns are padded with empty strings to avoid unpack errors.
    """
    rows = df.values.tolist()
    if not rows:
        return "<table class='testdetails'></table>"

    # Module title (left‑aligned, no background)
    module_name = rows[0][0]
    parts = [MODULE_ROW_TMPL.format(module=module_name), TABLE_HEADER]

    for row in rows[1:]:
        # Ensure three columns
        test, result, details = (list(row) + ["", "", ""])[:3]
        # Skip possible extra header rows and empty rows
        if (test == "Test" and result == "Result" and details == "Details") or not test.strip():
            continue
        col_class = "testname" if "." in test else "module"
        test_td = f'<td class="{col_class}">{test}</td>'
        if result.strip().lower() == "fail":
            result_td = f'<td class="failed">{result}</td>'
            details_td = f'<td class="failuredetails">{details}</td>'
        else:
            result_td = f'<td>{result}</td>'
            details_td = f'<td>{details}</td>'
        parts.append(f'<tr>{test_td}{result_td}{details_td}</tr>')

    return "<table class='testdetails'>" + "".join(parts) + "</table>"


def _make_summary_table(source: Optional[Union[Path, str]]) -> List[str]:
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
    rows.append('<tr>' + ''.join(header_cells) + '</tr>')
    for row in df.itertuples(index=False, name=None):
        cells = [f'<td class="summary-data">{cell}</td>' for cell in row]
        rows.append('<tr>' + ''.join(cells) + '</tr>')
    table_html = "<table class='summary'>" + ''.join(rows) + "</table>"
    return [table_html]

def _extract_version(fingerprint: str) -> str | None:
    # (unchanged)
    """Extract a version number like 672 from a fingerprint string.
    The version is defined as the token that appears after the fourth '/' and
    before any ':' that may follow. If the pattern cannot be found, return None.
    """
    if not fingerprint:
        return None
    # Find the part after the fourth '/'
    parts = fingerprint.split('/')
    if len(parts) < 5:
        return None
    candidate = parts[4]
    # Remove any trailing ':' and following text
    candidate = candidate.split(':')[0]
    # Keep only digits (the version number)
    import re
    m = re.search(r"(\d+)", candidate)
    return m.group(1) if m else None

def _extract_suite_from_summary(source: str) -> str | None:
    """Extract the suite name (e.g., "CTS") from a summary table in the HTML source.
    The summary table has class "summary" and contains a row like "Suite / Plan".
    This function returns the text left of the first '/' in that cell.
    Returns None if not found.
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
                suite_cell = str(row.iloc[1])
                # The suite name is the part before the first '/' (e.g., "CTS / cts" -> "CTS")
                return suite_cell.split('/')[0].strip()
    return None

def generate_report(


    left_dfs: List[pd.DataFrame],
    right_dfs: List[pd.DataFrame],
    diff_dfs: List[pd.DataFrame],
    left_title: str = "",  # kept for compatibility; not displayed
    right_title: str = "",  # kept for compatibility; not displayed
    output_path: Path = Path("diff.html"),
    left_summary_source: Optional[Union[Path, str]] = None,
    right_summary_source: Optional[Union[Path, str]] = None,
) -> Path:
    """Create a two‑column HTML view showing left & right tables.

    * ``left_dfs`` / ``right_dfs`` – DataFrames extracted from the two HTML files.
    * ``diff_dfs`` – kept for API compatibility, not used.
        * Titles are kept for compatibility but not displayed; summary tables include fingerprints.
    """
    # Build summary tables if sources provided
    left_summary = _make_summary_table(left_summary_source) if left_summary_source else []
    # Fallback: if parsing failed, extract raw summary table via regex
    if not left_summary and left_summary_source:
        try:
            raw_html = Path(left_summary_source).read_text(encoding='utf-8')
            m = re.search(r"<table[^>]*class=['\"]summary['\"][^>]*>.*?</table>", raw_html, re.DOTALL)
            if m:
                left_summary = [m.group(0)]
        except Exception:
            pass
    right_summary = _make_summary_table(right_summary_source) if right_summary_source else []
    if not right_summary and right_summary_source:
        try:
            raw_html = Path(right_summary_source).read_text(encoding='utf-8')
            m = re.search(r"<table[^>]*class=['\"]summary['\"][^>]*>.*?</table>", raw_html, re.DOTALL)
            if m:
                right_summary = [m.group(0)]
        except Exception:
            pass
    # Compute overlap statistics of test names between left and right
    # Extract only actual test names (contain a dot) from the first column of each testdetails DataFrame
    left_tests = {str(val) for df in left_dfs for val in df.iloc[:, 0].astype(str).tolist() if '.' in str(val)}
    right_tests = {str(val) for df in right_dfs for val in df.iloc[:, 0].astype(str).tolist() if '.' in str(val)}
    same_count = len(left_tests & right_tests)
    diff_count = len(left_tests ^ right_tests)
    overlap_summary = f"<table class='summary'><tr><th class='summary-header'>Same testnames</th><td class='summary-data'>{same_count}</td></tr><tr><th class='summary-header'>Degrade testnames</th><td class='summary-data' style='background:#fa5858;'>{diff_count}</td></tr></table>"
    # Compute module overlap statistics
    left_modules = {str(df.iloc[0,0]) for df in left_dfs}
    right_modules = {str(df.iloc[0,0]) for df in right_dfs}
    same_modules = len(left_modules & right_modules)
    degrade_modules = len(left_modules ^ right_modules)
    module_summary = f"<table class='summary'><tr><th class='summary-header'>Same modules</th><td class='summary-data'>{same_modules}</td></tr><tr><th class='summary-header'>Degrade modules</th><td class='summary-data' style='background:#fa5858;'>{degrade_modules}</td></tr></table>"
    # Prepare a simple pie chart for module comparison
    chart_html = "<div class='chart'><canvas id='moduleChart' width='230' height='230' style='width:230px;height:230px;'></canvas></div>"
    chart_script = (
        "<script src='https://cdn.jsdelivr.net/npm/chart.js'></script>"
        "<script>"
        "var ctx=document.getElementById('moduleChart').getContext('2d');"
        f"new Chart(ctx,{{type:'pie',data:{{labels:['Same modules ({same_modules})','Degrade modules ({degrade_modules})'],datasets:[{{data:[{same_modules},{degrade_modules}],backgroundColor:['#4caf50','#f44336']}}]}} ,options:{{responsive:false,maintainAspectRatio:false}}}});"
        "</script>"
    )
    # Build left summary: include left summary, CTS Diff block, chart, and list of degraded module names
    degrade_module_names = left_modules.symmetric_difference(right_modules)
    # Remove the ABI prefix (e.g., "armeabi-v7a") from module names for display
    cleaned_module_names = []
    for name in degrade_module_names:
        # Strip common ABI prefixes and surrounding whitespace
        cleaned = name.replace('armeabi-v7a ', '').replace('armeabi-v7a\u00a0', '').strip()
        cleaned_module_names.append(cleaned)
    degrade_module_names = set(cleaned_module_names)
    degrade_modules_list_html = "<div class='degrade-modules'>Degrade modules:<br>" + '<br>'.join(sorted(degrade_module_names)) + "</div>"
    # Build CTS Diff title with version info if available
    left_version = _extract_version(left_title)
    right_version = _extract_version(right_title)
    # Extract suite name from the source html (left/right) if available
    left_suite = _extract_suite_from_summary(left_summary_source) if left_summary_source else None
    right_suite = _extract_suite_from_summary(right_summary_source) if right_summary_source else None
    suite_name = left_suite or right_suite or "CTS"
    # Build the diff title using versions (if any) and suite name
    # Horizontal divider label (suite name) will be placed above both columns
    horizontal_divider_html = f"<div class='horizontal-divider'><span>{suite_name}</span></div>"
    if left_version and right_version:
        diff_title = f"v{left_version} Vs v{right_version} {suite_name} Diff"
    else:
        diff_title = f"{suite_name} Diff"
        # Divider spanning both columns (placed above each column's summary)
    divider_html = f"<br><div class='horizontal-divider'><span>{suite_name}</span></div>"
    left_summary_combined = (
        divider_html +
        "<div class='summary-wrapper'>"
        "<div class='left-summary'>" + ''.join(left_summary) + "</div>"
        "<div class='right-summary'>"
        f"<div class='cts-diff'>{diff_title}</div>"
        + chart_html + chart_script + degrade_modules_list_html +
        "</div>"
        "</div>"
    )
    # Right side keeps its summary tables (module summary removed)
    right_placeholder = "<div class='right-summary' style='visibility:hidden;'></div>"
    right_summary_combined = (
        divider_html +
        f"<div class='summary-wrapper'><div class='left-summary'>{''.join(right_summary)}</div>{right_placeholder}</div>"
    )

    parts = [
        HTML_HEADER,
        f"<div class='col'>",
        f"<h2>{left_title}</h2>" if left_title else "",
        left_summary_combined,
        *[_make_table(df) for df in left_dfs],
        "</div>",
        f"<div class='col'>",
        f"<h2>{right_title}</h2>" if right_title else "",
        right_summary_combined,
        *[_make_table(df) for df in right_dfs],
        "</div>",
        HTML_FOOTER,
    ]
    # 写入文件一次性完成
    # Ensure parent directory exists
    output_path.parent.mkdir(parents=True, exist_ok=True)
    final_path = output_path
    try:
        output_path.write_text("\n".join(parts), encoding="utf-8")
    except PermissionError:
        # Fallback to a writable location in /tmp
        fallback_dir = Path(tempfile.gettempdir()) / "diff_output"
        fallback_dir.mkdir(parents=True, exist_ok=True)  # Ensure writable directory in /tmp
        fallback_path = fallback_dir / f"{output_path.stem}.html"
        logging.warning("Permission denied writing to %s; writing to %s instead.", output_path, fallback_path)
        fallback_path.write_text("\n".join(parts), encoding="utf-8")
        final_path = fallback_path
    return final_path