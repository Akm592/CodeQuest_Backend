"""Endpoint tests, focused on the access-control behaviour that was broken."""

from unittest.mock import AsyncMock, patch

import pytest

from tests.conftest import ALICE_SESSION, BOB_SESSION


def _db_stub(**overrides):
    """Build a SupabaseManager stand-in. Everything is denied unless overridden."""
    db = AsyncMock()
    db.owns_session = AsyncMock(return_value=False)
    db.ensure_session_owned = AsyncMock(return_value=False)
    db.get_messages_by_session_id = AsyncMock(return_value=[])
    db.get_chat_sessions = AsyncMock(return_value=[])
    db.create_chat_session = AsyncMock(return_value=True)
    db.store_message = AsyncMock(return_value=True)
    for key, value in overrides.items():
        setattr(db, key, AsyncMock(return_value=value))
    return db


# --- Guests ----------------------------------------------------------------


def test_guest_gets_a_session_id_without_a_database_row(client):
    response = client.post("/sessions")
    assert response.status_code == 200
    assert "session_id" in response.json()


def test_guest_listing_sessions_is_empty(client):
    response = client.get("/sessions")
    assert response.status_code == 200
    assert response.json() == []


def test_guest_reading_messages_is_empty(client):
    response = client.get(f"/sessions/{ALICE_SESSION}/messages")
    assert response.status_code == 200
    assert response.json() == []


# --- Invalid credentials ---------------------------------------------------


ENDPOINTS = [
    ("get", "/sessions"),
    ("post", "/sessions"),
    ("get", f"/sessions/{ALICE_SESSION}/messages"),
]


def _auth_raising(exc):
    """Patch the Supabase auth client so get_user raises ``exc``."""
    from app.core import auth as auth_module

    auth_module.reset_auth_cache()
    fake_client = AsyncMock()
    fake_client.auth.get_user = AsyncMock(side_effect=exc)
    return patch.object(auth_module, "_get_auth_client", AsyncMock(return_value=fake_client))


@pytest.mark.parametrize("method,path", ENDPOINTS)
def test_a_junk_token_is_rejected_rather_than_trusted(client, method, path):
    """Regression: any non-empty header used to mean 'authenticated'."""
    from supabase_auth.errors import AuthApiError

    with _auth_raising(AuthApiError("invalid claim", 401, "bad_jwt")):
        response = getattr(client, method)(path, headers={"Authorization": "Bearer nonsense"})
    assert response.status_code == 401


@pytest.mark.parametrize("method,path", ENDPOINTS)
def test_an_auth_outage_is_503_not_401(client, method, path):
    """A paused Supabase project must not read as "your credentials are bad".

    Returning 401 here is what sent the browser into a refresh storm against an
    endpoint that was simply returning 502.
    """
    from supabase_auth.errors import AuthRetryableError

    with _auth_raising(AuthRetryableError("Bad Gateway", 502)):
        response = getattr(client, method)(path, headers={"Authorization": "Bearer real-token"})
    assert response.status_code == 503
    assert response.headers.get("Retry-After")


def test_guests_are_unaffected_by_an_auth_outage(client):
    """Guests present no token, so they never reach verification at all."""
    from supabase_auth.errors import AuthRetryableError

    with _auth_raising(AuthRetryableError("Bad Gateway", 502)):
        assert client.get("/sessions").status_code == 200
        assert client.post("/sessions").status_code == 200


# --- Ownership -------------------------------------------------------------


def test_reading_another_users_session_returns_404(client, as_user, alice):
    """The IDOR fix: alice must not be able to read bob's messages by UUID."""
    as_user(alice)
    db = _db_stub()  # owns_session False => not hers
    db.get_messages_by_session_id = AsyncMock(return_value=[{"content": "bob's secret"}])

    with patch("app.api.chat.SupabaseManager.for_user", AsyncMock(return_value=db)):
        response = client.get(f"/sessions/{BOB_SESSION}/messages")

    assert response.status_code == 404
    assert "bob's secret" not in response.text


def test_reading_your_own_session_returns_its_messages(client, as_user, alice):
    as_user(alice)
    db = _db_stub(owns_session=True)
    db.get_messages_by_session_id = AsyncMock(return_value=[{"content": "hello"}])

    with patch("app.api.chat.SupabaseManager.for_user", AsyncMock(return_value=db)):
        response = client.get(f"/sessions/{ALICE_SESSION}/messages")

    assert response.status_code == 200
    assert response.json() == [{"content": "hello"}]


def test_messages_endpoint_rejects_a_malformed_session_id(client, as_user, alice):
    as_user(alice)
    with patch("app.api.chat.SupabaseManager.for_user", AsyncMock(return_value=_db_stub())):
        response = client.get("/sessions/not-a-uuid/messages")
    assert response.status_code == 400


def test_posting_to_another_users_session_returns_404(client, as_user, alice):
    """Writes were unguarded too: any session UUID could be written into."""
    as_user(alice)
    db = _db_stub()  # ensure_session_owned False

    with patch("app.api.chat.SupabaseManager.for_user", AsyncMock(return_value=db)):
        response = client.post(
            "/chat",
            json={"user_input": "hi"},
            headers={"X-Session-ID": BOB_SESSION},
        )

    assert response.status_code == 404
    db.store_message.assert_not_awaited()


def test_listing_sessions_uses_the_authenticated_user(client, as_user, alice):
    as_user(alice)
    db = _db_stub()
    db.get_chat_sessions = AsyncMock(return_value=[{"id": ALICE_SESSION}])

    with patch("app.api.chat.SupabaseManager.for_user", AsyncMock(return_value=db)) as for_user:
        response = client.get("/sessions")

    assert response.status_code == 200
    assert response.json() == [{"id": ALICE_SESSION}]
    assert for_user.await_args.args[0].id == alice.id


# --- Request validation ----------------------------------------------------


def test_chat_requires_a_session_header(client):
    assert client.post("/chat", json={"user_input": "hi"}).status_code == 400


def test_chat_rejects_a_malformed_session_header(client):
    response = client.post(
        "/chat", json={"user_input": "hi"}, headers={"X-Session-ID": "nope"}
    )
    assert response.status_code == 400


def test_chat_rejects_empty_input(client):
    response = client.post(
        "/chat", json={"user_input": "   "}, headers={"X-Session-ID": ALICE_SESSION}
    )
    assert response.status_code == 400


def test_scrape_endpoint_requires_an_identifier(client):
    assert client.post("/scrape_leetcode", json={}).status_code == 400
