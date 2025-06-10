# app/database/supabase_client.py
from typing import Optional, List, Dict # Ensure Dict is imported
from supabase import create_client, Client
from app.core.config import settings
from app.core.logger import logger
import uuid # Import uuid if needed for validation within the class


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
            client = cls.get_client()
            # Ensure session_id is string
            session_id_str = str(session_id)
            # Basic validation
            uuid.UUID(session_id_str)

            response = client.table("chat_sessions").insert(
                {
                    "session_id": session_id_str,
                    "user_id": user_id,
                    "session_name": session_name,
                }
            ).execute()
            # Optional: Check response status or errors if execute() provides them
            # if response.error: logger.error(...) return False
            logger.info(f"Successfully inserted chat session {session_id_str}")
            return True
        except ValueError:
            logger.error(f"Invalid session_id format provided to create_chat_session: {session_id}")
            return False
        except Exception as e:
            logger.error(f"Error creating chat session {session_id}: {str(e)}", exc_info=True)
            return False

    # --- ADD THIS METHOD ---
    @classmethod
    async def get_session_by_id(cls, session_id: str) -> Optional[Dict]:
        """Retrieve a specific chat session by its ID."""
        try:
            # Validate UUID format before querying
            session_uuid = uuid.UUID(session_id)
            client = cls.get_client()
            response = (
                client.table("chat_sessions")
                .select("*")
                .eq("session_id", str(session_uuid)) # Query using the validated string UUID
                .limit(1) # Optimization: We only expect one session
                .execute()
            )
            # Check if data was returned
            if response.data:
                logger.debug(f"Found session data for ID {session_id}")
                return response.data[0] # Return the first (and only) dictionary
            else:
                logger.debug(f"No session found in DB for ID {session_id}")
                return None
        except ValueError:
            logger.error(f"Invalid session_id format passed to get_session_by_id: {session_id}")
            return None # Invalid format, cannot exist in DB
        except Exception as e:
            logger.error(f"Error retrieving session by ID '{session_id}': {str(e)}", exc_info=True)
            return None
    # --- END OF ADDED METHOD ---


    @classmethod
    async def get_chat_sessions_for_user(cls, user_id: str) -> Optional[List[Dict]]:
        """Retrieve all chat sessions for a given user."""
        try:
            client = cls.get_client()
            response = (
                client.table("chat_sessions")
                .select("*")
                .eq("user_id", user_id)
                .order("created_at", desc=True) # Optional: Order by creation time
                .execute()
            )
            return response.data # Will be [] if no sessions found, None only on error
        except Exception as e:
            logger.error(f"Error retrieving chat sessions for user {user_id}: {str(e)}", exc_info=True)
            return None # Indicate error occurred

    @classmethod
    async def get_messages_by_session_id(cls, session_id: str) -> Optional[List[Dict]]:
        """Retrieve all messages for a given session, ordered by timestamp."""
        try:
             # Validate UUID format before querying
            session_uuid = uuid.UUID(session_id)
            client = cls.get_client()
            response = (
                client.table("messages")
                .select("*")
                .eq("session_id", str(session_uuid))
                .order("created_at", desc=False) # Order messages chronologically
                .execute()
            )
            return response.data # Will be [] if no messages found, None only on error
        except ValueError:
            logger.error(f"Invalid session_id format passed to get_messages_by_session_id: {session_id}")
            return None
        except Exception as e:
            logger.error(f"Error retrieving messages for session {session_id}: {str(e)}", exc_info=True)
            return None # Indicate error occurred

    @classmethod
    async def update_chat_session_name(cls, session_id: str, session_name: str) -> bool:
        """Update the name of a chat session."""
        try:
            # Validate UUID format before querying
            session_uuid = uuid.UUID(session_id)
            client = cls.get_client()
            response = client.table("chat_sessions").update(
                {"session_name": session_name}
            ).eq("session_id", str(session_uuid)).execute()
            # Optional: Check if update actually affected rows if API provides count
            # For now, assume success if no exception
            logger.info(f"Updated session name for {session_id} to '{session_name}'")
            return True
        except ValueError:
            logger.error(f"Invalid session_id format passed to update_chat_session_name: {session_id}")
            return False
        except Exception as e:
            logger.error(f"Error updating session name for {session_id}: {str(e)}", exc_info=True)
            return False

    @classmethod
    async def store_message(
        cls,
        session_id: str,
        sender_type: str,
        content: str,
        intent: Optional[str] = None,
        visualization_data: Optional[Dict] = None,
        parent_message_id: Optional[str] = None, # Ensure this column exists in your DB table
        metadata: Optional[Dict] = None,
    ) -> bool:
        """Store a message in the database."""
        try:
             # Validate UUID format before inserting
            session_uuid = uuid.UUID(session_id)
            client = cls.get_client()
            message_data = {
                "session_id": str(session_uuid),
                "sender_type": sender_type,
                "content": content,
                "intent": intent,
                # Ensure JSON serializable data is passed for JSONB columns
                "visualization_data": visualization_data if visualization_data else None,
                "parent_message_id": str(uuid.UUID(parent_message_id)) if parent_message_id else None, # Validate parent ID if provided
                "metadata": metadata or {},
            }

            response = client.table("messages").insert(message_data).execute()
            # Optional: Check for errors in response
            # if response.error: logger.error(...) return False
            logger.debug(f"Stored message for session {session_id} by {sender_type}")
            return True
        except ValueError:
             logger.error(f"Invalid session_id or parent_message_id format for store_message. Session: {session_id}")
             return False
        except Exception as e:
            # Log the actual data causing the error if possible (be careful with sensitive info)
            logger.error(f"Exception storing message for session {session_id}: {str(e)}", exc_info=True)
            # logger.debug(f"Message data attempted: {message_data}") # Uncomment for deep debugging
            return False