# app/routers/chat.py
from fastapi import APIRouter, HTTPException, Depends, Request
from fastapi.responses import StreamingResponse
from app.schemas.chat_schemas import ChatRequest # Assuming this is in app/schemas/chat_schemas.py
from app.llm import gemini_integration
from app.llm.prompts import GENERAL_PROMPT, CS_TUTOR_PROMPT, VISUALIZATION_PROMPT # Ensure prompts are correctly defined
from app.memory.chat_memory import ChatMemory, ChatSession # Import ChatSession for type hint
from app.database.supabase_client import SupabaseManager # Assuming this is configured
# Remove RAG import if not actively used in this specific flow for now
# from app.rag import rag_engine
from typing import Dict, Any, List, AsyncGenerator, Optional
import uuid
import json
import re
from app.core.logger import logger # Ensure logger is configured
from app.scrapers.leetcode_scraper import scrape_leetcode_question

router = APIRouter()
chat_memory = ChatMemory()

# Enhanced intent classification
def classify_intent(query: str) -> str:
    """Determine the initial intent of the user query."""
    if not query or not isinstance(query, str):
        return "general" # Default for empty or invalid input

    query_lower = query.lower().strip()

    # Keywords suggesting visualization request
    vis_keywords = {"visualize", "show steps", "draw", "diagram", "animate"}
    # Keywords suggesting CS tutoring or LeetCode context
    cs_keywords = {
        "computer science", "data structure", "algorithm", "explain", "how does", "implement",
        "example code", "time complexity", "space complexity", "big o", "leetcode", "problem",
        "solution", "code for", "solve"
    }
    # Patterns for LeetCode identifiers
    leetcode_url_pattern = r"leetcode\.com/problems/"
    leetcode_num_dot_pattern = r"^\s*\d+\s*\." # e.g., "368." or " 368. "
    leetcode_just_num_pattern = r"^\s*\d+\s*$" # e.g., "368"

    # Prioritize visualization if explicitly asked
    if any(kw in query_lower for kw in vis_keywords):
        logger.debug(f"Intent classified as 'visualization' based on keywords: {query[:80]}...")
        return "visualization"

    # Check for LeetCode patterns or CS keywords
    if (re.search(leetcode_url_pattern, query_lower) or
        re.match(leetcode_num_dot_pattern, query_lower) or
        re.match(leetcode_just_num_pattern, query_lower) or
        any(kw in query_lower for kw in cs_keywords)):
        logger.debug(f"Intent classified as 'cs_tutor' based on keywords/patterns: {query[:80]}...")
        return "cs_tutor"

    # Removed RAG keywords for now to simplify focus
    # rag_keywords = {"what is", "define", "information about"}
    # elif any(kw in query_lower for kw in rag_keywords):
    #     return "rag"

    logger.debug(f"Intent classified as 'general': {query[:80]}...")
    return "general"


