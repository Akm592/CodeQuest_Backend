"""Chat API.

Guests and signed-in users share these endpoints. A guest is a caller with no
``Authorization`` header at all: their sessions are ephemeral, nothing is
persisted, and they are rate limited. A signed-in caller has had their token
verified by ``app.core.auth`` and every database operation runs as them, so row
level security applies.
"""

import hashlib
import json
import re
import uuid
from collections import OrderedDict
from typing import Any, AsyncGenerator, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from app.core.auth import AuthenticatedUser, get_current_user
from app.core.logger import logger
from app.core.rate_limit import check_rate_limit
from app.database.supabase_client import SupabaseManager
from app.llm import gemini_integration
from app.llm.prompts import CS_TUTOR_PROMPT, GENERAL_PROMPT
from app.memory.chat_memory import ChatMemory, ChatSession
from app.schemas.chat_schemas import (
    SUPPORTED_LANGUAGES,
    ChatRequest,
    PendingContext,
)
from app.schemas.visualization_schemas import Suggestion, VisualizeRequest
from app.scrapers.leetcode_scraper import fetch_leetcode_question, scrape_leetcode_question

router = APIRouter()
chat_memory = ChatMemory()

# Cap how much scraped problem text goes into a prompt.
MAX_PROBLEM_CHARS = 8000


def _sse(payload: Dict[str, Any]) -> str:
    """Format one Server-Sent Event."""
    return f"data: {json.dumps(payload)}\n\n"


def detect_language(text: str) -> Optional[str]:
    """Find a supported language named in the user's message.

    Lets "solve two sum in java" go straight to an answer instead of asking
    which language the user wants.
    """
    lowered = text.lower()
    for language in SUPPORTED_LANGUAGES:
        # Word-boundary match, but "c++"/"c#" contain regex metacharacters.
        pattern = r"(?<![a-z0-9])" + re.escape(language) + r"(?![a-z0-9+#])"
        if re.search(pattern, lowered):
            return language
    return None


def _problem_text(scraped: Dict[str, Any]) -> str:
    """Return the prompt-ready text for a scraped problem.

    ``formatted_content`` exists precisely for this. The previous code
    interpolated the whole dict into an f-string, so the model received a Python
    repr — single-quoted keys, literal backslash-n, and the problem body
    duplicated because it appears both in ``content`` and inside
    ``formatted_content``.
    """
    text = scraped.get("formatted_content") or scraped.get("content") or ""
    return text[:MAX_PROBLEM_CHARS]


def _examples_text(scraped: Dict[str, Any]) -> str:
    """Render the structured examples the scraper extracts.

    These are the reason the scraper returns structured data at all, and nothing
    was using them.
    """
    examples = scraped.get("examples") or []
    if not examples:
        return ""

    lines = []
    for example in examples[:3]:
        number = example.get("example_number", "?")
        raw_input = (example.get("input") or {}).get("raw")
        raw_output = (example.get("output") or {}).get("raw")
        if raw_input is None and raw_output is None:
            continue
        lines.append(f"Example {number}: Input: {raw_input} -> Output: {raw_output}")

    if not lines:
        return ""
    return "\n\nWorked examples from the problem statement:\n" + "\n".join(lines)


def _solution_prompt(scraped: Dict[str, Any], language: str) -> str:
    """Build the LeetCode solution prompt.

    It used to append "also output visualization JSON in a ```json block" when
    a visualization was wanted. That JSON was then regex-extracted from the
    answer, failed to parse whenever it nested, and was left in the chat text
    either way. Visualizations are now offered as suggestions after the answer
    and generated separately against a schema.
    """
    return (
        f"Here is the LeetCode problem description:\n\n"
        f"```\n{_problem_text(scraped)}\n```"
        f"{_examples_text(scraped)}\n\n"
        f"Please provide a comprehensive, step-by-step explanation and solution for this "
        f"problem in the **{language}** programming language. "
        f"Adhere strictly to the following CS Tutor response structure:\n"
        f"1.  **Problem Refresher:** Briefly restate the goal.\n"
        f"2.  **Initial Thoughts / Brute Force (If Applicable):** Explain the simplest "
        f"approach, its logic, and complexity.\n"
        f"3.  **Optimized Approach(es):** Describe the core idea, explain the logic "
        f"step-by-step, provide clean, well-commented **{language}** code, and analyze "
        f"Time and Space Complexity.\n"
        f"4.  **Edge Cases/Considerations:** Mention any important edge cases or constraints.\n\n"
        f"Ensure the code is correct, runnable, and follows {language} best practices."
    )


