"""Work out the real client IP behind a reverse proxy.

The rate limiter used to key on ``request.client.host``. On Render (and Vercel,
and any other PaaS) that is the *proxy's* address, so every user in the world
shared a single bucket and the guest limit locked out the whole product after a
few requests.
"""

import hashlib
import ipaddress
from typing import Optional

from fastapi import Request

from app.core.config import settings


def _valid_ip(candidate: str) -> Optional[str]:
    candidate = candidate.strip()
    if not candidate:
        return None
    # X-Forwarded-For entries are occasionally "ip:port" for IPv4.
    if candidate.count(":") == 1 and "." in candidate:
        candidate = candidate.rsplit(":", 1)[0]
    try:
        return str(ipaddress.ip_address(candidate))
    except ValueError:
        return None


def get_client_ip(request: Request) -> str:
    """Return the caller's IP address.

    ``X-Forwarded-For`` is append-only: each proxy adds the address it saw to the
    right-hand end, so with N trusted proxies in front of us the caller is the
    Nth entry counting from the right. Anything further left was supplied by the
    caller itself and is trivially spoofable — taking ``parts[0]``, the obvious
    reading, is the classic way to make a rate limiter bypassable.
    """
    forwarded = request.headers.get("X-Forwarded-For")
    hops = max(settings.TRUSTED_PROXY_HOPS, 0)

    if forwarded and hops > 0:
        parts = [p for p in (p.strip() for p in forwarded.split(",")) if p]
        if parts:
            index = max(len(parts) - hops, 0)
            ip = _valid_ip(parts[index])
            if ip:
                return ip
            # Header present but unusable: fall through rather than trusting it.

    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def client_key(request: Request) -> str:
    """Return a stable, non-identifying key for rate-limit bookkeeping.

    Raw IP addresses are personal data; there is no reason to keep them sitting
    in a long-lived in-memory map when a digest works just as well as a key.
    """
    return hashlib.sha256(get_client_ip(request).encode("utf-8")).hexdigest()[:32]
