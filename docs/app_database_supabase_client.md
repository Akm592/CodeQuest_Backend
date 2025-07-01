# `app/database/supabase_client.py` Documentation

## Overview

The `app/database/supabase_client.py` module provides a manager class for interacting with the Supabase database. It handles the initialization of the Supabase client and provides methods for creating, retrieving, and updating chat sessions and messages.

## Key Components

### `SupabaseManager` Class
- **Purpose**: A class that encapsulates all interactions with the Supabase database.

#### `get_client(cls) -> Client`
- **Purpose**: Initializes and returns the Supabase client.

#### `create_chat_session(...)`
- **Purpose**: Creates a new chat session in the database.

#### `get_session_by_id(cls, session_id: str) -> Optional[Dict]`
- **Purpose**: Retrieves a specific chat session by its ID.

#### `get_chat_sessions_for_user(cls, user_id: str) -> Optional[List[Dict]]`
- **Purpose**: Retrieves all chat sessions for a given user.

#### `get_messages_by_session_id(cls, session_id: str) -> Optional[List[Dict]]`
- **Purpose**: Retrieves all messages for a given session, ordered by timestamp.

#### `update_chat_session_name(cls, session_id: str, session_name: str) -> bool`
- **Purpose**: Updates the name of a chat session.

#### `store_message(...)`
- **Purpose**: Stores a message in the database.
