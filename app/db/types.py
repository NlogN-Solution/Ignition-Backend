from enum import Enum as PyEnum

from sqlalchemy import Enum as SQLEnum


def enum_type(enum_cls: type[PyEnum], name: str, create_type: bool = False, **kwargs):  # type: ignore[no-untyped-def]
    """Postgres ENUM column bound to a Python enum, keyed on `.value`.

    **`create_type` is inert.** It is a `postgresql.ENUM` parameter, but this
    builds a generic `sa.Enum`, which absorbs unrecognised keywords as dialect
    options and never reads it. Verified: the returned object has no
    `create_type` attribute at all.

    So the real behaviour — for both `metadata.create_all` and Alembic's
    `op.create_table` — is that **table DDL emits `CREATE TYPE` for you**:

    - `tests/conftest.py` can `create_all` against an empty database.
    - The initial migration needs no hand-written `CREATE TYPE` block.
    - But `op.drop_table` does *not* drop the types, so every migration that
      introduces an enum must drop it explicitly in `downgrade()` or the next
      upgrade fails with `DuplicateObject`. See `ENUM_TYPES` in
      `migrations/versions/f7f30062eb35_initial_schema.py`.

    The parameter is kept only so ED360's model code ports over unchanged; do
    not add new call sites that pass it, and do not make it functional — the
    test suite's `create_all` depends on types being emitted by table DDL.
    """
    return SQLEnum(
        enum_cls,
        name=name,
        create_type=create_type,
        values_callable=lambda enum: [member.value for member in enum],
        **kwargs,
    )
