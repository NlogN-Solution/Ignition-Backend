"""Schemas for the catalogue import endpoint."""

from __future__ import annotations

from pydantic import BaseModel


class ImportRow(BaseModel):
    entity: str
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    retired: int = 0


class ImportPreview(BaseModel):
    """What an import did, or would do.

    `applied=False` is a dry run: the transaction was rolled back and these are
    the counts a real run would produce.
    """

    applied: bool
    universities: int
    routes: int
    offerings: int
    rows: list[ImportRow]
    #: Blocking problems found during extraction, if any.
    problems: list[str] = []
    #: The full `report.md` — the derivations a human is meant to review.
    report: str = ""
