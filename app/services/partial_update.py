from __future__ import annotations

from typing import Any

from ..api.exceptions import BadRequestException


def reject_null_on_required(model: type[Any], data: dict[str, Any]) -> None:
    """Guard a PATCH payload before it is applied to `model`.

    Callers pass `exclude_unset=True`, so a key being present means the client
    sent it — and an explicit null means "clear this". ED360's services instead
    skip every `None`, which quietly makes optional fields unclearable:
    `{"department_id": null}` returns 200 and changes nothing, so a staff member
    can be moved between departments but never out of one.

    Applying nulls verbatim would trade that for a NOT NULL violation surfacing
    as a 500, so required columns are rejected here with a 400 instead. What
    remains is the useful case: nulling a nullable column clears it.
    """
    columns = model.__table__.columns
    offenders = [key for key, value in data.items() if value is None and key in columns and not columns[key].nullable]
    if offenders:
        raise BadRequestException(f"These fields cannot be null: {', '.join(sorted(offenders))}")