async def _suggest(question: str, answer: str, auto_open: bool = False):
    """Offer visualizations for an answer.

    Yields the SSE event (if there is anything to offer) and finally the
    suggestions as plain dicts, for storing with the message. Kept separate
    from the answer stream so a failure here can never cost the user the
    answer they already have.
    """
    suggestions: List[Suggestion] = []
    if answer.strip():
        suggestions = await gemini_integration.suggest_visualizations(question, answer)
    data = [s.model_dump(exclude_none=True) for s in suggestions]
    if data:
        yield _sse({"type": "suggestions", "data": data, "autoOpen": auto_open})
    yield data


def _suggestion_metadata(suggestions: List[Dict[str, Any]], auto_open: bool) -> Dict[str, Any]:
    if not suggestions:
        return {}
    return {"suggestions": suggestions, "auto_open": auto_open}


async def _persist(
    db: Optional[SupabaseManager],
    session_id: str,
    sender_type: str,
    content: str,
    intent: Optional[str] = None,
    visualization_data: Optional[Dict] = None,
    metadata: Optional[Dict] = None,
) -> None:
    """Store a message when the caller is signed in. A no-op for guests."""
    if db is None:
        return
    await db.store_message(
        session_id=session_id,
        sender_type=sender_type,
        content=content,
        intent=intent,
        visualization_data=visualization_data,
        metadata=metadata,
    )


async def _resolve_pending(
    chat_session: ChatSession, pending: Optional[PendingContext]
) -> Optional[Dict[str, Any]]:
    """Recover the problem an "awaiting language" turn refers to.

    Client-supplied context is tried first because it survives the instance
    restarting or spinning down; in-process state is the warm fast path.
    """
    if pending is not None:
        scraped = await fetch_leetcode_question(pending.slug)
        if scraped:
            return scraped
        logger.info(f"Could not re-resolve pending slug '{pending.slug}'.")

    if chat_session.get_state("awaiting_language"):
        return chat_session.get_state("scraped_question")

    return None


def _clear_pending(chat_session: ChatSession) -> None:
    chat_session.set_state("awaiting_language", False)
    chat_session.set_state("scraped_question", None)
    chat_session.set_state("request_visualization", False)


async def _stream_solution(
    scraped: Dict[str, Any],
    language: str,
    visualize: bool,
    session_id: str,
    chat_session: ChatSession,
    db: Optional[SupabaseManager],
    question: str,
) -> AsyncGenerator[str, None]:
    """Generate and stream a LeetCode solution, then offer visualizations."""
    logger.info(f"[{session_id}] Generating solution in '{language}' (visualize={visualize}).")

    full_output = ""
    async for chunk in gemini_integration.stream_chat_response(
        user_query=_solution_prompt(scraped, language),
        system_prompt=CS_TUTOR_PROMPT,
        chat_history=[],
    ):
        full_output += chunk
        yield _sse({"type": "text", "content": chunk})

    _clear_pending(chat_session)
    chat_session.add_message("bot", full_output)

    suggestions: List[Dict[str, Any]] = []
    async for item in _suggest(f"LeetCode: {scraped.get('title', '')}\n{question}", full_output, visualize):
        if isinstance(item, str):
            yield item
        else:
            suggestions = item

    await _persist(
        db,
        session_id,
        "bot",
        full_output,
        intent="cs_tutor",
        metadata={
            "response_type": "LLM_solution",
            "language": language,
            **_suggestion_metadata(suggestions, visualize),
        },
    )


