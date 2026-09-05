"""Shared test fixtures.

``conftest.py`` is imported before any test module, which makes it the reliable
place to populate the environment. The previous approach — a ``patch`` inside the
test module — ran too late, because importing ``app.core.config`` had already
executed its module-level validation and raised.
"""

import os
import sys
from pathlib import Path

# Make `import app...` work when pytest is invoked as plain `pytest`: without
# this the repo root is never on sys.path, since tests/ has no __init__.py.
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

os.environ.setdefault("GEMINI_API_KEY", "test-gemini-key")
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_ANON_KEY", "test-anon-key")
os.environ.setdefault("RATE_LIMIT_RULES", "{}")
os.environ.setdefault("GUEST_RATE_LIMIT", "10")
os.environ.setdefault("TRUSTED_PROXY_HOPS", "1")
os.environ.setdefault("LOG_LEVEL", "WARNING")
os.environ.setdefault("APP_LOG_FILE", "")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.core import auth as auth_module  # noqa: E402
from app.core.auth import AuthenticatedUser  # noqa: E402
from app.core.rate_limit import reset_rate_limit_state  # noqa: E402
from app.main import app  # noqa: E402

ALICE_ID = "11111111-1111-1111-1111-111111111111"
BOB_ID = "22222222-2222-2222-2222-222222222222"
ALICE_SESSION = "aaaaaaaa-0000-0000-0000-000000000001"
BOB_SESSION = "bbbbbbbb-0000-0000-0000-000000000002"


@pytest.fixture
def client():
    """Build a TestClient with clean per-test module state."""
    reset_rate_limit_state()
    auth_module.reset_auth_cache()
    from app.api import chat as chat_module

    chat_module.chat_memory.sessions.clear()
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def alice():
    """Return a signed-in user."""
    return AuthenticatedUser(id=ALICE_ID, email="alice@example.com", access_token="alice-token")


@pytest.fixture
def bob():
    """Return a second signed-in user, for isolation tests."""
    return AuthenticatedUser(id=BOB_ID, email="bob@example.com", access_token="bob-token")


@pytest.fixture
def as_user(monkeypatch):
    """Make ``get_current_user`` return a chosen user without contacting Supabase."""

    def _apply(user):
        # No parameters: FastAPI inspects the override's signature, so an
        # unannotated `request` argument would be read as a query parameter.
        async def fake_get_current_user():
            return user

        app.dependency_overrides[auth_module.get_current_user] = fake_get_current_user
        return user

    yield _apply
    app.dependency_overrides.clear()
