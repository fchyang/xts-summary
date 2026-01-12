import pandas as pd
import logging
from bs4 import Tag
from typing import Tuple

def _table_to_df(table: Tag) -> pd.DataFrame:
    """Convert a BeautifulSoup ``<table>`` element into a pandas DataFrame.
    Handles missing headers and irregular column counts.
    """
    rows: list[list[str]] = []
    for tr in table.find_all("tr"):
        cells = [c.get_text(strip=True) for c in tr.find_all(["td", "th"])]
        rows.append(cells)
    if not rows:
        return pd.DataFrame()
    max_len = max(len(r) for r in rows)
    rows = [r + [""] * (max_len - len(r)) for r in rows]
    has_header = bool(table.find("tr").find_all("th"))
    if has_header and len(rows[0]) == max_len:
        header = rows[0]
        data = rows[1:]
        return pd.DataFrame(data, columns=header)
    else:
        col_names = [f"col_{i}" for i in range(max_len)]
        return pd.DataFrame(rows, columns=col_names)

def compare_tables(left_tbl: Tag, right_tbl: Tag) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Return left_df, right_df, diff_df highlighting cell differences.
    Align both index and columns (outer join) before calling ``DataFrame.compare``
    so pandas can operate on identically‑labeled frames.
    """
    try:
        left_df = _table_to_df(left_tbl)
        right_df = _table_to_df(right_tbl)
        # Align rows and columns together
        left_df, right_df = left_df.align(right_df, join="outer", axis=None, fill_value="")
        diff_df = left_df.compare(right_df, keep_equal=False)
    except Exception as e:
        log = logging.getLogger(__name__)
        log.error("Error comparing tables: %s", e)
        left_df = pd.DataFrame()
        right_df = pd.DataFrame()
        diff_df = pd.DataFrame()
    return left_df, right_df, diff_df
