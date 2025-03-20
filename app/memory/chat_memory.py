# app/memory/chat_memory.py
from typing import Dict, List, Optional, Any

class ChatSession:
    def __init__(self, session_id: str):
        self.session_id = session_id
        self.history: List[Dict[str, str]] = []
        self.state: Dict[str, Any] = {}  # Added state dictionary

    def add_message(self, role: str, content: str):
        self.history.append({"role": role, "content": content})

    def get_history(self, context_window_size: int = 5) -> List[Dict[str, str]]:
        return self.history[-context_window_size:]

    def set_state(self, key: str, value: Any):
        self.state[key] = value

    def get_state(self, key: str, default: Any = None) -> Any:
        return self.state.get(key, default)

class ChatMemory:
    def __init__(self):
        self.sessions: Dict[str, ChatSession] = {}

    def get_session(self, session_id: str) -> ChatSession:
        if session_id not in self.sessions:
            self.sessions[session_id] = ChatSession(session_id)
        return self.sessions[session_id]