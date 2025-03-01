# Supabase client initialization and functions
from typing import Optional
from supabase import create_client, Client
from app.core.config import settings
from app.core.logger import logger


class SupabaseManager:
    _client: Optional[Client] = None

    @classmethod
    def get_client(cls) -> Client:
        if cls._client is None:
            try:
                cls._client = create_client(
                    settings.SUPABASE_URL, settings.SUPABASE_ANON_KEY
                )
                logger.info("Successfully connected to Supabase.")
            except Exception as e:
                logger.error(f"Failed to initialize Supabase client: {e}")
                raise  # Re-raise to prevent app from starting without DB
        return cls._client

    @staticmethod
    async def store_message(
        session_id: str,
        sender_type: str,  # Changed from user_message/bot_message to sender_type
        content: str,  # Changed from user_message/bot_message to content
        intent: Optional[str] = None,
        visualization_data: Optional[dict] = None,
        parent_message_id: Optional[str] = None,  # Added parent_message_id
        metadata: Optional[dict] = None,  # Added metadata
    ):
        supabase_client = SupabaseManager.get_client()
        try:
            response = (
                await supabase_client.table(
                    "messages"
                )  # Changed table name to 'messages'
                .insert(
                    {
                        "session_id": session_id,
                        "sender_type": sender_type,  # Using sender_type directly
                        "content": content,  # Using content directly
                        "intent": intent,
                        "visualization_data": visualization_data,
                        "parent_message_id": parent_message_id,  # Include parent_message_id
                        "metadata": metadata,  # Include metadata
                    }
                )
                .execute()
            )

            if response.error:
                logger.error(
                    f"Error storing message in Supabase (messages table): {response.error}"  # Updated error message
                )
                return False
            return True
        except Exception as e:
            logger.error(
                f"Exception while storing message in messages table: {e}"
            )  # Updated error message
            return False


# Example Usage (you can access the client using SupabaseManager.get_client())
# supabase = SupabaseManager.get_client()
