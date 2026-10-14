"""The tables of an HTML page, with the heading text just before each (stdlib parser; SPEC §5.2 HTML sources)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser

HEADINGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
TAIL = 300


@dataclass
class Cell:
    text: str
    hrefs: list[str] = field(default_factory=list)


@dataclass
class Table:
    heading: str  # the last heading's text before the table ("" if none)
    rows: list[list[Cell]]
    before: str = ""  # the last ~300 characters of visible text before the table (titles set in bold, not <h*>)


class _Parser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables: list[Table] = []
        self.heading = ""
        self._in_heading = False
        self._heading_buf: list[str] = []
        self._table: Table | None = None
        self._row: list[Cell] | None = None
        self._cell: Cell | None = None
        self._buf: list[str] = []
        self._depth = 0  # nested tables are folded into the outer one
        self._tail = ""  # visible text outside tables, trimmed to the last TAIL characters
        self._skip = 0  # inside <script> or <style>

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1
        if tag in HEADINGS and self._table is None:
            self._in_heading, self._heading_buf = True, []
        elif tag == "table":
            self._depth += 1
            if self._depth == 1:
                self._table = Table(self.heading, [], _clean(self._tail)[-TAIL:])
        elif self._table is not None and tag == "tr":
            self._row = []
        elif self._row is not None and tag in ("td", "th"):
            self._cell, self._buf = Cell(""), []
        elif self._cell is not None and tag == "a":
            href = dict(attrs).get("href")
            if href:
                self._cell.hrefs.append(href)

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self._skip:
            self._skip -= 1
        if tag in HEADINGS and self._in_heading:
            self.heading = _clean("".join(self._heading_buf))
            self._in_heading = False
        elif tag in ("td", "th") and self._cell is not None and self._row is not None:
            self._cell.text = _clean("".join(self._buf))
            self._row.append(self._cell)
            self._cell = None
        elif tag == "tr" and self._row is not None and self._table is not None:
            if self._row:
                self._table.rows.append(self._row)
            self._row = None
        elif tag == "table" and self._depth:
            self._depth -= 1
            if self._depth == 0 and self._table is not None:
                self.tables.append(self._table)
                self._table = None

    def handle_data(self, data):
        if self._skip:
            return
        if self._table is None:
            self._tail = (self._tail + " " + data)[-2 * TAIL :]
        if self._in_heading:
            self._heading_buf.append(data)
        if self._cell is not None:
            self._buf.append(data)


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()


def tables(html: str) -> list[Table]:
    p = _Parser()
    p.feed(html)
    p.close()
    return p.tables
