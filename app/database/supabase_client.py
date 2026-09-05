"""Supabase access, scoped to the calling user.

Two changes from the original here, both load-bearing:

1. The client is now the *async* one. Every method was already ``async def`` but
   called the synchronous client underneath, so each database round trip blocked
   the event loop — on a single free-tier worker that stalls every concurrent SSE
   stream.

2. Queries run as the caller, by forwarding their JWT, so the row level security
   policies in ``supabase/migrations`` apply. The backend physically cannot read
   another user's rows even if the application logic above it is wrong.

Note the deliberate avoidance of ``client.postgrest.auth(token)``: that mutates
headers on the shared client instance, so two concurrent requests can interleave
and run one user's query under another user's token.
"""

import time
import uuid
from typing import Dict, List, Optional, Tuple

from supabase import AsyncClient, AsyncClientOptions, acreate_client

from app.core.config import settings
from app.core.logger import logger

# Building a client per request is wasteful, so keep them briefly, keyed by
# token digest. TTL is short because a client outlives the token's validity
# otherwise.
_CLIENT_TTL_SECONDS = 300
_CLIENT_CACHE_MAX = 128
_client_cache: Dict[str, Tuple[float, AsyncClient]] = {}


def reset_client_cache() -> None:
    """Drop cached clients. Used by tests."""
    _client_cache.clear()


def _is_uuid(value: str) -> bool:
    try:
        uuid.UUID(str(value))
        return True
    except (ValueError, AttributeError, TypeError):
        return False


async def get_user_client(access_token: str) -> AsyncClient:
    """Return a Supabase client that acts as the holder of ``access_token``."""
    import hashlib

    key = hashlib.sha256(access_token.encode("utf-8")).hexdigest()
    now = time.monotonic()

    entry = _client_cache.get(key)
    if entry and entry[0] > now:
        return entry[1]

    client = await acreate_client(
        settings.SUPABASE_URL,
        settings.SUPABASE_ANON_KEY,
        options=AsyncClientOptions(headers={"Authorization": f"Bearer {access_token}"}),
    )

    if len(_client_cache) >= _CLIENT_CACHE_MAX:
        oldest = min(_client_cache, key=lambda k: _client_cache[k][0])
        _client_cache.pop(oldest, None)
    _client_cache[key] = (now + _CLIENT_TTL_SECONDS, client)
    return client


class SupabaseManager:
    """Database operations for one authenticated user."""

    def __init__(self, client: AsyncClient, user_id: str):
        self._client = client
        self._user_id = user_id

    @classmethod
    async def for_user(cls, user) -> "SupabaseManager":
        """Build a manager acting as ``user`` (an ``AuthenticatedUser``)."""
        client = await get_user_client(user.access_token)
        return cls(client, user.id)

    async def create_chat_session(
        self, session_id: str, session_name: str = "New Chat"
    ) -> bool:
        """Create a chat session owned by this user."""
        if not _is_uuid(session_id):
            logger.error("create_chat_session called with a non-uuid session id.")
            return False
        try:
            await (
                self._client.table("chat_sessions")
                .insert(
                    {
                        "id": str(session_id),
                        "session_name": session_name,
                        "user_id": self._user_id,
                    }
                )
                .execute()
            )
            logger.info(f"Created chat session {session_id}.")
            return True
        except Exception as exc:
            logger.error(f"Error creating chat session {session_id}: {exc}", exc_info=True)
            return False

    async def get_session_by_id(self, session_id: str) -> Optional[Dict]:
        """Fetch one of *this user's* sessions. Returns None if it isn't theirs."""
        if not _is_uuid(session_id):
            return None
        try:
            response = await (
                self._client.table("chat_sessions")
                .select("*")
                .eq("id", str(session_id))
                .eq("user_id", self._user_id)
                .limit(1)
                .execute()
            )
            return response.data[0] if response.data else None
        except Exception as exc:
            logger.error(f"Error retrieving session {session_id}: {exc}", exc_info=True)
            return None

    async def owns_session(self, session_id: str) -> bool:
        """Whether this user owns the session."""
        return await self.get_session_by_id(session_id) is not None

    async def ensure_session_owned(
        self, session_id: str, session_name: str = "New Chat"
    ) -> bool:
        """Confirm the caller owns the session, creating it if it doesn't exist.

        The frontend mints session UUIDs client-side, so a first message can
        legitimately arrive before any row exists. Creating it here also closes
        the gap where a caller could write messages into a session id belonging
        to somebody else.
        """
        if not _is_uuid(session_id):
            return False
        existing = await self.get_session_by_id(session_id)
        if existing is not None:
            return True

        # Not ours. It may not exist at all, or it may belong to another user —
        # RLS hides which, and the insert below fails safely in the latter case.
        return await self.create_chat_session(session_id, session_name=session_name)

    async def get_chat_sessions(self) -> Optional[List[Dict]]:
        """All of this user's sessions, newest first."""
        try:
            response = await (
                self._client.table("chat_sessions")
                .select("*")
                .eq("user_id", self._user_id)
                .order("created_at", desc=True)
                .execute()
            )
            return response.data
        except Exception as exc:
            logger.error(f"Error retrieving chat sessions: {exc}", exc_info=True)
            return None

    async def get_messages_by_session_id(self, session_id: str) -> Optional[List[Dict]]:
        """Messages for a session the caller owns, oldest first."""
        if not _is_uuid(session_id):
            return None
        try:
            response = await (
                self._client.table("messages")
                .select("*")
                .eq("session_id", str(session_id))
                .order("created_at", desc=False)
                .execute()
            )
            return response.data
        except Exception as exc:
            logger.error(f"Error retrieving messages for {session_id}: {exc}", exc_info=True)
            return None

    async def update_chat_session_name(self, session_id: str, session_name: str) -> bool:
        """Rename one of this user's sessions."""
        if not _is_uuid(session_id):
            return False
        try:
            await (
                self._client.table("chat_sessions")
                .update({"session_name": session_name})
                .eq("id", str(session_id))
                .eq("user_id", self._user_id)
                .execute()
            )
            return True
        except Exception as exc:
            logger.error(f"Error renaming session {session_id}: {exc}", exc_info=True)
            return False

    async def store_message(
        self,
        session_id: str,
        sender_type: str,
        content: str,
        intent: Optional[str] = None,
        visualization_data: Optional[Dict] = None,
        parent_message_id: Optional[str] = None,
        metadata: Optional[Dict] = None,
    ) -> bool:
        """Store a message. RLS rejects it if the session isn't the caller's."""
        if not _is_uuid(session_id):
            return False
        try:
            await (
                self._client.table("messages")
                .insert(
                    {
                        "session_id": str(session_id),
                        "sender_type": sender_type,
                        "content": content,
                        "intent": intent,
                        "visualization_data": visualization_data or None,
                        "parent_message_id": (
                            str(parent_message_id)
                            if parent_message_id and _is_uuid(parent_message_id)
                            else None
                        ),
                        "metadata": metadata or {},
                    }
                )
                .execute()
            )
            return True
        except Exception as exc:
            logger.error(f"Error storing message for {session_id}: {exc}", exc_info=True)
            return False