async def stream_response(
    user_input: str,
    session_id: str,
    chat_session: ChatSession, # Use ChatSession type hint from chat_memory
    chat_history: List[Dict[str, str]]
) -> AsyncGenerator[str, None]:
    """
    Generate streaming response as SSE events, handling LeetCode scraping,
    solution generation, visualization requests, and regular chat flow.
    Yields JSON strings formatted for Server-Sent Events.
    """
    logger.info(f"[Session: {session_id}] Processing input: '{user_input[:80]}...'")

    try:
        # --- State Handling: Responding after LeetCode detected & language requested ---
        if chat_session.get_state("awaiting_language"):
            language = user_input.strip()
            scraped_question = chat_session.get_state("scraped_question")
            request_visualization = chat_session.get_state("request_visualization", False)

            # --- Input Validation ---
            if not language: # Handle empty language input
                logger.warning(f"[Session: {session_id}] User provided empty language.")
                response = "Please specify the programming language you'd like the solution in (e.g., Python, Java, C++)."
                yield f"data: {json.dumps({'type': 'text', 'content': response})}\n\n"
                # Keep awaiting language state, don't clear it
                chat_session.add_message("bot", response) # Log bot asking again
                await SupabaseManager.store_message(
                    session_id=session_id, sender_type="bot", content=response, intent="cs_tutor", metadata={"response_type": "clarification_retry"}
                )
                return # Stop processing this turn

            if not scraped_question:
                logger.error(f"[Session: {session_id}] State Error: Awaiting language but no scraped_question found.")
                response = "Error: I seem to have lost the context of the LeetCode question. Could you please provide the question identifier again?"
                yield f"data: {json.dumps({'type': 'text', 'content': response})}\n\n"
                # Reset state on error
                chat_session.set_state("awaiting_language", False)
                chat_session.set_state("scraped_question", None)
                chat_session.set_state("request_visualization", False)
                chat_session.add_message("bot", response)
                await SupabaseManager.store_message(
                    session_id=session_id, sender_type="bot", content=response, intent="error", metadata={"response_type": "state_error"}
                )
                return # Stop processing

            # --- Generate LeetCode Solution ---
            logger.info(f"[Session: {session_id}] Generating LeetCode solution in '{language}'. Visualization requested: {request_visualization}")

            # Construct the prompt for the LLM, using the CS Tutor guidelines
            prompt_for_llm = (
                f"Here is the LeetCode problem description:\n\n"
                f"```\n{scraped_question}\n```\n\n"
                f"Please provide a comprehensive, step-by-step explanation and solution for this problem in the **{language}** programming language. "
                f"Adhere strictly to the following CS Tutor response structure:\n"
                f"1.  **Problem Refresher:** Briefly restate the goal.\n"
                f"2.  **Initial Thoughts / Brute Force (If Applicable):** Explain the simplest approach, its logic, and complexity.\n"
                f"3.  **Optimized Approach(es):** Describe the core idea (e.g., DP, two pointers, sliding window, greedy), explain the logic step-by-step, provide clean, well-commented **{language}** code, and analyze Time and Space Complexity.\n"
                f"4.  **Edge Cases/Considerations:** Mention any important edge cases or constraints.\n\n"
                f"Ensure the code is correct, runnable, and follows {language} best practices."
            )

            # Append visualization request to prompt if needed
            if request_visualization:
                 prompt_for_llm += (
                     f"\n\n**Additionally:** Based on the optimal algorithm discussed, generate the necessary JSON data to visualize its key steps or the primary data structure involved (e.g., array states, DP table build-up, tree/graph traversal). "
                     f"Output this JSON *after* the textual explanation, enclosed in ```json ... ``` blocks. Use one of the standard visualization types (sorting, tree, graph, array, matrix, table, etc.) as defined previously."
                 )
                 logger.info(f"[Session: {session_id}] Visualization request added to LLM prompt.")

            bot_response_text_part = ""
            visualization_json = None
            full_llm_output = ""

            # Stream the response using CS_TUTOR_PROMPT
            # History is not passed here; the prompt is self-contained for the solution generation task.
            async for chunk in gemini_integration.stream_chat_response(
                user_query=prompt_for_llm, system_prompt=CS_TUTOR_PROMPT, chat_history=[]
            ):
                full_llm_output += chunk
                # Stream text chunks directly to the frontend
                yield f"data: {json.dumps({'type': 'text', 'content': chunk})}\n\n"

            # --- Post-Streaming Processing for Visualization ---
            if request_visualization:
                logger.info(f"[Session: {session_id}] Attempting to extract visualization JSON from LLM output.")
                # Regex to find ```json ... ``` block or a standalone JSON object/array
                # More robust regex to handle potential surrounding text and markdown variations
                json_match = re.search(r"```json\s*([\s\S]*?)\s*```|(?<!`)(\{\s*\"visualizationType\".*?\})(?!`)|(?<!`)(\[\s*\{.*?\}\s*\])(?!`)", full_llm_output, re.DOTALL | re.IGNORECASE)

                if json_match:
                    # Extract the JSON string from the first non-None group
                    json_str = next((g for g in json_match.groups() if g is not None), None)

                    if json_str:
                        try:
                            # Attempt to parse the extracted JSON string
                            visualization_json = json.loads(json_str.strip())
                            # Basic validation: check if it's a dict and has visualizationType
                            if isinstance(visualization_json, dict) and "visualizationType" in visualization_json:
                                # Remove the JSON block from the text response to avoid duplication
                                bot_response_text_part = full_llm_output.replace(json_match.group(0), "").strip()
                                logger.info(f"[Session: {session_id}] Successfully extracted and parsed visualization JSON.")
                                # Send the visualization data as a separate SSE event
                                yield f"data: {json.dumps({'type': 'visualization', 'data': visualization_json})}\n\n"
                            else:
                                logger.warning(f"[Session: {session_id}] Extracted JSON is invalid or missing 'visualizationType'. JSON: {json_str[:200]}...")
                                bot_response_text_part = full_llm_output # Keep full output if JSON is invalid
                                visualization_json = None # Reset vis_data
                        except json.JSONDecodeError as e:
                            logger.warning(f"[Session: {session_id}] Failed to parse extracted JSON: {e}. JSON string: {json_str[:200]}...")
                            bot_response_text_part = full_llm_output # Keep full output if JSON parsing failed
                            visualization_json = None
                    else:
                         logger.info("[Session: {session_id}] Regex matched, but no JSON content found in groups.")
                         bot_response_text_part = full_llm_output
                else:
                     logger.info(f"[Session: {session_id}] No visualization JSON block found in the LLM output.")
                     bot_response_text_part = full_llm_output # No JSON found, use full output
            else:
                 bot_response_text_part = full_llm_output # No visualization requested

            # --- Clean up state and store results ---
            chat_session.set_state("awaiting_language", False)
            chat_session.set_state("scraped_question", None)
            chat_session.set_state("request_visualization", False)

            # Store the final response (text part)
            chat_session.add_message("bot", bot_response_text_part)
            await SupabaseManager.store_message(
                session_id=session_id,
                sender_type="bot",
                content=bot_response_text_part,
                intent="cs_tutor", # Mark intent as cs_tutor
                visualization_data=visualization_json, # Store extracted JSON if any
                metadata={"response_type": "LLM_solution", "language": language, "visualization_provided": bool(visualization_json)}
            )
            logger.info(f"[Session: {session_id}] Finished streaming LeetCode solution.")

        # --- Regular Chat Logic / Initial LeetCode Detection ---
        else:
            initial_intent = classify_intent(user_input)
            # Check if the user explicitly asked for visualization in *this* turn
            request_visualization_this_turn = (initial_intent == "visualization")

            # --- Attempt LeetCode Scraping ---
            scraped_data = None
            # Scrape if intent suggests CS/LeetCode OR input looks like an identifier
            if initial_intent == "cs_tutor" or initial_intent == "visualization":
                logger.info(f"[Session: {session_id}] Intent is '{initial_intent}', attempting LeetCode scrape for: '{user_input[:80]}...'")
                scraped_data = await scrape_leetcode_question(user_input)
            # Add explicit check even for general intent if it looks like leetcode ID
            elif re.search(r"leetcode\.com/problems/", user_input.lower()) or re.match(r"^\s*\d+\s*[.]?", user_input):
                 logger.info(f"[Session: {session_id}] Input pattern suggests LeetCode ID, attempting scrape for: '{user_input[:80]}...'")
                 scraped_data = await scrape_leetcode_question(user_input)

            # --- LeetCode Identified: Ask for Language ---
            if scraped_data:
                logger.info(f"[Session: {session_id}] Successfully scraped LeetCode data.")
                # Set state to transition to language request flow
                chat_session.set_state("awaiting_language", True)
                chat_session.set_state("scraped_question", scraped_data)
                # Store if visualization was requested *in this turn* that triggered the scrape
                chat_session.set_state("request_visualization", request_visualization_this_turn)

                response = "I found the LeetCode question details. Which programming language would you like the solution in (e.g., Python, Java, C++)?"
                yield f"data: {json.dumps({'type': 'text', 'content': response})}\n\n"
                chat_session.add_message("bot", response) # Add bot's question to history
                await SupabaseManager.store_message(
                    session_id=session_id,
                    sender_type="bot",
                    content=response,
                    intent="cs_tutor", # Intent is now confirmed cs_tutor
                    visualization_data=None,
                    metadata={"response_type": "clarification_language", "needs_language": True}
                )

            # --- No LeetCode Found or Scrape Failed: Handle as Normal Intent ---
            else:
                logger.info(f"[Session: {session_id}] Not identified as LeetCode or scrape failed. Proceeding with intent: '{initial_intent}'")
                bot_response_text = ""
                vis_data = None
                system_prompt = GENERAL_PROMPT # Default prompt

                # --- Handle Visualization Intent (Non-LeetCode) ---
                if initial_intent == "visualization":
                    logger.info(f"[Session: {session_id}] Generating visualization data for: '{user_input[:80]}...'")
                    # Use the specific visualization function (which uses VISUALIZATION_PROMPT)
                    vis_data = await gemini_integration.get_visualization_data(user_input)
                    if vis_data and isinstance(vis_data, dict) and vis_data: # Check if dict and not empty
                        logger.info("[Session: {session_id}] Successfully generated visualization data.")
                        yield f"data: {json.dumps({'type': 'visualization', 'data': vis_data})}\n\n"
                        # Send a brief confirmation text as well
                        confirmation_text = "OK, I've generated the visualization data based on your request."
                        yield f"data: {json.dumps({'type': 'text', 'content': confirmation_text})}\n\n"
                        bot_response_text = confirmation_text # Store confirmation
                    else:
                        logger.warning(f"[Session: {session_id}] Failed to generate valid visualization data.")
                        error_text = "Sorry, I couldn't generate the visualization data for that specific request. Could you try rephrasing or asking for a supported type (like sorting, trees, graphs, arrays)?"
                        yield f"data: {json.dumps({'type': 'text', 'content': error_text})}\n\n"
                        bot_response_text = error_text

                # --- Handle CS Tutor Intent (Non-LeetCode) ---
                elif initial_intent == "cs_tutor":
                     system_prompt = CS_TUTOR_PROMPT
                     logger.info(f"[Session: {session_id}] Handling CS Tutor query (non-LeetCode): '{user_input[:80]}...'")
                     async for chunk in gemini_integration.stream_chat_response(
                         user_input, system_prompt, chat_history
                     ):
                         bot_response_text += chunk
                         yield f"data: {json.dumps({'type': 'text', 'content': chunk})}\n\n"

                # --- Handle RAG Intent (Placeholder) ---
                # elif initial_intent == "rag":
                #    # ... (Implement RAG logic here if needed) ...
                #    logger.info(f"[Session: {session_id}] Handling RAG query: '{user_input[:80]}...'")
                #    # Example: response = await handle_rag_query(user_input, chat_history)
                #    bot_response_text = "RAG response placeholder."
                #    yield f"data: {json.dumps({'type': 'text', 'content': bot_response_text})}\n\n"

                # --- Handle General Intent ---
                else: # General intent
                    logger.info(f"[Session: {session_id}] Handling general query: '{user_input[:80]}...'")
                    system_prompt = GENERAL_PROMPT
                    async for chunk in gemini_integration.stream_chat_response(
                        user_input, system_prompt, chat_history
                    ):
                        bot_response_text += chunk
                        yield f"data: {json.dumps({'type': 'text', 'content': chunk})}\n\n"

                # --- Store Final Bot Response (Non-LeetCode Flow) ---
                if bot_response_text: # Avoid storing empty messages
                    chat_session.add_message("bot", bot_response_text)
                    await SupabaseManager.store_message(
                        session_id=session_id,
                        sender_type="bot",
                        content=bot_response_text,
                        intent=initial_intent,
                        visualization_data=vis_data, # Store vis_data if generated
                        metadata={"response_type": "LLM_general"} # More specific metadata
                    )
        logger.info(f"[Session: {session_id}] Finished processing stream.")

    except Exception as e:
        logger.error(f"[Session: {session_id}] Unhandled exception in stream_response: {e}", exc_info=True)
        error_message = "An unexpected error occurred while processing your request. Please try again."
        try:
            yield f"data: {json.dumps({'type': 'error', 'content': error_message})}\n\n"
            # Also store the error message
            chat_session.add_message("bot", f"Error: {error_message}")
            await SupabaseManager.store_message(
                        session_id=session_id, sender_type="bot", content=f"Internal Error: {e}",
                        intent="error", metadata={"response_type": "exception"}
                    )
        except Exception as yield_err:
            logger.error(f"[Session: {session_id}] Failed to yield error message to client: {yield_err}")


