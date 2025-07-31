# app/memory/chat_memory.py
from collections import deque
from typing import Any, Dict, List


class ChatSession:
    """Manages the chat history and state for a single session."""

    def __init__(self, session_id: str, max_history_length: int = 5):
        self.session_id = session_id
        self.history: deque[Dict[str, str]] = deque(maxlen=max_history_length)
        self.state: Dict[str, Any] = {}  # Added state dictionary

    def add_message(self, role: str, content: str):
        """Add a message to the session's history."""
        self.history.append({"role": role, "content": content})

    def get_history(self) -> List[Dict[str, str]]:
        """Return the current chat history."""
        return list(self.history)

    def set_state(self, key: str, value: Any):
        """Set a state variable for the session."""
        self.state[key] = value

    def get_state(self, key: str, default: Any = None) -> Any:
        """Get a state variable from the session."""
        return self.state.get(key, default)

class ChatMemory:
    """Manages multiple chat sessions."""

    def __init__(self):
        self.sessions: Dict[str, ChatSession] = {}

    def get_session(self, session_id: str, max_history_length: int = 5) -> ChatSession:
        """Retrieve or create a chat session."""
        if session_id not in self.sessions:
            self.sessions[session_id] = ChatSession(session_id, max_history_length=max_history_length)
        return self.sessions[session_id]
