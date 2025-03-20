# app/routers/chat.py
from fastapi import APIRouter, HTTPException, Depends, Request
from fastapi.responses import StreamingResponse
from app.schemas.chat_schemas import ChatRequest
from app.llm import gemini_integration
from app.llm.prompts import GENERAL_PROMPT, CS_TUTOR_PROMPT
from app.memory.chat_memory import ChatMemory
from app.database.supabase_client import SupabaseManager
from app.rag import rag_engine
from typing import Dict, Any, List, AsyncGenerator
import uuid
import json
from app.core.logger import logger
from app.scrapers.leetcode_scraper import scrape_leetcode_question

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
    rag_keywords = {"what is", "explain", "define", "information about"}
    if any(kw in query_lower for kw in vis_keywords):
        return "visualization"
    elif any(kw in query_lower for kw in cs_keywords):
        return "cs_tutor"
    elif any(kw in query_lower for kw in rag_keywords):
        return "rag"
    else:
        return "general"

async def stream_response(
    user_input: str,
    session_id: str,
    chat_session,
    chat_history: List[Dict[str, str]]
) -> AsyncGenerator[str, None]:
    """
    Generate streaming response as SSE events, handling LeetCode scraping and solution generation.
    """
    # Check if awaiting language input
    if chat_session.get_state("awaiting_language"):
        language = user_input.strip()
        scraped_question = chat_session.get_state("scraped_question")
        if not scraped_question:
            response = "Error: No scraped question found."
            yield f"data: {json.dumps({'type': 'text', 'content': response})}\n\n"
            chat_session.add_message("bot", response)
            await SupabaseManager.store_message(
                session_id=session_id,
                sender_type="bot",
                content=response,
                intent="cs_tutor",
                visualization_data=None,
                metadata={"response_type": "error"}
            )
        else:
            prompt = f"Provide a solution to the following LeetCode question in {language}:\n{scraped_question}"
            bot_response = ""
            async for chunk in gemini_integration.stream_chat_response(
                prompt, CS_TUTOR_PROMPT, chat_history
            ):
                bot_response += chunk
                yield f"data: {json.dumps({'type': 'text', 'content': chunk})}\n\n"
            chat_session.add_message("bot", bot_response)
            chat_session.set_state("awaiting_language", False)
            chat_session.set_state("scraped_question", None)  # Clear state
            await SupabaseManager.store_message(
                session_id=session_id,
                sender_type="bot",
                content=bot_response,
                intent="cs_tutor",
                visualization_data=None,
                metadata={"response_type": "LLM"}
            )
    else:
        # Check if input is a LeetCode question identifier
        scraped_data = await scrape_leetcode_question(user_input)
        if scraped_data:
            chat_session.set_state("awaiting_language", True)
            chat_session.set_state("scraped_question", scraped_data)
            response = "I have the LeetCode question details. In what programming language would you like the solution?"
            yield f"data: {json.dumps({'type': 'text', 'content': response})}\n\n"
            chat_session.add_message("bot", response)
            await SupabaseManager.store_message(
                session_id=session_id,
                sender_type="bot",
                content=response,
                intent="cs_tutor",
                visualization_data=None,
                metadata={"response_type": "LLM"}
            )
        else:
            # Regular chat logic
            intent = classify_intent(user_input)
            bot_response = ""
            vis_data = None
            if intent == "visualization":
                vis_data = await gemini_integration.get_visualization_data(user_input)
                if vis_data:
                    yield f"data: {json.dumps({'type': 'visualization', 'data': vis_data})}\n\n"
            system_prompt = CS_TUTOR_PROMPT if intent == "cs_tutor" else GENERAL_PROMPT
            async for chunk in gemini_integration.stream_chat_response(
                user_input, system_prompt, chat_history
            ):
                bot_response += chunk
                yield f"data: {json.dumps({'type': 'text', 'content': chunk})}\n\n"
            chat_session.add_message("bot", bot_response)
            await SupabaseManager.store_message(
                session_id=session_id,
                sender_type="bot",
                content=bot_response,
                intent=intent,
                visualization_data=vis_data,
                metadata={"response_type": "LLM"}
            )

@router.post("/scrape_leetcode")
async def scrape_leetcode_endpoint(request: Request):
    """
    Endpoint to scrape a LeetCode question given an identifier (URL, name, or number).
    """
    body = await request.json()
    identifier = body.get("identifier")
    if not identifier:
        raise HTTPException(status_code=400, detail="Missing 'identifier' in request body")
    
    scraped_data = await scrape_leetcode_question(identifier)
    if not scraped_data:
        raise HTTPException(status_code=404, detail="Failed to scrape LeetCode question or question not found")
    
    return {"question_details": scraped_data}

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
        metadata={"from_frontend": True}
    )

    # Handle first message session naming
    messages_in_session = await SupabaseManager.get_messages_by_session_id(session_id)
    if not messages_in_session:
        session_name = " ".join(user_input.split()[:3]) or "New Chat"
        await SupabaseManager.update_chat_session_name(session_id, session_name)

    return StreamingResponse(
        stream_response(user_input, session_id, chat_session, chat_history),
        media_type="text/event-stream"
    )

# Existing endpoints remain unchanged
@router.post("/sessions", response_model=dict)
async def create_chat_session_endpoint(request: Request):
    user_id = "user_placeholder"
    new_session_id = uuid.uuid4()
    success = await SupabaseManager.create_chat_session(user_id, str(new_session_id))
    if not success:
        raise HTTPException(status_code=500, detail="Failed to create new chat session")
    return {"session_id": str(new_session_id)}

@router.get("/sessions", response_model=List[dict])
async def get_chat_sessions_endpoint(request: Request):
    user_id = "user_placeholder"
    sessions = await SupabaseManager.get_chat_sessions_for_user(user_id)
    return sessions if sessions else []

@router.get("/sessions/{session_id}/messages", response_model=List[dict])
async def get_session_messages_endpoint(session_id: str, request: Request):
    messages = await SupabaseManager.get_messages_by_session_id(session_id)
    return messages if messages else []