# --- API Endpoints ---

@router.post("/scrape_leetcode")
async def scrape_leetcode_endpoint(request: Request):
    """
    Endpoint to test scraping a LeetCode question given an identifier.
    Uses the enhanced scraper logic.
    """
    try:
        body = await request.json()
        identifier = body.get("identifier")
        if not identifier or not isinstance(identifier, str):
            raise HTTPException(status_code=400, detail="Missing or invalid 'identifier' (string) in request body")

        logger.info(f"API request to scrape LeetCode identifier: {identifier}")
        scraped_data = await scrape_leetcode_question(identifier) # Use the enhanced function

        if not scraped_data:
            logger.warning(f"API scraping failed for identifier: {identifier}")
            raise HTTPException(status_code=404, detail="Failed to scrape LeetCode question. Please check the identifier (URL, number, title) or try again later.")

        logger.info(f"API successfully scraped data for identifier: {identifier}")
        return {"question_details": scraped_data}

    except json.JSONDecodeError:
         raise HTTPException(status_code=400, detail="Invalid JSON request body")
    except HTTPException as http_exc:
        # Re-raise known HTTP exceptions
        raise http_exc
    except Exception as e:
        logger.error(f"Error in /scrape_leetcode endpoint: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error during scraping.")


@router.post("/chat")
async def chat_endpoint(chat_request: ChatRequest, request: Request):
    """
    Main chat endpoint. Handles user input, manages session state,
    stores messages, and returns a streaming response.
    """
    user_input = chat_request.user_input.strip()
    if not user_input:
        raise HTTPException(status_code=400, detail="User input cannot be empty")

    # --- Session ID Handling ---
    session_id_header = request.headers.get("X-Session-ID")
    if not session_id_header:
        logger.warning("Missing X-Session-ID header in /chat request")
        raise HTTPException(status_code=400, detail="X-Session-ID header is required")
    try:
        # Validate UUID format
        session_id = str(uuid.UUID(session_id_header))
    except ValueError:
        logger.warning(f"Invalid X-Session-ID format received: {session_id_header}")
        raise HTTPException(status_code=400, detail="Invalid X-Session-ID format. Please provide a valid UUID.")

    # --- Get/Create Chat Session & History ---
    chat_session = chat_memory.get_session(session_id)
    # Get a reasonable amount of history for context, limit token usage later if needed
    chat_history = chat_session.get_history(context_window_size=10) # Get last 10 turns (user+bot)

    # --- Store User Message ---
    # Add to in-memory history first
    chat_session.add_message("user", user_input)
    # Log initial intent guess for the user message to DB
    initial_intent_for_logging = classify_intent(user_input)
    await SupabaseManager.store_message(
        session_id=session_id,
        sender_type="user",
        content=user_input,
        intent=initial_intent_for_logging, # Store initial guess based on input
        visualization_data=None,
        metadata={"from_frontend": True}
    )

    # --- Session Naming Logic (on first *user* message after session creation) ---
    # Check if session exists in DB *and* if it still has the default name "New Chat"
    # This is less reliable than checking message count, let's check messages
    messages_in_session = await SupabaseManager.get_messages_by_session_id(session_id)
    # Check if messages exist and count user messages. If only 1 user message (this one), set name.
    if messages_in_session is not None:
        user_message_count = sum(1 for msg in messages_in_session if msg.get('sender_type') == 'user')
        if user_message_count == 1:
             # Use first few words of the *first* user message
             session_name = " ".join(user_input.split()[:5]) # Use up to 5 words
             if not session_name: session_name = "Chat" # Fallback if input was whitespace
             logger.info(f"Setting session name for {session_id} to '{session_name}' based on first user message.")
             # Ensure session actually exists before updating name
             session_exists = await SupabaseManager.get_session_by_id(session_id) # Need this helper
             if session_exists:
                await SupabaseManager.update_chat_session_name(session_id, session_name)
             else:
                logger.warning(f"Attempted to set name for non-existent session {session_id} in DB.")


    # --- Return Streaming Response ---
    return StreamingResponse(
        stream_response(user_input, session_id, chat_session, chat_history),
        media_type="text/event-stream",
        headers={
            'Cache-Control': 'no-cache',
            'Connection': 'keep-alive',
            'X-Accel-Buffering': 'no' # Often needed for Nginx buffering issues with SSE
            }
    )


