"""Supabase JWT verification.

Before this module existed, the backend decided whether a caller was authenticated
by checking that the ``Authorization`` header was merely non-empty. The token was
never validated and the user id was hardcoded to the string ``"user_placeholder"``,
so any request carrying a junk header was treated as a signed-in user.

Verification goes through Supabase's own ``auth.get_user`` rather than decoding the
JWT locally. Supabase projects sign with HS256 (legacy shared secret) or with
asymmetric ES256/RS256 keys depending on when the project was created and whether it
has been migrated; a local decoder would have to handle both and would silently fail
against whichever regime it did not expect. Asking Supabase is correct for either.

The cost is one network round trip per token, which the short-lived cache below
absorbs for the rest of a conversation.
"""

import asyncio
import hashlib
import time
import uuid
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

import httpx
from fastapi import HTTPException, Request
from supabase import AsyncClient, acreate_client
from supabase_auth.errors import AuthRetryableError

from app.core.config import settings
from app.core.logger import logger

# How long a successful verification is trusted before we ask Supabase again.
# Short enough that a signed-out or revoked token stops working promptly, long
# enough that a burst of requests in one conversation costs a single round trip.
_CACHE_TTL_SECONDS = 60
_CACHE_MAX_ENTRIES = 256

# A hanging auth service must not tie up the single free-tier worker.
_VERIFY_TIMEOUT_SECONDS = 8

# Exceptions that mean "Supabase is unreachable or broken", as opposed to
# "this token is not valid". supabase-auth raises AuthRetryableError for
# 502/503/504/520-530 and for transport failures; raw httpx errors can still
# escape, since its base API only wraps HTTPStatusError and RuntimeError.
_UNAVAILABLE_ERRORS = (
    AuthRetryableError,
    httpx.TransportError,
    asyncio.TimeoutError,
)

# token digest -> (expires_at, user)
_verify_cache: Dict[str, Tuple[float, "AuthenticatedUser"]] = {}

_auth_client: Optional[AsyncClient] = None


@dataclass(frozen=True)
class AuthenticatedUser:
    """A caller whose token Supabase has confirmed."""

    id: str
    email: Optional[str]
    access_token: str


def _digest(token: str) -> str:
    """Cache key for a token. We never keep the raw token as a dict key."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _cache_get(token: str) -> Optional[AuthenticatedUser]:
    entry = _verify_cache.get(_digest(token))
    if not entry:
        return None
    expires_at, user = entry
    if expires_at < time.monotonic():
        _verify_cache.pop(_digest(token), None)
        return None
    return user


def _cache_put(token: str, user: AuthenticatedUser) -> None:
    if len(_verify_cache) >= _CACHE_MAX_ENTRIES:
        # Cheap bounded eviction; this is a latency cache, not a correctness one.
        oldest = min(_verify_cache, key=lambda k: _verify_cache[k][0])
        _verify_cache.pop(oldest, None)
    _verify_cache[_digest(token)] = (time.monotonic() + _CACHE_TTL_SECONDS, user)


def reset_auth_cache() -> None:
    """Drop cached verifications. Used by tests."""
    _verify_cache.clear()


async def _get_auth_client() -> AsyncClient:
    global _auth_client
    if _auth_client is None:
        _auth_client = await acreate_client(settings.SUPABASE_URL, settings.SUPABASE_ANON_KEY)
    return _auth_client


def extract_bearer_token(request: Request) -> Optional[str]:
    """Pull the bearer token out of the Authorization header, if there is one."""
    header = request.headers.get("Authorization")
    if not header:
        return None
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer":
        return None
    token = token.strip()
    # The frontend must omit the header entirely for guests. Guard against a
    # literal "Bearer null"/"Bearer undefined" reaching us as a valid-looking token.
    if not token or token in {"null", "undefined"}:
        return None
    return token


async def verify_token(token: str) -> AuthenticatedUser:
    """Verify a Supabase access token. Raises HTTPException on failure.

    A 401 and a 503 mean very different things to the caller, and conflating them
    is what turns a brief Supabase outage into a refresh storm: the client reads
    401 as "your credentials are bad", discards or refreshes its session, and
    retries against a service that is simply down. So an unreachable auth service
    is reported as 503 and only an actual rejection as 401.
    """
    cached = _cache_get(token)
    if cached is not None:
        return cached

    try:
        client = await _get_auth_client()
        response = await asyncio.wait_for(
            client.auth.get_user(token), timeout=_VERIFY_TIMEOUT_SECONDS
        )
    except _UNAVAILABLE_ERRORS as exc:
        logger.warning(f"Supabase auth is unreachable; cannot verify tokens: {exc}")
        raise HTTPException(
            status_code=503,
            detail="Authentication is temporarily unavailable. Please try again shortly.",
            headers={"Retry-After": "30"},
        ) from exc
    except Exception as exc:  # Supabase reachable and rejecting the token
        logger.info(f"Token verification failed: {exc}")
        raise HTTPException(status_code=401, detail="Invalid or expired credentials") from exc

    user = getattr(response, "user", None)
    if user is None or not getattr(user, "id", None):
        raise HTTPException(status_code=401, detail="Invalid or expired credentials")

    # The anon key is itself a JWT signed by the same project, so a token that
    # verifies is not automatically a *user*. Require a real user UUID.
    try:
        uuid.UUID(str(user.id))
    except (ValueError, AttributeError, TypeError):
        logger.warning("Token verified but carried a non-uuid subject; rejecting.")
        raise HTTPException(status_code=401, detail="Invalid or expired credentials")

    authenticated = AuthenticatedUser(
        id=str(user.id),
        email=getattr(user, "email", None),
        access_token=token,
    )
    _cache_put(token, authenticated)
    return authenticated


async def get_current_user(request: Request) -> Optional[AuthenticatedUser]:
    """FastAPI dependency. ``None`` means the caller is a guest.

    An *absent* Authorization header is a guest and is allowed through. A header
    that is present but invalid raises 401 rather than quietly downgrading to
    guest: silently treating a signed-in user as a guest is exactly the failure
    this module exists to fix, and it would stop persisting their messages
    without anything surfacing.
    """
    token = extract_bearer_token(request)
    if token is None:
        return None
    return await verify_token(token)


async def require_user(request: Request) -> AuthenticatedUser:
    """FastAPI dependency for endpoints that must have a signed-in caller."""
    user = await get_current_user(request)
    if user is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    return user
