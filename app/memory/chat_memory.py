# Chat memory management
from typing import Dict, List, Optional


class ChatSession:
    def __init__(self, session_id: str):
        self.session_id = session_id
        self.history: List[Dict[str, str]] = []  # Store history as list of turns

    def add_message(self, role: str, content: str):
        self.history.append({"role": role, "content": content})

    def get_history(self, context_window_size: int = 5) -> List[Dict[str, str]]:
        """Returns the most recent messages within the context window."""
        return self.history[-context_window_size:]


class ChatMemory:
    def __init__(self):
        self.sessions: Dict[str, ChatSession] = {}

    def get_session(self, session_id: str) -> ChatSession:
        if session_id not in self.sessions:
            self.sessions[session_id] = ChatSession(session_id)
        return self.sessions[session_id]
