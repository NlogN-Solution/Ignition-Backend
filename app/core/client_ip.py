"""Who is actually on the other end of the socket.

Behind Render's proxy, `request.client.host` is the proxy, not the caller —
every visitor shares one address. That turned every per-IP rate limit into a
single global bucket an anonymous script could drain (ten bad logins a minute
locked the whole console out of `/auth/login`), and wrote the proxy's address
into every audit-log row.

The fix is *not* uvicorn's `--forwarded-allow-ips='*'`. With `*`, uvicorn
trusts the **leftmost** `X-Forwarded-For` entry, and the leftmost entry is
whatever the client typed: `curl -H 'X-Forwarded-For: 1.2.3.4'` would give an
attacker a fresh rate-limit bucket per request. Each proxy *appends* to the
header, so the only entries that can be trusted are the ones the proxies
themselves added — counted from the right.

`TRUSTED_PROXY_HOPS` is that count. With N trusted proxies in front of the
app, the client is the Nth entry from the right. Getting N wrong has an
asymmetric cost, which is why the default is 0 and production sets 1:

* too low  → you key on a proxy's address (the old, safe-but-coarse behaviour);
* too high → you key on a client-supplied value (spoofable).

`CLIENT_IP_HEADER` is the alternative for platforms whose edge *overwrites* a
single header with the connecting address (e.g. Cloudflare's
`CF-Connecting-IP`). Only set it if the app cannot be reached except through
that edge — otherwise a direct caller sets it themselves.

Anything that is not a syntactically valid IP falls back to the socket peer:
these values end up in `INET` columns, and a garbage header must never become
a 500.
"""

from __future__ import annotations

import ipaddress

from starlette.requests import Request

from .config import get_settings


def _valid_ip(value: str | None) -> str | None:
    if not value:
        return None
    candidate = value.strip()
    # "[2001:db8::1]:443" and "203.0.113.7:51234" both appear in the wild.
    if candidate.startswith("[") and "]" in candidate:
        candidate = candidate[1 : candidate.index("]")]
    elif candidate.count(":") == 1:
        candidate = candidate.split(":", 1)[0]
    try:
        return str(ipaddress.ip_address(candidate))
    except ValueError:
        return None


def client_ip(request: Request) -> str | None:
    """The best trustworthy guess at the caller's address, or None."""
    settings = get_settings()
    peer = _valid_ip(request.client.host) if request.client else None

    if settings.CLIENT_IP_HEADER:
        from_header = _valid_ip(request.headers.get(settings.CLIENT_IP_HEADER))
        if from_header:
            return from_header

    hops = settings.TRUSTED_PROXY_HOPS
    if hops > 0:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            entries = [entry.strip() for entry in forwarded.split(",") if entry.strip()]
            if len(entries) >= hops:
                resolved = _valid_ip(entries[-hops])
                if resolved:
                    return resolved
    return peer


def rate_limit_key(request: Request) -> str:
    """slowapi key function. Never empty: an unknown caller shares one bucket
    rather than escaping the limit altogether."""
    return client_ip(request) or "unknown"
