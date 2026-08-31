"""Catalogue import from the admin panel (CATALOGUE-CMS-PLAN.md §8.3).

This endpoint is the difference between a one-off migration and a workflow.
The CLI (`scripts/import_catalogue.py`) loads next cycle's spreadsheet fine,
but only a developer can run it — so without this, next September is another
developer task rather than something the admissions team does themselves.

Upload the `.xlsx` → it is extracted and diffed → the summary comes back → the
same call with `apply=true` writes it. Both paths run exactly the same code as
the CLI, via `scripts.import_catalogue.import_into`.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from ..api.auth import require_role
from ..api.deps import get_db_session
from ..api.exceptions import BadRequestException
from ..core.config import get_settings
from ..models.enums import UserRole
from ..schemas.imports import ImportPreview, ImportRow

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

router = APIRouter(prefix="/imports", tags=["Imports"])

#: Only the account's admins. An import rewrites the entire public catalogue in
#: one call, which is a wider blast radius than editing a record.
_RUN_IMPORT = require_role(UserRole.ADMIN)

_MAX_WORKBOOK_MB = 25


@router.post("/catalogue", response_model=ImportPreview, summary="Import a catalogue workbook")
async def import_catalogue(
    file: UploadFile = File(...),
    apply: bool = Form(False),
    session: AsyncSession = Depends(get_db_session),
    _: object = Depends(_RUN_IMPORT),
) -> ImportPreview:
    """Extract, diff and — with `apply=true` — write.

    The default is a dry run on purpose: the diff is what a human is supposed
    to read before four thousand rows change.
    """
    from scripts.extract_xlsx import extract
    from scripts.import_catalogue import import_into

    settings = get_settings()
    if settings.is_production:
        raise BadRequestException("Catalogue import is disabled in production")

    if not (file.filename or "").lower().endswith(".xlsx"):
        raise BadRequestException("Expected an .xlsx workbook")

    content = await file.read()
    if not content:
        raise BadRequestException("Uploaded file is empty")
    if len(content) > _MAX_WORKBOOK_MB * 1024 * 1024:
        raise BadRequestException(f"Workbook exceeds the {_MAX_WORKBOOK_MB} MB limit")

    with tempfile.TemporaryDirectory() as scratch:
        root = Path(scratch)
        workbook = root / "catalogue.xlsx"
        workbook.write_bytes(content)
        out_dir = root / "out"

        try:
            extracted = extract(workbook, out_dir)
        except Exception as error:  # noqa: BLE001 - the file came from a user
            raise BadRequestException(f"Could not read the workbook: {error}") from error

        unmapped = sorted(set(extracted["master_unmapped"]))
        problems = list(extracted["problems"])
        # A name the alias map does not know would otherwise become a brand new
        # university. Refuse rather than guess — the fix is one line in
        # `scripts/catalogue_source.py`.
        if unmapped:
            raise BadRequestException(
                "Unrecognised university names in the workbook: " + ", ".join(unmapped)
            )

        report = await import_into(session, data_dir=out_dir, dry_run=not apply)
        report_text = (out_dir / "report.md").read_text() if (out_dir / "report.md").exists() else ""

    rows = [
        ImportRow(entity=entity, **counts)
        for entity, counts in report.rows.items()
    ]
    return ImportPreview(
        applied=apply,
        universities=len(extracted["universities"]),
        routes=len(extracted["routes"]),
        offerings=len(extracted["offerings"]),
        rows=rows,
        problems=problems,
        report=report_text,
    )
