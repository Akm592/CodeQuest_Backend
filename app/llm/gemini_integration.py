import json
import re
import time
from typing import Any, AsyncGenerator, Dict, List, Optional

from google import genai
from google.genai import types

from app.core.config import settings
from app.core.logger import logger
from app.llm.prompts import VISUALIZATION_PROMPT , INTENT_CLASSIFICATION_PROMPT

client = genai.Client(api_key=settings.GEMINI_API_KEY)

# Default model to use - configurable via GEMINI_MODEL env var
DEFAULT_MODEL = settings.GEMINI_MODEL

# Generation configurations
visualization_config = types.GenerateContentConfig(
    temperature=0.7,
    top_p=0.95,
    top_k=40,
    max_output_tokens=8192,
    response_mime_type="application/json",
)

chat_config = types.GenerateContentConfig(
    temperature=0.8,
    top_p=0.9,
    top_k=20,
    max_output_tokens=10000,
)

# Faster, more deterministic config for classification
classification_config = types.GenerateContentConfig(
    temperature=0.0,
    top_p=0.95,
    top_k=40,
    max_output_tokens=100,
)

# Simple in-memory cache for intent classification
_intent_cache: Dict[str, str] = {}
MAX_CACHE_SIZE = 100


def clean_json_response(raw_text: str) -> str:
    """Extract JSON from model response, handling surrounding text."""
    match = re.search(r"```json\s*([\s\S]*?)\s*```", raw_text)
    if match:
        return match.group(1).strip()
    # If no ```json block, try to parse the whole text as JSON
    try:
        json.loads(raw_text.strip())
        return raw_text.strip()
    except json.JSONDecodeError:
        return raw_text.strip() # Return original text if not a valid JSON block

async def get_visualization_data(user_query: str) -> Optional[Dict[str, Any]]:
    """Generate visualization data."""
    try:
        chat = client.aio.chats.create(
            model=DEFAULT_MODEL,
            config=visualization_config,
        )
        response = await chat.send_message(
            VISUALIZATION_PROMPT + "\n\n" + user_query
        )
        cleaned_text = clean_json_response(response.text)
        return json.loads(cleaned_text) if cleaned_text else None
    except Exception as e:
        logger.error(f"Visualization error: {str(e)}")
        return None

async def get_chat_response(
    user_query: str, system_prompt: str, chat_history: List[Dict[str, str]] = None
) -> str:
    """Generate full text response with chat history (non-streaming).

    Args:
        user_query: The current user query
        system_prompt: System instructions for the model
        chat_history: List of previous messages [{"role": "user", "content": "..."}, ...]

    """
    try:
        # Prepare history in the format expected by google.genai
        # [{'role': 'user', 'parts': [{'text': '...'}]}, {'role': 'model', 'parts': [{'text': '...'}]}]
        history = []
        if chat_history:
            for message in chat_history:
                role = "user" if message["role"] == "user" else "model"
                history.append(types.Content(role=role, parts=[types.Part(text=message["content"])]))

        chat = client.aio.chats.create(
            model=DEFAULT_MODEL,
            config=chat_config,
            history=history
        )
        if system_prompt:
            # Send system prompt as the first message if needed, or better, include it in config if supported as system instruction.
            # But adhering to previous logic: send it first.
            await chat.send_message(system_prompt)

        response = await chat.send_message(user_query)
        return response.text.strip()
    except Exception as e:
        logger.error(f"Chat error: {str(e)}")
        return "I couldn't generate a response. Please try again."

async def stream_chat_response(
    user_query: str, system_prompt: str, chat_history: List[Dict[str, str]] = None
) -> AsyncGenerator[str, None]:
    """Stream text response chunks with chat history.

    Args:
        user_query: The current user query
        system_prompt: System instructions for the model
        chat_history: List of previous messages [{"role": "user", "content": "..."}, ...]

    """
    try:
        # Construct contents list
        contents = []
        if system_prompt:
             contents.append(types.Content(role="user", parts=[types.Part(text=system_prompt)]))
        if chat_history:
            for msg in chat_history:
                role = "user" if msg["role"] == "user" else "model"
                contents.append(types.Content(role=role, parts=[types.Part(text=msg["content"])]))
        contents.append(types.Content(role="user", parts=[types.Part(text=user_query)]))

        # Stream response
        # Using generate_content_stream for one-off generation with context manually constructed, 
        # mirroring the previous logic which passed a list of contents.
        response = await client.aio.models.generate_content_stream(
            model=DEFAULT_MODEL,
            contents=contents,
            config=chat_config,
        )
        async for chunk in response:
            logger.debug(f"Response chunk: {chunk.text}")
            yield chunk.text
    except Exception as e:
        logger.error(f"Streaming error: {str(e)}")
        yield "Error generating response."