async def stream_response(
    user_input: str,
    session_id: str,
    chat_session: ChatSession,
    chat_history: List[Dict[str, str]],
    db: Optional[SupabaseManager] = None,
    preferred_language: Optional[str] = None,
    pending: Optional[PendingContext] = None,
) -> AsyncGenerator[str, None]:
    """Produce the SSE stream for one user turn."""
    logger.info(f"[{session_id}] Processing a message.")

    try:
        # --- Answering the "which language?" question -----------------------
        awaiting = pending is not None or chat_session.get_state("awaiting_language")
        answer_language = preferred_language or (
            user_input.strip().lower()
            if user_input.strip().lower() in SUPPORTED_LANGUAGES
            else detect_language(user_input)
        )
        if awaiting and answer_language is None:
            # The user moved on instead of naming a language. Previously the
            # bot re-asked "which language?" for as long as the in-process
            # flag stayed set, so any other question got the same reply.
            _clear_pending(chat_session)
            awaiting = False

        if awaiting:
            language = answer_language or ""
            visualize = bool(pending.visualize) if pending else bool(
                chat_session.get_state("request_visualization", False)
            )

            scraped = await _resolve_pending(chat_session, pending)

            if not scraped:
                _clear_pending(chat_session)
                message = (
                    "I lost track of which problem we were on. "
                    "Could you send the LeetCode question again?"
                )
                yield _sse({"type": "text", "content": message})
                chat_session.add_message("bot", message)
                await _persist(
                    db, session_id, "bot", message, intent="error",
                    metadata={"response_type": "state_error"},
                )
                return

            async for event in _stream_solution(
                scraped, language, visualize, session_id, chat_session, db, user_input
            ):
                yield event
            return

        # --- A fresh turn ---------------------------------------------------
        intent = await gemini_integration.classify_intent_with_llm(user_input)
        wants_visualization = intent == "visualization"

        scraped = None
        if intent in ("cs_tutor", "visualization") or re.search(
            r"leetcode\.com/problems/", user_input.lower()
        ) or re.match(r"^\s*\d+\s*[.]?", user_input):
            scraped = await scrape_leetcode_question(user_input)

        if scraped:
            language = preferred_language or detect_language(user_input)
            if language:
                # Skip the clarification turn entirely.
                async for event in _stream_solution(
                    scraped, language, wants_visualization, session_id, chat_session, db,
                    user_input,
                ):
                    yield event
                return

            chat_session.set_state("awaiting_language", True)
            chat_session.set_state("scraped_question", scraped)
            chat_session.set_state("request_visualization", wants_visualization)

            message = (
                f"I found **{scraped.get('title', 'the question')}**. "
                "Which programming language would you like the solution in "
                "(e.g. Python, Java, C++)?"
            )
            yield _sse({"type": "text", "content": message})
            yield _sse({
                "type": "pending",
                "data": {
                    "kind": "awaiting_language",
                    "slug": scraped.get("slug", ""),
                    "visualize": wants_visualization,
                },
            })
            chat_session.add_message("bot", message)
            await _persist(
                db, session_id, "bot", message, intent="cs_tutor",
                metadata={"response_type": "clarification_language", "needs_language": True},
            )
            return

        # --- Not a LeetCode problem -----------------------------------------
        # An explicit "visualize X" request gets a normal (brief) explanation,
        # and its first suggestion is opened straight away by the client.
        system_prompt = GENERAL_PROMPT if intent == "general" else CS_TUTOR_PROMPT
        query = user_input
        if wants_visualization:
            query = (
                f"{user_input}\n\n(The user asked for a visualization. An animated "
                "step-by-step trace will be shown right below your answer, so keep the "
                "explanation concise: the idea, the key steps, and the complexity. Do "
                "not draw ASCII diagrams or ask whether they want a dry run.)"
            )

        bot_text = ""
        async for chunk in gemini_integration.stream_chat_response(
            query, system_prompt, chat_history
        ):
            bot_text += chunk
            yield _sse({"type": "text", "content": chunk})

        if not bot_text:
            return

        chat_session.add_message("bot", bot_text)

        suggestions: List[Dict[str, Any]] = []
        if intent != "general" or wants_visualization:
            async for item in _suggest(user_input, bot_text, wants_visualization):
                if isinstance(item, str):
                    yield item
                else:
                    suggestions = item

        await _persist(
            db, session_id, "bot", bot_text, intent=intent,
            metadata={
                "response_type": "LLM_general",
                **_suggestion_metadata(suggestions, wants_visualization),
            },
        )

    except gemini_integration.GenerationError as exc:
        logger.error(f"[{session_id}] Model failure: {exc}")
        yield _sse({
            "type": "error",
            "content": "The AI model didn't respond. Please try again in a moment.",
        })
    except Exception as exc:
        logger.error(f"[{session_id}] Unhandled error in stream_response: {exc}", exc_info=True)
        message = "Something went wrong while answering. Please try again."
        try:
            yield _sse({"type": "error", "content": message})
            chat_session.add_message("bot", message)
            # Store the generic text, not the exception: this content is rendered
            # straight back into the user's chat history.
            await _persist(
                db, session_id, "bot", message, intent="error",
                metadata={"response_type": "exception"},
            )
        except Exception as inner:
            logger.error(f"[{session_id}] Could not deliver the error message: {inner}")


