import pytest
from unittest.mock import MagicMock, patch
from app.database.supabase_client import SupabaseManager
from supabase import Client
import uuid

@pytest.fixture(autouse=True)
def mock_supabase_client():
    with patch('app.database.supabase_client.create_client') as mock_create_client:
        mock_client_instance = MagicMock(spec=Client)
        mock_create_client.return_value = mock_client_instance
        SupabaseManager._client = None  # Ensure client is re-initialized for each test
        yield mock_client_instance

def test_get_client_initialization(mock_supabase_client):
    client = SupabaseManager.get_client()
    assert client is mock_supabase_client

def test_get_client_returns_existing_client(mock_supabase_client):
    first_client = SupabaseManager.get_client()
    second_client = SupabaseManager.get_client()
    assert first_client is second_client
    assert first_client is mock_supabase_client

def test_get_client_raises_error_on_failure():
    with patch('app.database.supabase_client.create_client', side_effect=Exception("Supabase connection error")):
        SupabaseManager._client = None # Reset client for this test
        with pytest.raises(Exception, match="Supabase connection error"):
            SupabaseManager.get_client()

@pytest.mark.asyncio
async def test_create_chat_session_success(mock_supabase_client):
    mock_execute = MagicMock()
    mock_execute.return_value.data = [{}]
    mock_supabase_client.table.return_value.insert.return_value.execute.return_value = mock_execute.return_value

    user_id = "test_user"
    session_id = str(uuid.uuid4())
    session_name = "Test Chat"

    result = await SupabaseManager.create_chat_session(user_id, session_id, session_name)

    mock_supabase_client.table.assert_called_once_with("chat_sessions")
    mock_supabase_client.table.return_value.insert.assert_called_once_with({
        "session_id": session_id,
        "user_id": user_id,
        "session_name": session_name,
    })
    mock_supabase_client.table.return_value.insert.return_value.execute.assert_called_once()
    assert result is True

@pytest.mark.asyncio
async def test_create_chat_session_failure(mock_supabase_client):
    mock_supabase_client.table.return_value.insert.return_value.execute.side_effect = Exception("DB error")

    user_id = "test_user"
    session_id = str(uuid.uuid4())
    session_name = "Test Chat"

    result = await SupabaseManager.create_chat_session(user_id, session_id, session_name)

    assert result is False

@pytest.mark.asyncio
async def test_create_chat_session_invalid_uuid(mock_supabase_client):
    user_id = "test_user"
    session_id = "invalid-uuid"
    session_name = "Test Chat"

    result = await SupabaseManager.create_chat_session(user_id, session_id, session_name)

    assert result is False
    mock_supabase_client.table.assert_not_called() # Should not attempt DB call with invalid UUID

@pytest.mark.asyncio
async def test_get_session_by_id_found(mock_supabase_client):
    session_id = str(uuid.uuid4())
    expected_session = {"session_id": session_id, "user_id": "user123", "session_name": "My Chat"}
    
    mock_execute = MagicMock()
    mock_execute.return_value.data = [expected_session]
    mock_supabase_client.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value = mock_execute.return_value

    result = await SupabaseManager.get_session_by_id(session_id)

    mock_supabase_client.table.assert_called_once_with("chat_sessions")
    mock_supabase_client.table.return_value.select.assert_called_once_with("*")
    mock_supabase_client.table.return_value.select.return_value.eq.assert_called_once_with("session_id", session_id)
    mock_supabase_client.table.return_value.select.return_value.eq.return_value.limit.assert_called_once_with(1)
    assert result == expected_session

@pytest.mark.asyncio
async def test_get_session_by_id_not_found(mock_supabase_client):
    session_id = str(uuid.uuid4())
    
    mock_execute = MagicMock()
    mock_execute.return_value.data = []
    mock_supabase_client.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value = mock_execute.return_value

    result = await SupabaseManager.get_session_by_id(session_id)

    assert result is None

@pytest.mark.asyncio
async def test_get_session_by_id_invalid_uuid(mock_supabase_client):
    session_id = "invalid-uuid"
    
    result = await SupabaseManager.get_session_by_id(session_id)

    assert result is None
    mock_supabase_client.table.assert_not_called()

@pytest.mark.asyncio
async def test_get_chat_sessions_for_user_success(mock_supabase_client):
    user_id = "user123"
    expected_sessions = [{"session_id": str(uuid.uuid4()), "session_name": "Chat 1"}, {"session_id": str(uuid.uuid4()), "session_name": "Chat 2"}]
    
    mock_execute = MagicMock()
    mock_execute.return_value.data = expected_sessions
    mock_supabase_client.table.return_value.select.return_value.eq.return_value.order.return_value.execute.return_value = mock_execute.return_value

    result = await SupabaseManager.get_chat_sessions_for_user(user_id)

    mock_supabase_client.table.assert_called_once_with("chat_sessions")
    mock_supabase_client.table.return_value.select.assert_called_once_with("*")
    mock_supabase_client.table.return_value.select.return_value.eq.assert_called_once_with("user_id", user_id)
    mock_supabase_client.table.return_value.select.return_value.eq.return_value.order.assert_called_once_with("created_at", desc=True)
    assert result == expected_sessions

@pytest.mark.asyncio
async def test_get_chat_sessions_for_user_failure(mock_supabase_client):
    user_id = "user123"
    
    mock_execute = MagicMock()
    mock_execute.return_value.data = None # Simulate DB error
    mock_supabase_client.table.return_value.select.return_value.eq.return_value.order.return_value.execute.return_value = mock_execute.return_value

    result = await SupabaseManager.get_chat_sessions_for_user(user_id)

    assert result is None

