"""One spelling per mailbox.

`users.email` used to be compared byte-for-byte, and Pydantic's `EmailStr`
lower-cases only the domain — so `Sec.Case@x.com` and `sec.case@x.com` were two
accounts for one mailbox (FAPI-SEC-009). Every inbound address is now trimmed
and lower-cased here, and `users` carries a unique index on `lower(email)` as
the backstop (see `models/user.py` and migration `b4e1d9c2a7f3`).

Lower-casing the local part is technically lossy — RFC 5321 lets a server treat
`Bob` and `bob` as different mailboxes — but no mainstream provider does, and
the alternative is exactly the duplicate-identity bug this closes.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import AfterValidator, BeforeValidator, EmailStr


def normalise_email(value: str) -> str:
    return value.strip().lower()


def _strip(value: object) -> object:
    return value.strip() if isinstance(value, str) else value


#: Use for every request field that carries an account e-mail address.
NormalizedEmail = Annotated[EmailStr, BeforeValidator(_strip), AfterValidator(normalise_email)]
