
from pathlib import Path
import pandas as pd
from typing import List

# -------------------------------------------------
# 常量区（HTML 结构、CSS、模板）
# -------------------------------------------------
HTML_HEADER = """<!DOCTYPE html>
<html><head><meta charset='utf-8'><title>Table Diff</title>
<style>
body {font-family:Arial, sans-serif; margin:0; padding:0;}
.container {display:flex; width:100%;}
.col {flex:1; padding:10px; box-sizing:border-box; overflow:auto;}
table {border-collapse:collapse; margin:10px 0; width:100%;}
th, td {border:1px solid #aaa; padding:4px 8px;}
h2 {margin-top:0;}
.testdetails td.module   {background:none;}
.testdetails th.module   {background:none;}
.testdetails td.testname {background:#d4e9a9;}
.testdetails td.failed   {background:#fa5858;}
.testdetails td.failuredetails {background:#d4e9a9;}
.testdetails th          {background:#a5c639 !important;}
.testdetails th.module   {background:none !important;}
</style></head><body>
<div class='container'>"""
HTML_FOOTER = """</div></body></html>"""
MODULE_ROW_TMPL = '<tr><th colspan="3" class="module" style="text-align:left;">{module}</th></tr>'
TABLE_HEADER   = '<tr><th>Test</th><th>Result</th><th>Details</th></tr>'

def _make_table(df: pd.DataFrame) -> str:
    """Convert a DataFrame into the custom HTML table.
    第一行被视作模块名称，随后为测试数据。
    """
    rows = df.values.tolist()
    if not rows:
        return "<table class='testdetails'></table>"

    # 模块标题（左对齐、无背景）
    module_name = rows[0][0]
    parts = [MODULE_ROW_TMPL.format(module=module_name), TABLE_HEADER]

    for test, result, details in rows[1:]:
        # 跳过可能出现的多余标题行
        if test == "Test" and result == "Result" and details == "Details":
            continue
        col_class = "testname" if "." in test else "module"
        test_td = f'<td class="{col_class}">{test}</td>'
        if result.strip().lower() == "fail":
            result_td  = f'<td class="failed">{result}</td>'
            details_td = f'<td class="failuredetails">{details}</td>'
        else:
            result_td  = f'<td>{result}</td>'
            details_td = f'<td>{details}</td>'
        parts.append(f'<tr>{test_td}{result_td}{details_td}</tr>')

    return "<table class='testdetails'>" + "".join(parts) + "</table>"

def generate_report(
    left_dfs: List[pd.DataFrame],
    right_dfs: List[pd.DataFrame],
    diff_dfs: List[pd.DataFrame],
    left_title: str,
    right_title: str,
    output_path: Path,
) -> None:
    """Create a two‑column HTML view showing left & right tables.

    * ``left_dfs`` / ``right_dfs`` – DataFrames extracted from the two HTML files.
    * ``diff_dfs`` – kept for API compatibility, not used.
    * ``left_title`` / ``right_title`` – titles shown above each column.
    """
    parts = [
        HTML_HEADER,
        f"<div class='col'><h2>{left_title}</h2>",
        *[_make_table(df) for df in left_dfs],
        "</div>",
        f"<div class='col'><h2>{right_title}</h2>",
        *[_make_table(df) for df in right_dfs],
        "</div>",
        HTML_FOOTER,
    ]
    # 写入文件一次性完成
    output_path.write_text("\n".join(parts), encoding="utf-8")