@pytest.mark.asyncio
async def test_get_messages_by_session_id_success(mock_supabase_client):
    session_id = str(uuid.uuid4())
    expected_messages = [{"id": 1, "content": "Hello"}, {"id": 2, "content": "Hi"}]
    
    mock_execute = MagicMock()
    mock_execute.return_value.data = expected_messages
    mock_supabase_client.table.return_value.select.return_value.eq.return_value.order.return_value.execute.return_value = mock_execute.return_value

    result = await SupabaseManager.get_messages_by_session_id(session_id)

    mock_supabase_client.table.assert_called_once_with("messages")
    mock_supabase_client.table.return_value.select.assert_called_once_with("*")
    mock_supabase_client.table.return_value.select.return_value.eq.assert_called_once_with("session_id", session_id)
    mock_supabase_client.table.return_value.select.return_value.eq.return_value.order.assert_called_once_with("created_at", desc=False)
    assert result == expected_messages

@pytest.mark.asyncio
async def test_get_messages_by_session_id_not_found(mock_supabase_client):
    session_id = str(uuid.uuid4())
    
    mock_execute = MagicMock()
    mock_execute.return_value.data = []
    mock_supabase_client.table.return_value.select.return_value.eq.return_value.order.return_value.execute.return_value = mock_execute.return_value

    result = await SupabaseManager.get_messages_by_session_id(session_id)

    assert result == []

@pytest.mark.asyncio
async def test_get_messages_by_session_id_invalid_uuid(mock_supabase_client):
    session_id = "invalid-uuid"
    
    result = await SupabaseManager.get_messages_by_session_id(session_id)

    assert result is None
    mock_supabase_client.table.assert_not_called()

@pytest.mark.asyncio
async def test_update_chat_session_name_success(mock_supabase_client):
    session_id = str(uuid.uuid4())
    new_name = "Updated Chat Name"
    
    mock_execute = MagicMock()
    mock_execute.return_value.data = [{}]
    mock_supabase_client.table.return_value.update.return_value.eq.return_value.execute.return_value = mock_execute.return_value

    result = await SupabaseManager.update_chat_session_name(session_id, new_name)

    mock_supabase_client.table.assert_called_once_with("chat_sessions")
    mock_supabase_client.table.return_value.update.assert_called_once_with({"session_name": new_name})
    mock_supabase_client.table.return_value.update.return_value.eq.assert_called_once_with("session_id", session_id)
    assert result is True

@pytest.mark.asyncio
async def test_update_chat_session_name_failure(mock_supabase_client):
    session_id = str(uuid.uuid4())
    new_name = "Updated Chat Name"
    
    mock_supabase_client.table.return_value.update.return_value.eq.return_value.execute.side_effect = Exception("Update error")

    result = await SupabaseManager.update_chat_session_name(session_id, new_name)

    assert result is False

@pytest.mark.asyncio
async def test_update_chat_session_name_invalid_uuid(mock_supabase_client):
    session_id = "invalid-uuid"
    new_name = "Updated Chat Name"
    
    result = await SupabaseManager.update_chat_session_name(session_id, new_name)

    assert result is False
    mock_supabase_client.table.assert_not_called()

@pytest.mark.asyncio
async def test_store_message_success(mock_supabase_client):
    session_id = str(uuid.uuid4())
    message_data = {
        "session_id": session_id,
        "sender_type": "user",
        "content": "Hello, bot!",
        "intent": "general",
        "visualization_data": None,
        "parent_message_id": None,
        "metadata": {"from_frontend": True},
    }
    
    mock_execute = MagicMock()
    mock_execute.return_value.data = [{}]
    mock_supabase_client.table.return_value.insert.return_value.execute.return_value = mock_execute.return_value

    result = await SupabaseManager.store_message(**message_data)

    mock_supabase_client.table.assert_called_once_with("messages")
    mock_supabase_client.table.return_value.insert.assert_called_once_with(message_data)
    assert result is True

@pytest.mark.asyncio
async def test_store_message_failure(mock_supabase_client):
    session_id = str(uuid.uuid4())
    message_data = {
        "session_id": session_id,
        "sender_type": "user",
        "content": "Hello, bot!",
        "intent": "general",
        "visualization_data": None,
        "parent_message_id": None,
        "metadata": {"from_frontend": True},
    }
    
    mock_supabase_client.table.return_value.insert.return_value.execute.side_effect = Exception("Store error")

    result = await SupabaseManager.store_message(**message_data)

    assert result is False

@pytest.mark.asyncio
async def test_store_message_invalid_session_uuid(mock_supabase_client):
    message_data = {
        "session_id": "invalid-uuid",
        "sender_type": "user",
        "content": "Hello, bot!",
        "intent": "general",
        "visualization_data": None,
        "parent_message_id": None,
        "metadata": {"from_frontend": True},
    }
    
    result = await SupabaseManager.store_message(**message_data)

    assert result is False
    mock_supabase_client.table.assert_not_called()

@pytest.mark.asyncio
async def test_store_message_invalid_parent_message_uuid(mock_supabase_client):
    session_id = str(uuid.uuid4())
    message_data = {
        "session_id": session_id,
        "sender_type": "user",
        "content": "Hello, bot!",
        "intent": "general",
        "visualization_data": None,
        "parent_message_id": "invalid-parent-uuid",
        "metadata": {"from_frontend": True},
    }
    
    result = await SupabaseManager.store_message(**message_data)

    assert result is False
    mock_supabase_client.table.assert_not_called()