async def get_contextual_visualization_data(
    user_query: str,
    chat_history: List[Dict[str, str]] = None,
    algorithm_context: str = None,
) -> Optional[Dict[str, Any]]:
    """Generate visualization data with conversation and example context."""
    try:
        context_prompt = VISUALIZATION_PROMPT

        if algorithm_context:
            context_prompt += f"\n\nAlgorithm Solution Context:\n{algorithm_context}\n"

        if chat_history:
            recent_context = []
            for msg in chat_history[-4:]:
                if any(
                    keyword in msg.get("content", "").lower()
                    for keyword in ["algorithm", "solution", "code", "problem", "example"]
                ):
                    recent_context.append(f"{msg['role']}: {msg['content'][:300]}...")

            if recent_context:
                context_prompt += "\n\nRecent Conversation Context:\n" + "\n".join(recent_context) + "\n"

        
        chat = client.aio.chats.create(
            model=DEFAULT_MODEL,
            config=visualization_config,
        )
        response = await chat.send_message(context_prompt + "\n\nUser Request: " + user_query)

        cleaned_text = clean_json_response(response.text)
        result = json.loads(cleaned_text) if cleaned_text else None

        if result and isinstance(result, dict):
            logger.info(f"Generated contextual visualization using example data for: {user_query[:50]}...")
            return result
        else:
            logger.warning(f"Generated invalid visualization data: {cleaned_text[:200]}...")
            return None

    except Exception as e:
        logger.error(f"Contextual visualization error: {str(e)}")
        return None


# INTENT_CLASSIFICATION_PROMPT = """
# You are an expert intent classifier for a computer science tutoring chatbot. Your task is to analyze the user's query and classify it into one of the following categories:

# - "visualization": The user wants to see a step-by-step execution of an algorithm, often with specific data. They use words like "visualize", "show steps", "trace", "draw", "animate", or provide an algorithm name along with data like an array or graph.
# - "cs_tutor": The user wants an explanation of a concept, an algorithm, or a solution to a problem (like a LeetCode question). They use words like "teach me", "explain", "how does...work", "what is", "solve", or ask for complexity analysis.
# - "general": The query is a general conversation starter, a greeting, or a question not related to a specific CS concept or visualization.

# Analyze the following user query and return ONLY the single-word classification. Do not add any other text, reasoning, or markdown.

# Examples:
# - Query: "Bubble sort visualization with array [64, 34, 25, 12, 22, 11, 90]" -> visualization
# - Query: "teach me binary search" -> cs_tutor
# - Query: "solve leetcode 1. two sum" -> cs_tutor
# - Query: "Quick sort with pivot selection on [3, 6, 8, 10, 1, 2, 1]" -> visualization
# - Query: "what is the time complexity of merge sort?" -> cs_tutor
# - Query: "hi how are you" -> general
# - Query: "BFS traversal starting from node A in graph with edges [(A,B), (A,C), (B,D), (C,D)]" -> visualization

# User Query: "{user_query}"
# """

async def classify_intent_with_llm(user_query: str) -> str:
    """Uses the LLM to classify the user's intent with in-memory caching."""
    # Normalize query for better cache hits
    normalized_query = user_query.strip().lower()
    
    # Check cache first
    if normalized_query in _intent_cache:
        logger.info(f"Cache hit for intent: '{normalized_query[:30]}...' -> {_intent_cache[normalized_query]}")
        return _intent_cache[normalized_query]

    try:
        start_time = time.perf_counter()
        prompt = INTENT_CLASSIFICATION_PROMPT.format(user_query=user_query)

        response = await client.aio.models.generate_content(
            model=DEFAULT_MODEL,
            contents=prompt,
            config=classification_config,
        )

        intent = response.text.strip().lower()
        duration = time.perf_counter() - start_time

        if intent in ["visualization", "cs_tutor", "general"]:
            logger.info(f"LLM classified intent for '{user_query[:60]}...' as: {intent} (took {duration:.2f}s)")
            
            # Update cache (simple FIFO-ish pruning if it gets too big)
            if len(_intent_cache) >= MAX_CACHE_SIZE:
                # Remove a random key or first key if needed, here just clearing oldest entry
                oldest_key = next(iter(_intent_cache))
                del _intent_cache[oldest_key]
            
            _intent_cache[normalized_query] = intent
            return intent
        else:
            logger.warning(f"LLM returned an invalid intent classification: '{intent}'. Defaulting to 'general'.")
            return "general"

    except Exception as e:
        logger.error(f"Error during LLM intent classification: {e}. Defaulting to 'general'.")
        return "general"
