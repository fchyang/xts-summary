import pathlib
import re
from typing import List, Tuple

import bs4
import requests

def _load_html(source: str) -> str:
    """Load HTML from a local file or an HTTP/HTTPS URL."""
    if source.startswith(("http://", "https://")):
        resp = requests.get(source, timeout=10)
        resp.raise_for_status()
        return resp.text
    else:
        path = pathlib.Path(source).expanduser()
        return path.read_text(encoding="utf-8")

def _parse_fingerprint(soup: bs4.BeautifulSoup) -> str:
    """Extract the first “Fingerprint” token found in the document."""
    text = soup.get_text()
    match = re.search(r"Fingerprint[:\s]*([^\s<]+)", text, re.I)
    return match.group(1) if match else "Untitled"

def extract_testdetails(source: str) -> Tuple[str, List[bs4.Tag]]:
    """Return (fingerprint, tables) where tables are <table class='testdetails'> elements."""
    html = _load_html(source)
    soup = bs4.BeautifulSoup(html, "lxml")
    fingerprint = _parse_fingerprint(soup)
    # Collect both 'testdetails' and optional 'incompletemodules' tables, preserving order (testdetails first).
    testdetail_tables = [tbl for tbl in soup.find_all("table") if "testdetails" in (tbl.get("class") or [])]
    incompletemodule_tables = [tbl for tbl in soup.find_all("table") if "incompletemodules" in (tbl.get("class") or [])]
    tables = testdetail_tables + incompletemodule_tables
    return fingerprint, tables