# --- Session Management Endpoints (Keep as provided originally, ensure imports) ---
from typing import List # Add List import if not already present

@router.post("/sessions", response_model=dict)
async def create_chat_session_endpoint(request: Request):
    """Creates a new chat session entry in the database."""
    # TODO: Replace placeholder with actual user authentication/identification
    user_id = "user_placeholder" # Replace with actual user ID from auth
    new_session_id = str(uuid.uuid4())
    # Create session with default name
    success = await SupabaseManager.create_chat_session(user_id, new_session_id, session_name="New Chat")
    if not success:
        logger.error(f"Failed to create new chat session for user {user_id}")
        raise HTTPException(status_code=500, detail="Failed to create new chat session in database")
    logger.info(f"Created new chat session {new_session_id} for user {user_id}")
    return {"session_id": new_session_id}

@router.get("/sessions", response_model=List[dict])
async def get_chat_sessions_endpoint(request: Request):
    """Retrieves all chat sessions for the current user."""
    # TODO: Replace placeholder with actual user authentication/identification
    user_id = "user_placeholder" # Replace with actual user ID from auth
    sessions = await SupabaseManager.get_chat_sessions_for_user(user_id)
    if sessions is None:
        # Distinguish between DB error and no sessions found
        logger.error(f"Database error retrieving sessions for user {user_id}")
        raise HTTPException(status_code=500, detail="Could not retrieve chat sessions")
    return sessions # Returns [] if user exists but has no sessions, which is correct

@router.get("/sessions/{session_id}/messages", response_model=List[dict])
async def get_session_messages_endpoint(session_id: str, request: Request):
    """Retrieves all messages for a given chat session."""
    # TODO: Add authorization check: Does the current user own this session_id?
    try:
        # Validate session_id format
        uuid.UUID(session_id)
    except ValueError:
         raise HTTPException(status_code=400, detail="Invalid session_id format. Must be a UUID.")

    messages = await SupabaseManager.get_messages_by_session_id(session_id)
    if messages is None:
        logger.error(f"Database error retrieving messages for session {session_id}")
        raise HTTPException(status_code=500, detail="Could not retrieve messages for this session")
    # It's okay to return an empty list if the session exists but has no messages yet
    return messages

# REMINDER: Ensure you have added the `get_session_by_id` async method
#           to the `SupabaseManager` class in `app/database/supabase_client.py`
#           as shown in the previous response.