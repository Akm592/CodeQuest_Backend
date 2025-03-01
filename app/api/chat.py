# Chat endpoint logic with conversation context
from fastapi import APIRouter, HTTPException, Depends, Request
from app.schemas.chat_schemas import ChatRequest, ChatResponse
from app.llm import gemini_integration
from app.llm.prompts import GENERAL_PROMPT, CS_TUTOR_PROMPT
from app.memory.chat_memory import ChatMemory
from app.database.supabase_client import SupabaseManager
from app.rag import rag_engine
from typing import Optional, Dict, Any, List
import uuid
from app.core.logger import logger

router = APIRouter()
chat_memory = ChatMemory()  # In-memory chat memory


def classify_intent(query: str) -> str:
    """Determine the intent of the user query."""
    query_lower = query.lower()
    vis_keywords = {"visualize", "show", "steps", "algorithm", "sort", "tree", "graph"}
    cs_keywords = {
        "computer science",
        "data structure",
        "algorithm",
        "explain",
        "how to",
        "example",
    }
    rag_keywords = {
        "what is",
        "explain",
        "define",
        "information about",
    }

    if any(kw in query_lower for kw in vis_keywords):
        return "visualization"
    elif any(kw in query_lower for kw in cs_keywords):
        return "cs_tutor"
    elif any(kw in query_lower for kw in rag_keywords):
        return "rag"
    else:
        return "general"


@router.post("/chat", response_model=ChatResponse)
async def chat_endpoint(chat_request: ChatRequest, request: Request):
    user_input = chat_request.user_input.strip()
    if not user_input:
        raise HTTPException(status_code=400, detail="Empty input")

    session_id_header = request.headers.get("X-Session-ID")
    if not session_id_header:
        raise HTTPException(status_code=400, detail="X-Session-ID header is missing")
    try:
        session_id = uuid.UUID(session_id_header)  # Validate UUID format
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid X-Session-ID format")

    # Get chat session and previous context (from in-memory for now)
    chat_session = chat_memory.get_session(str(session_id))
    previous_messages = chat_session.get_history(context_window_size=5)
    chat_session.add_message(
        "user", user_input
    )  # Add user message to in-memory history

    intent = classify_intent(user_input)
    visualization_data: Optional[Dict[str, Any]] = None
    system_prompt = GENERAL_PROMPT
    bot_response = ""

    if intent == "visualization":
        visualization_data = await gemini_integration.get_visualization_data(user_input)
        system_prompt = CS_TUTOR_PROMPT
    elif intent == "cs_tutor":
        system_prompt = CS_TUTOR_PROMPT
    elif intent == "rag":
        context = await rag_engine.retrieve_relevant_context(user_input)
        if context:
            bot_response = await rag_engine.generate_rag_response(
                user_input, context, previous_messages
            )
        else:
            bot_response = "I'm sorry, I cannot find relevant information in my knowledge base to answer your question."
    else:  # intent == "general"
        pass

    if not bot_response and intent != "rag":
        bot_response = await gemini_integration.get_chat_response(
            user_input, system_prompt, previous_messages
        )
    chat_session.add_message(
        "bot", bot_response
    )  # Add bot response to in-memory history

    # Store user message in Supabase
    first_user_message = False
    session_name = None
    messages_in_session_response = await SupabaseManager.get_messages_by_session_id(
        str(session_id)
    )
    if messages_in_session_response is None or len(messages_in_session_response) == 0:
        first_user_message = True
        session_name = user_input.split()[:3]  # First 3 words
        session_name = " ".join(session_name) if session_name else "New Chat"
        await SupabaseManager.update_chat_session_name(
            str(session_id), session_name
        )  # Set session name

    await SupabaseManager.store_message(
        session_id=str(session_id),
        sender_type="user",
        content=user_input,
        intent=intent,
        visualization_data=None,
        metadata={"from_frontend": True},
    )

    message_stored = await SupabaseManager.store_message(
        session_id=str(session_id),
        sender_type="bot",
        content=bot_response,
        intent=intent,
        visualization_data=visualization_data,
        metadata={"response_type": "LLM"},
    )
    if not message_stored:
        logger.warning(f"Failed to store bot message in session {session_id}")

    return {
        "bot_response": bot_response,
        "visualization_data": visualization_data,
        "response_type": "visualization" if visualization_data else "text",
    }


@router.post("/sessions", response_model=dict)  # POST to create new session
async def create_chat_session_endpoint(request: Request):
    user_id = "user_placeholder"  # Replace with actual user ID retrieval from auth context/headers
    new_session_id = uuid.uuid4()  # Generate new UUID for session
    success = await SupabaseManager.create_chat_session(user_id, str(new_session_id))
    if not success:
        raise HTTPException(
            status_code=500, detail="Failed to create new chat session in database"
        )
    return {"session_id": str(new_session_id)}


@router.get("/sessions", response_model=List[dict])  # GET to list sessions
async def get_chat_sessions_endpoint(request: Request):
    user_id = "user_placeholder"  # Replace with actual user ID retrieval
    sessions = await SupabaseManager.get_chat_sessions_for_user(user_id)
    return sessions if sessions else []


@router.get(
    "/sessions/{session_id}/messages", response_model=List[dict]
)  # GET messages for a session
async def get_session_messages_endpoint(session_id: str, request: Request):
    messages = await SupabaseManager.get_messages_by_session_id(session_id)
    return messages if messages else []
