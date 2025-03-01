# Supabase client initialization and functions
from typing import Optional, List
from supabase import create_client, Client
from app.core.config import settings
from app.core.logger import logger


class SupabaseManager:
    _client: Optional[Client] = None

    @classmethod
    def get_client(cls) -> Client:
        """Initialize and return the Supabase client."""
        if cls._client is None:
            try:
                cls._client = create_client(
                    settings.SUPABASE_URL, settings.SUPABASE_ANON_KEY
                )
                logger.info("Successfully connected to Supabase.")
            except Exception as e:
                logger.error(f"Failed to initialize Supabase client: {e}")
                raise
        return cls._client

    @classmethod
    async def create_chat_session(
        cls, user_id: str, session_id: str, session_name: str = "New Chat"
    ) -> bool:
        """Create a new chat session in the database."""
        try:
            cls.get_client().table("chat_sessions").insert(
                {
                    "session_id": session_id,
                    "user_id": user_id,
                    "session_name": session_name,
                }
            ).execute()
            return True
        except Exception as e:
            logger.error(f"Error creating chat session: {str(e)}")
            return False

    @classmethod
    async def get_chat_sessions_for_user(cls, user_id: str) -> Optional[List[dict]]:
        """Retrieve all chat sessions for a given user."""
        try:
            response = (
                cls.get_client()
                .table("chat_sessions")
                .select("*")
                .eq("user_id", user_id)
                .execute()
            )
            return response.data
        except Exception as e:
            logger.error(f"Error retrieving chat sessions: {str(e)}")
            return None

    @classmethod
    async def get_messages_by_session_id(cls, session_id: str) -> Optional[List[dict]]:
        """Retrieve all messages for a given session."""
        try:
            response = (
                cls.get_client()
                .table("messages")
                .select("*")
                .eq("session_id", session_id)
                .execute()
            )
            return response.data
        except Exception as e:
            logger.error(f"Error retrieving messages: {str(e)}")
            return None

    @classmethod
    async def update_chat_session_name(cls, session_id: str, session_name: str) -> bool:
        """Update the name of a chat session."""
        try:
            cls.get_client().table("chat_sessions").update(
                {"session_name": session_name}
            ).eq("session_id", session_id).execute()
            return True
        except Exception as e:
            logger.error(f"Error updating session name: {str(e)}")
            return False

    @classmethod
    async def store_message(
        cls,
        session_id: str,
        sender_type: str,
        content: str,
        intent: Optional[str] = None,
        visualization_data: Optional[dict] = None,
        parent_message_id: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> bool:
        """Store a message in the database."""
        try:
            message_data = {
                "session_id": session_id,
                "sender_type": sender_type,
                "content": content,
                "intent": intent,
                "visualization_data": visualization_data,
                "parent_message_id": parent_message_id,
                "metadata": metadata or {},
            }

            response = cls.get_client().table("messages").insert(message_data).execute()

            if response.error:
                logger.error(f"Error storing message: {response.error}")
                return False
            return True
        except Exception as e:
            logger.error(f"Exception storing message: {str(e)}")
            return False
