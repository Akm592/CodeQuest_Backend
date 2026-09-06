"""SupabaseManager.

Every query is scoped to the owning user. The database enforces this too, via
the RLS policies in supabase/migrations, but the explicit filters keep the
intent visible in the code and use the indexes.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.database.supabase_client import SupabaseManager

USER_ID = "11111111-1111-1111-1111-111111111111"
SESSION_ID = "aaaaaaaa-0000-0000-0000-000000000001"
OTHER_SESSION = "bbbbbbbb-0000-0000-0000-000000000002"


class FakeQuery:
    """Records the filters applied, then returns a canned result."""

    def __init__(self, result=None, raises=None):
        self._result = result if result is not None else []
        self._raises = raises
        self.filters = {}
        self.payload = None
        self.table_name = None

    # Chainable builder methods
    def select(self, *_args, **_kwargs):
        return self

    def insert(self, payload):
        self.payload = payload
        return self

    def update(self, payload):
        self.payload = payload
        return self

    def eq(self, column, value):
        self.filters[column] = value
        return self

    def order(self, *_args, **_kwargs):
        return self

    def limit(self, *_args, **_kwargs):
        return self

    async def execute(self):
        if self._raises:
            raise self._raises
        return MagicMock(data=self._result)


def make_manager(result=None, raises=None):
    query = FakeQuery(result=result, raises=raises)
    client = MagicMock()

    def table(name):
        query.table_name = name
        return query

    client.table = table
    return SupabaseManager(client, USER_ID), query


# --- Reads -----------------------------------------------------------------


async def test_get_session_by_id_filters_on_both_id_and_owner():
    manager, query = make_manager(result=[{"id": SESSION_ID}])
    session = await manager.get_session_by_id(SESSION_ID)

    assert session == {"id": SESSION_ID}
    assert query.table_name == "chat_sessions"
    assert query.filters == {"id": SESSION_ID, "user_id": USER_ID}


async def test_get_session_by_id_returns_none_when_not_yours():
    manager, _ = make_manager(result=[])
    assert await manager.get_session_by_id(OTHER_SESSION) is None


@pytest.mark.parametrize("bad", ["not-a-uuid", "", "12345"])
async def test_get_session_by_id_rejects_malformed_ids(bad):
    manager, _ = make_manager(result=[{"id": SESSION_ID}])
    assert await manager.get_session_by_id(bad) is None


async def test_get_session_by_id_survives_a_database_error():
    manager, _ = make_manager(raises=Exception("connection reset"))
    assert await manager.get_session_by_id(SESSION_ID) is None


async def test_owns_session_reflects_the_lookup():
    manager, _ = make_manager(result=[{"id": SESSION_ID}])
    assert await manager.owns_session(SESSION_ID) is True

    manager, _ = make_manager(result=[])
    assert await manager.owns_session(SESSION_ID) is False


async def test_get_chat_sessions_filters_by_owner():
    manager, query = make_manager(result=[{"id": SESSION_ID}])
    sessions = await manager.get_chat_sessions()

    assert sessions == [{"id": SESSION_ID}]
    assert query.filters == {"user_id": USER_ID}


async def test_get_chat_sessions_returns_none_on_error():
    manager, _ = make_manager(raises=Exception("boom"))
    assert await manager.get_chat_sessions() is None


async def test_get_messages_filters_by_session():
    manager, query = make_manager(result=[{"content": "hi"}])
    messages = await manager.get_messages_by_session_id(SESSION_ID)

    assert messages == [{"content": "hi"}]
    assert query.table_name == "messages"
    assert query.filters == {"session_id": SESSION_ID}


async def test_get_messages_rejects_a_malformed_session_id():
    manager, _ = make_manager(result=[{"content": "hi"}])
    assert await manager.get_messages_by_session_id("nope") is None


# --- Writes ----------------------------------------------------------------


async def test_create_chat_session_writes_the_owner_and_the_id_column():
    manager, query = make_manager()
    assert await manager.create_chat_session(SESSION_ID, session_name="Chat") is True

    # The column is `id`, not `session_id` — the old tests asserted otherwise.
    assert query.payload == {
        "id": SESSION_ID,
        "session_name": "Chat",
        "user_id": USER_ID,
    }


async def test_create_chat_session_rejects_a_malformed_id():
    manager, _ = make_manager()
    assert await manager.create_chat_session("not-a-uuid") is False


async def test_create_chat_session_reports_failure():
    manager, _ = make_manager(raises=Exception("insert failed"))
    assert await manager.create_chat_session(SESSION_ID) is False


async def test_ensure_session_owned_accepts_an_existing_session():
    manager, _ = make_manager(result=[{"id": SESSION_ID}])
    assert await manager.ensure_session_owned(SESSION_ID) is True


async def test_ensure_session_owned_creates_a_missing_session():
    manager, query = make_manager(result=[])
    assert await manager.ensure_session_owned(SESSION_ID, session_name="First words") is True
    assert query.payload["user_id"] == USER_ID
    assert query.payload["session_name"] == "First words"


async def test_ensure_session_owned_rejects_a_malformed_id():
    manager, _ = make_manager()
    assert await manager.ensure_session_owned("bad") is False


async def test_update_session_name_is_scoped_to_the_owner():
    manager, query = make_manager()
    assert await manager.update_chat_session_name(SESSION_ID, "Renamed") is True
    assert query.payload == {"session_name": "Renamed"}
    assert query.filters == {"id": SESSION_ID, "user_id": USER_ID}


async def test_store_message_builds_the_expected_row():
    manager, query = make_manager()
    ok = await manager.store_message(
        session_id=SESSION_ID,
        sender_type="bot",
        content="hello",
        intent="general",
        metadata={"response_type": "LLM_general"},
    )

    assert ok is True
    assert query.table_name == "messages"
    assert query.payload["session_id"] == SESSION_ID
    assert query.payload["sender_type"] == "bot"
    assert query.payload["content"] == "hello"
    assert query.payload["intent"] == "general"
    assert query.payload["metadata"] == {"response_type": "LLM_general"}
    assert query.payload["parent_message_id"] is None


async def test_store_message_defaults_metadata_to_an_object():
    manager, query = make_manager()
    await manager.store_message(session_id=SESSION_ID, sender_type="user", content="hi")
    assert query.payload["metadata"] == {}


async def test_store_message_drops_a_malformed_parent_id():
    manager, query = make_manager()
    await manager.store_message(
        session_id=SESSION_ID, sender_type="user", content="hi", parent_message_id="nope"
    )
    assert query.payload["parent_message_id"] is None


async def test_store_message_rejects_a_malformed_session_id():
    manager, _ = make_manager()
    assert await manager.store_message(session_id="bad", sender_type="user", content="x") is False


async def test_store_message_reports_failure():
    manager, _ = make_manager(raises=Exception("rls denied"))
    result = await manager.store_message(
        session_id=SESSION_ID, sender_type="user", content="x"
    )
    assert result is False


async def test_for_user_builds_a_manager_scoped_to_that_user(monkeypatch):
    from app.core.auth import AuthenticatedUser
    from app.database import supabase_client

    fake_client = MagicMock()
    monkeypatch.setattr(
        supabase_client, "get_user_client", AsyncMock(return_value=fake_client)
    )

    user = AuthenticatedUser(id=USER_ID, email="a@b.c", access_token="tok")
    manager = await SupabaseManager.for_user(user)
    assert manager._user_id == USER_ID
    supabase_client.get_user_client.assert_awaited_once_with("tok")