# --- Endpoints -------------------------------------------------------------


@router.post("/scrape_leetcode")
async def scrape_leetcode_endpoint(request: Request):
    """Look up a LeetCode question by URL, number or title."""
    try:
        body = await request.json()
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON request body")

    identifier = body.get("identifier")
    if not identifier or not isinstance(identifier, str):
        raise HTTPException(
            status_code=400, detail="Missing or invalid 'identifier' (string) in request body"
        )

    scraped = await scrape_leetcode_question(identifier)
    if not scraped:
        raise HTTPException(
            status_code=404,
            detail=(
                "Could not find that LeetCode question. Check the identifier "
                "(URL, number or title) and try again."
            ),
        )
    return {"question_details": scraped}


# Generated scenes, keyed by the request. Reopening a suggestion, or two people
# clicking the same one, should not pay for generation twice.
_SCENE_CACHE: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
SCENE_CACHE_SIZE = 200


def _scene_cache_key(body: VisualizeRequest) -> str:
    raw = json.dumps(
        {"trace": body.trace.model_dump(), "context": body.context or ""}, sort_keys=True
    )
    return hashlib.sha256(raw.encode()).hexdigest()


@router.post("/chat/visualize")
async def visualize_endpoint(
    body: VisualizeRequest,
    request: Request,
    user: Optional[AuthenticatedUser] = Depends(get_current_user),
):
    """Build the step-by-step scene for a suggestion the user clicked."""
    key = _scene_cache_key(body)
    cached = _SCENE_CACHE.get(key)
    if cached is not None:
        _SCENE_CACHE.move_to_end(key)
        return {"scene": cached}

    if user is None:
        check_rate_limit(request)

    language = detect_language(body.context or "") or "python"
    try:
        scene = await gemini_integration.generate_scene(body.trace, body.context, language)
    except gemini_integration.GenerationError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    data = scene.model_dump(exclude_none=True)
    _SCENE_CACHE[key] = data
    if len(_SCENE_CACHE) > SCENE_CACHE_SIZE:
        _SCENE_CACHE.popitem(last=False)
    return {"scene": data}


