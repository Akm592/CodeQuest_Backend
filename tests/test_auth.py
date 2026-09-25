"""Tests for token verification.

The behaviour under test is the one that was broken: the backend used to treat
*any* non-empty Authorization header as proof of a signed-in user.
"""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from app.core import auth as auth_module
from app.core.auth import AuthenticatedUser, extract_bearer_token, verify_token


class _Request:
    def __init__(self, headers):
        self.headers = headers


def test_extract_bearer_token_reads_a_normal_header():
    assert extract_bearer_token(_Request({"Authorization": "Bearer abc123"})) == "abc123"


def test_extract_bearer_token_is_none_when_absent():
    assert extract_bearer_token(_Request({})) is None


@pytest.mark.parametrize("value", ["Bearer null", "Bearer undefined", "Bearer ", "Bearer"])
def test_extract_bearer_token_ignores_empty_stand_ins(value):
    """A frontend bug must degrade to guest, not lock the user out with a 401."""
    assert extract_bearer_token(_Request({"Authorization": value})) is None


def test_extract_bearer_token_ignores_other_schemes():
    assert extract_bearer_token(_Request({"Authorization": "Basic abc123"})) is None


async def test_verify_token_rejects_a_token_supabase_does_not_know():
    auth_module.reset_auth_cache()
    fake_client = AsyncMock()
    fake_client.auth.get_user = AsyncMock(side_effect=Exception("invalid JWT"))

    with patch.object(auth_module, "_get_auth_client", AsyncMock(return_value=fake_client)):
        with pytest.raises(HTTPException) as exc:
            await verify_token("garbage")
    assert exc.value.status_code == 401


async def test_verify_token_rejects_a_response_without_a_user():
    """The anon key is itself a JWT, so a token that verifies is not always a user."""
    auth_module.reset_auth_cache()
    fake_client = AsyncMock()
    fake_client.auth.get_user = AsyncMock(return_value=type("R", (), {"user": None})())

    with patch.object(auth_module, "_get_auth_client", AsyncMock(return_value=fake_client)):
        with pytest.raises(HTTPException) as exc:
            await verify_token("anon-key-shaped-token")
    assert exc.value.status_code == 401


async def test_verify_token_rejects_a_non_uuid_subject():
    auth_module.reset_auth_cache()
    user = type("U", (), {"id": "service_role", "email": None})()
    fake_client = AsyncMock()
    fake_client.auth.get_user = AsyncMock(return_value=type("R", (), {"user": user})())

    with patch.object(auth_module, "_get_auth_client", AsyncMock(return_value=fake_client)):
        with pytest.raises(HTTPException) as exc:
            await verify_token("weird")
    assert exc.value.status_code == 401


async def test_verify_token_accepts_a_real_user_and_caches_it():
    auth_module.reset_auth_cache()
    user = type("U", (), {"id": "11111111-1111-1111-1111-111111111111", "email": "a@b.c"})()
    get_user = AsyncMock(return_value=type("R", (), {"user": user})())
    fake_client = AsyncMock()
    fake_client.auth.get_user = get_user

    with patch.object(auth_module, "_get_auth_client", AsyncMock(return_value=fake_client)):
        first = await verify_token("good-token")
        second = await verify_token("good-token")

    assert isinstance(first, AuthenticatedUser)
    assert first.id == "11111111-1111-1111-1111-111111111111"
    assert first.access_token == "good-token"
    assert second == first
    # Second call served from cache rather than a second round trip.
    assert get_user.await_count == 1


def test_cache_never_stores_the_raw_token():
    auth_module.reset_auth_cache()
    user = AuthenticatedUser(id="11111111-1111-1111-1111-111111111111", email=None, access_token="s3cret")
    auth_module._cache_put("s3cret", user)
    assert "s3cret" not in auth_module._verify_cache


# --- Availability vs. rejection -------------------------------------------
#
# Conflating these is what turns a brief Supabase outage into a refresh storm:
# the client reads 401 as "your credentials are bad" and starts retrying against
# a service that is simply down.


async def _verify_raising(exc):
    """Run verify_token with get_user raising ``exc``; return the HTTPException."""
    auth_module.reset_auth_cache()
    fake_client = AsyncMock()
    fake_client.auth.get_user = AsyncMock(side_effect=exc)
    with patch.object(auth_module, "_get_auth_client", AsyncMock(return_value=fake_client)):
        with pytest.raises(HTTPException) as caught:
            await verify_token("some-token")
    return caught.value


async def test_a_gateway_error_is_reported_as_unavailable():
    """AuthRetryableError covers Supabase's 502/503/504 — the paused-project case."""
    from supabase_auth.errors import AuthRetryableError

    error = await _verify_raising(AuthRetryableError("Bad Gateway", 502))
    assert error.status_code == 503
    assert error.headers.get("Retry-After")


async def test_a_connection_failure_is_reported_as_unavailable():
    import httpx

    error = await _verify_raising(httpx.ConnectError("name resolution failed"))
    assert error.status_code == 503


async def test_a_timeout_is_reported_as_unavailable():
    import asyncio

    error = await _verify_raising(asyncio.TimeoutError())
    assert error.status_code == 503


async def test_a_rejected_token_is_still_401():
    """A reachable Supabase saying no must not be mistaken for an outage."""
    from supabase_auth.errors import AuthApiError

    error = await _verify_raising(AuthApiError("invalid claim", 401, "bad_jwt"))
    assert error.status_code == 401


async def test_a_hanging_auth_service_does_not_block_forever(monkeypatch):
    """Without the timeout a stalled auth service occupies the worker."""
    monkeypatch.setattr(auth_module, "_VERIFY_TIMEOUT_SECONDS", 0.05)
    auth_module.reset_auth_cache()

    async def never_returns(_token):
        await asyncio.sleep(30)

    fake_client = AsyncMock()
    fake_client.auth.get_user = never_returns
    with patch.object(auth_module, "_get_auth_client", AsyncMock(return_value=fake_client)):
        with pytest.raises(HTTPException) as caught:
            await verify_token("slow-token")
    assert caught.value.status_code == 503
