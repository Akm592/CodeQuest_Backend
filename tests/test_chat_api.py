import pytest
from unittest.mock import MagicMock, patch, AsyncMock
from fastapi.testclient import TestClient
from fastapi import HTTPException

# Patch settings before importing app to avoid validation errors
with patch("app.core.config.Settings") as MockSettings:
    MockSettings.return_value.GEMINI_API_KEY = "dummy"
    MockSettings.return_value.SUPABASE_URL = "http://dummy"
    MockSettings.return_value.SUPABASE_ANON_KEY = "dummy"
    MockSettings.return_value.RATE_LIMIT_RULES = "{}"
    MockSettings.return_value.CORS_ORIGINS = []
    
    from app.main import app
    from app.api.chat import check_rate_limit, in_memory_rate_limit

client = TestClient(app)

@pytest.fixture(autouse=True)
def mock_supabase():
    with patch("app.api.chat.SupabaseManager") as mock:
        yield mock

def test_create_guest_session(mock_supabase):
    mock_supabase.create_chat_session = AsyncMock(return_value=True)
    response = client.post("/sessions")
    assert response.status_code == 200
    assert "session_id" in response.json()
    # No auth header -> guest
    # Verify create_chat_session called with "guest"
    mock_supabase.create_chat_session.assert_called()
    args = mock_supabase.create_chat_session.call_args[0]
    assert args[0] == "guest"

def test_create_user_session(mock_supabase):
    mock_supabase.create_chat_session = AsyncMock(return_value=True)
    response = client.post("/sessions", headers={"Authorization": "Bearer token"})
    assert response.status_code == 200
    # Verify called with placeholder (or real ID if logic extended)
    args = mock_supabase.create_chat_session.call_args[0]
    assert args[0] == "user_placeholder"

def test_get_sessions_guest(mock_supabase):
    # No auth header should return empty list
    response = client.get("/sessions")
    assert response.status_code == 200
    assert response.json() == []
    mock_supabase.get_chat_sessions_for_user.assert_not_called()

def test_get_sessions_user(mock_supabase):
    mock_sessions = [{"id": "123", "session_name": "Test"}]
    mock_supabase.get_chat_sessions_for_user = AsyncMock(return_value=mock_sessions)
    response = client.get("/sessions", headers={"Authorization": "Bearer token"})
    assert response.status_code == 200
    assert response.json() == mock_sessions

def test_rate_limit_function():
    in_memory_rate_limit.clear()
    ip = "127.0.0.1"
    
    # 10 allowed
    for _ in range(10):
        check_rate_limit(ip)
    
    # 11th blocked
    with pytest.raises(HTTPException) as exc:
        check_rate_limit(ip)
    assert exc.value.status_code == 429

def test_migrate_sessions(mock_supabase):
    mock_supabase.migrate_chat_sessions = AsyncMock(return_value=True)
    payload = {"guest_session_ids": ["sess1", "sess2"]}
    response = client.post("/sessions/migrate", json=payload, headers={"Authorization": "Bearer token"})
    assert response.status_code == 200
    assert response.json()["success"] is True
    assert response.json()["migrated"] == 2

def test_migrate_sessions_no_auth(mock_supabase):
    payload = {"guest_session_ids": ["sess1"]}
    response = client.post("/sessions/migrate", json=payload)
    assert response.status_code == 401