@router.post("/chat")
async def chat_endpoint(
    chat_request: ChatRequest,
    request: Request,
    user: Optional[AuthenticatedUser] = Depends(get_current_user),
):
    """Send a message and receive the reply as a Server-Sent Events stream."""
    user_input = chat_request.user_input.strip()
    if not user_input:
        raise HTTPException(status_code=400, detail="User input cannot be empty")

    session_id_header = request.headers.get("X-Session-ID")
    if not session_id_header:
        raise HTTPException(status_code=400, detail="X-Session-ID header is required")
    try:
        session_id = str(uuid.UUID(session_id_header))
    except ValueError:
        raise HTTPException(
            status_code=400, detail="Invalid X-Session-ID format. Provide a valid UUID."
        )

    db: Optional[SupabaseManager] = None

    if user is None:
        # Guest: ephemeral session, nothing persisted, rate limited.
        check_rate_limit(request)
    else:
        db = await SupabaseManager.for_user(user)
        # Confirm the caller owns this session before anything is written to it.
        # Without this an authenticated caller could write messages into any
        # session UUID they cared to guess.
        session_name = " ".join(user_input.split()[:5]) or "New Chat"
        if not await db.ensure_session_owned(session_id, session_name=session_name):
            raise HTTPException(status_code=404, detail="Chat session not found")

    chat_session = chat_memory.get_session(session_id)
    chat_history = chat_session.get_history()

    # After a restart the in-process history is empty; rebuild it from the
    # database so a signed-in user's conversation survives a spindown.
    if user is not None and db is not None and not chat_history:
        stored = await db.get_messages_by_session_id(session_id)
        if stored:
            for message in stored[-10:]:
                role = "user" if message.get("sender_type") == "user" else "bot"
                chat_session.add_message(role, message.get("content") or "")
            chat_history = chat_session.get_history()

    chat_session.add_message("user", user_input)
    if db is not None:
        await db.store_message(
            session_id=session_id,
            sender_type="user",
            content=user_input,
            intent=None,
            metadata={"from_frontend": True},
        )

    return StreamingResponse(
        stream_response(
            user_input,
            session_id,
            chat_session,
            chat_history,
            db=db,
            preferred_language=chat_request.preferred_language,
            pending=chat_request.pending,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # Nginx and similar buffer SSE without this.
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/sessions", response_model=dict)
async def create_chat_session_endpoint(
    user: Optional[AuthenticatedUser] = Depends(get_current_user),
):
    """Start a chat session. Guests get an id without a database row."""
    new_session_id = str(uuid.uuid4())

    if user is None:
        return {"session_id": new_session_id}

    db = await SupabaseManager.for_user(user)
    if not await db.create_chat_session(new_session_id, session_name="New Chat"):
        raise HTTPException(status_code=500, detail="Could not create a chat session")
    return {"session_id": new_session_id}


@router.get("/sessions", response_model=List[dict])
async def get_chat_sessions_endpoint(
    user: Optional[AuthenticatedUser] = Depends(get_current_user),
):
    """List the caller's chat sessions. Guests have none."""
    if user is None:
        return []

    db = await SupabaseManager.for_user(user)
    sessions = await db.get_chat_sessions()
    if sessions is None:
        raise HTTPException(status_code=500, detail="Could not retrieve chat sessions")
    return sessions


@router.get("/sessions/{session_id}/messages", response_model=List[dict])
async def get_session_messages_endpoint(
    session_id: str,
    user: Optional[AuthenticatedUser] = Depends(get_current_user),
):
    """Messages for one of the caller's sessions.

    A session the caller does not own is reported as 404 rather than 403, so the
    endpoint does not confirm which session ids exist.
    """
    if user is None:
        return []

    try:
        uuid.UUID(session_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid session_id format. Must be a UUID.")

    db = await SupabaseManager.for_user(user)
    if not await db.owns_session(session_id):
        raise HTTPException(status_code=404, detail="Chat session not found")

    messages = await db.get_messages_by_session_id(session_id)
    if messages is None:
        raise HTTPException(status_code=500, detail="Could not retrieve messages")
    return messages
