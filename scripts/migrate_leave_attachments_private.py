"""Move leave attachments uploaded before FAPI-SEC-006 off public delivery.

Until the fix, `POST /leave-requests` stored its attachment (typically a
medical note) as a *public* Cloudinary `upload` asset and kept the public URL
on the row. This script, for every such row:

1. renames the asset from delivery type `upload` to `authenticated` in place
   (same public id), which makes the old public URL stop working;
2. records the stored name in `attachment_stored_file_name` and clears
   `attachment_url`, so the API serves it only through the owner-or-manager
   route `GET /leave-requests/{id}/attachment`.

    python -m scripts.migrate_leave_attachments_private            # dry run
    python -m scripts.migrate_leave_attachments_private --apply

Needs the production CLOUDINARY_* credentials and DATABASE_URL in the
environment. Idempotent: rows already migrated have no `attachment_url` and
are skipped. Rows whose URL cannot be parsed are reported and left alone.
Note that CDN copies of the public URL may survive for a while after the
rename; invalidate them from the Cloudinary console if that matters.
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cloudinary.uploader  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.core.uploads import LEAVE_ATTACHMENT_FOLDER, _resource_type_for  # noqa: E402
from app.db.session import session_factory  # noqa: E402
from app.models import LeaveRequest  # noqa: E402

#: The generated name `store_upload` gives every asset: a UUID plus extension.
_STORED_NAME = re.compile(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\.[A-Za-z0-9]+)")


async def main(apply: bool) -> None:
    moved = skipped = failed = 0
    async with session_factory() as session:
        rows = (
            await session.scalars(
                select(LeaveRequest).where(
                    LeaveRequest.attachment_url.is_not(None),
                    LeaveRequest.attachment_stored_file_name.is_(None),
                )
            )
        ).all()
        for row in rows:
            match = _STORED_NAME.search(row.attachment_url or "")
            if match is None:
                print(f"SKIP  {row.id}: cannot parse a stored name from its URL")
                skipped += 1
                continue
            stored_name = match.group(1)
            public_id = f"{LEAVE_ATTACHMENT_FOLDER}/{stored_name}"
            resource_type = _resource_type_for(Path(stored_name).suffix.lower())
            print(f"{'MOVE' if apply else 'WOULD MOVE'}  {row.id}: {public_id} ({resource_type})")
            if not apply:
                continue
            try:
                await asyncio.to_thread(
                    cloudinary.uploader.rename,
                    public_id,
                    public_id,
                    type="upload",
                    to_type="authenticated",
                    resource_type=resource_type,
                    overwrite=False,
                    invalidate=True,
                )
            except Exception as exc:  # report and carry on; the row is left untouched
                print(f"FAIL  {row.id}: {exc}")
                failed += 1
                continue
            row.attachment_stored_file_name = stored_name
            row.attachment_url = None
            await session.commit()
            moved += 1
    print(f"done: moved={moved} skipped={skipped} failed={failed}{'' if apply else ' (dry run)'}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="actually rename assets and update rows")
    asyncio.run(main(parser.parse_args().apply))
