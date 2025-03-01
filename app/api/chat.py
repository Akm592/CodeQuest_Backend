# Chat endpoint logic with conversation context
from fastapi import APIRouter, HTTPException, Depends, Request
from app.schemas.chat_schemas import ChatRequest, ChatResponse
from app.llm import gemini_integration
from app.llm.prompts import GENERAL_PROMPT, CS_TUTOR_PROMPT
from app.memory.chat_memory import ChatMemory
from app.database.supabase_client import SupabaseManager
from app.rag import rag_engine
from typing import Optional, Dict, Any

router = APIRouter()
chat_memory = (
    ChatMemory()
)  # Initialize chat memory outside the endpoint for session persistence


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

    # Get session ID from request header
    session_id = request.headers.get("X-Session-ID")
    if not session_id:
        session_id = "default_session"  # Fallback if no session ID provided

    # Get chat session and previous context
    chat_session = chat_memory.get_session(session_id)

    # Get the previous messages for context (before adding the current message)
    # Adjust context_window_size as needed (e.g., 5-10 messages)
    previous_messages = chat_session.get_history(context_window_size=5)

    # Now add the current user message to history
    chat_session.add_message("user", user_input)

    intent = classify_intent(user_input)
    visualization_data: Optional[Dict[str, Any]] = None
    system_prompt = GENERAL_PROMPT
    bot_response = ""  # Initialize bot_response

    if intent == "visualization":
        visualization_data = await gemini_integration.get_visualization_data(user_input)
        system_prompt = CS_TUTOR_PROMPT
    elif intent == "cs_tutor":
        system_prompt = CS_TUTOR_PROMPT
    elif intent == "rag":
        context = await rag_engine.retrieve_relevant_context(user_input)
        if context:
            # For RAG, we still want to include chat history for continuity
            bot_response = await rag_engine.generate_rag_response(
                user_input, context, previous_messages
            )
        else:
            bot_response = "I'm sorry, I cannot find relevant information in my knowledge base to answer your question."
    else:  # intent == "general"
        pass  # Use default GENERAL_PROMPT

    if not bot_response and intent != "rag":
        # Pass the previous messages to provide context
        bot_response = await gemini_integration.get_chat_response(
            user_input, system_prompt, previous_messages
        )

    # Store the bot response in chat history
    chat_session.add_message("bot", bot_response)

    # Store chat history in Supabase
    await SupabaseManager.store_chat_history(
        session_id=session_id,
        user_message=user_input,
        bot_message=bot_response,
        intent=intent,
        visualization_data=visualization_data,
    )

    return {
        "bot_response": bot_response,
        "visualization_data": visualization_data,
        "response_type": "visualization" if visualization_data else "text",
    }
