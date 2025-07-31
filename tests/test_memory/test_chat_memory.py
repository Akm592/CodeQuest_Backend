import pytest
from app.memory.chat_memory import ChatSession, ChatMemory

def test_chat_session_initialization():
    session_id = "test_session_1"
    session = ChatSession(session_id)
    assert session.session_id == session_id
    assert session.history == []
    assert session.state == {}

def test_chat_session_add_message():
    session = ChatSession("test_session_2")
    session.add_message("user", "Hello")
    assert len(session.history) == 1
    assert session.history[0] == {"role": "user", "content": "Hello"}
    session.add_message("bot", "Hi there!")
    assert len(session.history) == 2
    assert session.history[1] == {"role": "bot", "content": "Hi there!"}

def test_chat_session_get_history():
    session = ChatSession("test_session_3")
    session.add_message("user", "1")
    session.add_message("bot", "2")
    session.add_message("user", "3")
    session.add_message("bot", "4")
    session.add_message("user", "5")
    session.add_message("bot", "6")

    # Test default context window size (5)
    history = session.get_history()
    assert len(history) == 5
    assert history[0] == {"role": "bot", "content": "2"}
    assert history[4] == {"role": "bot", "content": "6"}

    # Test custom context window size
    history = session.get_history(context_window_size=3)
    assert len(history) == 3
    assert history[0] == {"role": "bot", "content": "4"}
    assert history[2] == {"role": "bot", "content": "6"}

    history = session.get_history(context_window_size=10)
    assert len(history) == 6 # Should return all if less than window size

def test_chat_session_set_and_get_state():
    session = ChatSession("test_session_4")
    session.set_state("awaiting_language", True)
    assert session.get_state("awaiting_language") is True
    assert session.get_state("scraped_question") is None # Test default value

    session.set_state("scraped_question", "Two Sum problem")
    assert session.get_state("scraped_question") == "Two Sum problem"
    session.set_state("awaiting_language", False)
    assert session.get_state("awaiting_language") is False

def test_chat_memory_get_session():
    memory = ChatMemory()
    session_id_1 = "session_id_1"
    session_id_2 = "session_id_2"

    session1 = memory.get_session(session_id_1)
    assert isinstance(session1, ChatSession)
    assert session1.session_id == session_id_1

    # Getting the same session_id should return the same instance
    session1_again = memory.get_session(session_id_1)
    assert session1 is session1_again

    session2 = memory.get_session(session_id_2)
    assert isinstance(session2, ChatSession)
    assert session2.session_id == session_id_2
    assert session1 is not session2

    assert len(memory.sessions) == 2