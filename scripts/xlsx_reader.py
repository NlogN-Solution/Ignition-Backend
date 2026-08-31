"""Minimal stdlib .xlsx reader.

openpyxl is not a runtime dependency of this backend and is not needed: the
workbook is read straight out of the zip container. Only what the catalogue
extractor needs is implemented — shared strings, inline strings, and cell
values by column letter.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"

Row = tuple[int, dict[str, str]]


def collapse(value: str | None) -> str:
    return re.sub(r"\s+", " ", (value or "").strip())


def key(value: str | None) -> str:
    """Collapsed, upper-cased — the form every lookup table is keyed by."""
    return collapse(value).upper()


class Workbook:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._zip = zipfile.ZipFile(self.path)
        self._shared = [
            "".join(t.text or "" for t in si.iter(NS + "t"))
            for si in ET.fromstring(self._zip.read("xl/sharedStrings.xml"))
        ]
        rels = {
            rel.get("Id"): rel.get("Target")
            for rel in ET.fromstring(self._zip.read("xl/_rels/workbook.xml.rels"))
        }
        book = ET.fromstring(self._zip.read("xl/workbook.xml"))
        self.sheets: list[tuple[str, str]] = [
            (sheet.get("name"), rels[sheet.get(R + "id")]) for sheet in book.iter(NS + "sheet")
        ]
        self._by_key = {key(name): target for name, target in self.sheets}

    def sheet_names(self) -> list[str]:
        return [name for name, _ in self.sheets]

    def rows(self, name: str) -> list[Row]:
        target = self._by_key[key(name)]
        return self._rows_at(target)

    def _rows_at(self, target: str) -> list[Row]:
        path = target if target.startswith("xl/") else "xl/" + target.lstrip("/")
        out: list[Row] = []
        for row in ET.fromstring(self._zip.read(path)).iter(NS + "row"):
            cells: dict[str, str] = {}
            for cell in row:
                kind = cell.get("t")
                if kind == "inlineStr":
                    node = cell.find(NS + "is")
                    value = "".join(t.text or "" for t in node.iter(NS + "t")) if node is not None else ""
                else:
                    node = cell.find(NS + "v")
                    if node is None:
                        continue
                    value = self._shared[int(node.text)] if kind == "s" else (node.text or "")
                if value is None:
                    continue
                column = "".join(ch for ch in cell.get("r", "") if ch.isalpha())
                if column:
                    cells[column] = value
            out.append((int(row.get("r")), cells))
        return out

    def hyperlinks(self, name: str) -> list[str]:
        """External link targets declared for a sheet, in document order.

        The flyer links are relationship targets rather than cell values, so
        they are invisible to a cell-level read.
        """
        target = self._by_key[key(name)]
        stem = target.split("/")[-1]
        rel_path = f"xl/worksheets/_rels/{stem}.rels"
        if rel_path not in self._zip.namelist():
            return []
        body = self._zip.read(rel_path).decode("utf8")
        return re.findall(r'Target="(https?://[^"]+)"', body)
