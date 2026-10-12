"""Structure-only extraction from HTML/JSON: table headers, row counts, key paths, links.

Nothing here returns cell values except `table_excerpts`, which callers must only use
for public (non-private) sources (SPEC.md §7, §8 Phase 1).
"""

from __future__ import annotations

import json
import re
from html.parser import HTMLParser


class _TableParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables: list[dict] = []
        self._stack: list[dict] = []  # nested tables
        self._cell: list[str] | None = None
        self._cell_is_th = False
        self._row: list[tuple[bool, str]] | None = None
        self._section = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "table":
            self._stack.append({"id": a.get("id"), "class": a.get("class"), "rows": [], "thead_rows": []})
        elif not self._stack:
            return
        elif tag in ("thead", "tbody", "tfoot"):
            self._section = tag
        elif tag == "tr":
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []
            self._cell_is_th = tag == "th"

    def handle_endtag(self, tag):
        if not self._stack:
            return
        if tag in ("td", "th") and self._cell is not None and self._row is not None:
            text = re.sub(r"\s+", " ", "".join(self._cell)).strip()
            self._row.append((self._cell_is_th, text))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            table = self._stack[-1]
            target = "thead_rows" if self._section == "thead" else "rows"
            table[target].append(self._row)
            self._row = None
        elif tag in ("thead", "tbody", "tfoot"):
            self._section = None
        elif tag == "table":
            self.tables.append(self._stack.pop())

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)


def _split_header(table: dict) -> tuple[list[str], list[list[str]]]:
    head_rows = table["thead_rows"]
    body = table["rows"]
    if head_rows:
        headers = [t for _, t in head_rows[-1]]
    elif body and all(is_th for is_th, _ in body[0]):
        headers = [t for _, t in body[0]]
        body = body[1:]
    else:
        headers = []
    return headers, [[t for _, t in row] for row in body if row]


def html_tables(html: str) -> list[dict]:
    """Per table: id, class, header labels and the number of data rows. No cell values."""
    parser = _TableParser()
    parser.feed(html)
    out = []
    for t in parser.tables:
        headers, rows = _split_header(t)
        out.append({"id": t["id"], "class": t["class"], "headers": headers, "row_count": len(rows)})
    return out


def table_excerpts(html: str, max_rows: int = 5) -> list[dict]:
    """Headers plus the first rows of each table. PUBLIC SOURCES ONLY."""
    parser = _TableParser()
    parser.feed(html)
    out = []
    for t in parser.tables:
        headers, rows = _split_header(t)
        out.append({"id": t["id"], "headers": headers, "rows": rows[:max_rows], "row_count": len(rows)})
    return out


def json_key_paths(obj, max_depth: int = 6, max_paths: int = 400) -> list[str]:
    """Distinct key paths with value types; list indices collapse to []. No values."""
    seen: dict[str, str] = {}

    def walk(o, path: str, depth: int):
        if len(seen) >= max_paths:
            return
        if isinstance(o, dict):
            seen.setdefault(path or "$", "object")
            if depth >= max_depth:
                return
            for k, v in o.items():
                walk(v, f"{path}.{k}" if path else str(k), depth + 1)
        elif isinstance(o, list):
            seen.setdefault(f"{path}[]", "array")
            if depth >= max_depth:
                return
            for v in o[:50]:
                walk(v, f"{path}[]", depth + 1)
        else:
            seen.setdefault(path or "$", type(o).__name__)

    walk(obj, "", 0)
    return [f"{p}: {t}" for p, t in sorted(seen.items())]


def find_js_json(html: str, var_name: str):
    """Parse the JSON literal assigned to a JS variable (e.g. `var ecrData = {...};`)."""
    m = re.search(rf"\b{re.escape(var_name)}\s*=\s*", html)
    if not m:
        return None
    i = m.end()
    if i >= len(html) or html[i] not in "{[":
        return None
    opener = html[i]
    closer = "}" if opener == "{" else "]"
    depth = 0
    in_str: str | None = None
    escaped = False
    for j in range(i, len(html)):
        c = html[j]
        if in_str:
            if escaped:
                escaped = False
            elif c == "\\":
                escaped = True
            elif c == in_str:
                in_str = None
            continue
        if c in "\"'":
            in_str = c
        elif c == opener:
            depth += 1
        elif c == closer:
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(html[i : j + 1])
                except json.JSONDecodeError:
                    return None
    return None


class _LinkParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self._href = dict(attrs).get("href")
            self._text = []

    def handle_data(self, data):
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self._href is not None:
            self.links.append((self._href, re.sub(r"\s+", " ", "".join(self._text)).strip()))
            self._href = None


def matching_links(html: str, pattern: str, limit: int = 10) -> list[dict]:
    """Anchors whose href or text matches `pattern` (case-insensitive), de-duplicated."""
    rx = re.compile(pattern, re.IGNORECASE)
    parser = _LinkParser()
    parser.feed(html)
    out, seen = [], set()
    for href, text in parser.links:
        if href and href not in seen and (rx.search(href) or rx.search(text)):
            seen.add(href)
            out.append({"href": href, "text": text[:160]})
            if len(out) >= limit:
                break
    return out


def markers(html: str, patterns: dict[str, str]) -> dict[str, bool]:
    return {name: bool(re.search(rx, html, re.IGNORECASE)) for name, rx in patterns.items()}


def week_labels(html: str) -> list[int]:
    """Distinct 'Week N' numbers mentioned on a page (a structural label, not data)."""
    return sorted({int(n) for n in re.findall(r"\bWeek\s+(\d{1,2})\b", html, re.IGNORECASE) if 1 <= int(n) <= 22})
