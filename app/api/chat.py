from fastapi import APIRouter, HTTPException, Depends, Request
from fastapi.responses import StreamingResponse
from app.schemas.chat_schemas import ChatRequest
from app.llm import gemini_integration
from app.llm.prompts import GENERAL_PROMPT, CS_TUTOR_PROMPT
from app.memory.chat_memory import ChatMemory
from app.database.supabase_client import SupabaseManager
from app.rag import rag_engine
from typing import Optional, Dict, Any, List, AsyncGenerator
import uuid
import json
from app.core.logger import logger

router = APIRouter()
chat_memory = ChatMemory()

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

async def stream_response(
    intent: str,
    user_input: str,
    session_id: str,
    chat_session,
    chat_history: List[Dict[str, str]]
) -> AsyncGenerator[str, None]:
    """
    Generate streaming response as SSE events.
    """
    bot_response = ""
    vis_data = None
    try:
        if intent == "visualization":
            vis_data = await gemini_integration.get_visualization_data(user_input)
            if vis_data:
                yield f"data: {json.dumps({'type': 'visualization', 'data': vis_data})}\n\n"
            async for chunk in gemini_integration.stream_chat_response(
                user_input, CS_TUTOR_PROMPT, chat_history
            ):
                logger.debug(f"Text chunk: {chunk}")
                bot_response += chunk
                yield f"data: {json.dumps({'type': 'text', 'content': chunk})}\n\n"
        elif intent == "rag":
            context = await rag_engine.retrieve_relevant_context(user_input)
            response = await rag_engine.generate_rag_response(
                user_input, context, chat_history
            ) if context else "I'm sorry, I cannot find relevant information."
            bot_response = response
            logger.debug(f"RAG response: {response}")
            yield f"data: {json.dumps({'type': 'text', 'content': response})}\n\n"
        else:  # "cs_tutor" or "general"
            system_prompt = CS_TUTOR_PROMPT if intent == "cs_tutor" else GENERAL_PROMPT
            async for chunk in gemini_integration.stream_chat_response(
                user_input, system_prompt, chat_history
            ):
                logger.debug(f"Text chunk: {chunk}")
                bot_response += chunk
                yield f"data: {json.dumps({'type': 'text', 'content': chunk})}\n\n"
    finally:
        # Add bot response to chat history
        chat_session.add_message("bot", bot_response)
        # Store bot message in Supabase
        await SupabaseManager.store_message(
            session_id=session_id,
            sender_type="bot",
            content=bot_response,
            intent=intent,
            visualization_data=vis_data,
            metadata={"response_type": "LLM"},
        )
        logger.info(f"Full bot response for session {session_id}: {bot_response}")
        # Note: Token usage logging is omitted as it's not directly available in streaming mode

@router.post("/chat")
async def chat_endpoint(chat_request: ChatRequest, request: Request):
    user_input = chat_request.user_input.strip()
    if not user_input:
        raise HTTPException(status_code=400, detail="Empty input")

    session_id_header = request.headers.get("X-Session-ID")
    if not session_id_header:
        raise HTTPException(status_code=400, detail="X-Session-ID header is missing")
    try:
        session_id = str(uuid.UUID(session_id_header))
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid X-Session-ID format")

    # Get chat session and history
    chat_session = chat_memory.get_session(session_id)
    chat_history = chat_session.get_history(context_window_size=5)
    chat_session.add_message("user", user_input)

    # Store user message
    intent = classify_intent(user_input)
    await SupabaseManager.store_message(
        session_id=session_id,
        sender_type="user",
        content=user_input,
        intent=intent,
        visualization_data=None,
        metadata={"from_frontend": True},
    )

    # Handle first message session naming
    messages_in_session = await SupabaseManager.get_messages_by_session_id(session_id)
    if not messages_in_session:
        session_name = " ".join(user_input.split()[:3]) or "New Chat"
        await SupabaseManager.update_chat_session_name(session_id, session_name)

    # Return streaming response
    return StreamingResponse(
        stream_response(intent, user_input, session_id, chat_session, chat_history),
        media_type="text/event-stream"
    )


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