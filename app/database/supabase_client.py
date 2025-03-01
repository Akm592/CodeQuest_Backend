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
    async def store_chat_history(
        session_id: str,
        user_message: str,
        bot_message: str,
        intent: str,
        visualization_data: Optional[dict] = None,
    ):
        supabase_client = SupabaseManager.get_client()
        try:
            response = (
                await supabase_client.table("chat_history")
                .insert(
                    {
                        "session_id": session_id,
                        "user_message": user_message,
                        "bot_message": bot_message,
                        "intent": intent,
                        "visualization_data": visualization_data,
                    }
                )
                .execute()
            )

            if response.error:
                logger.error(
                    f"Error storing chat history in Supabase: {response.error}"
                )
                return False
            return True
        except Exception as e:
            logger.error(f"Exception while storing chat history: {e}")
            return False


# Example Usage (you can access the client using SupabaseManager.get_client())
# supabase = SupabaseManager.get_client()
