import time
from typing import AsyncGenerator, Dict, List, Optional

from google import genai
from google.genai import types
from pydantic import ValidationError

from app.core.config import settings
from app.core.logger import logger
from app.llm.prompts import INTENT_CLASSIFICATION_PROMPT, SCENE_PROMPT
from app.schemas.visualization_schemas import SceneResult, TraceRequest

client = genai.Client(api_key=settings.GEMINI_API_KEY)

# Default model to use - configurable via GEMINI_MODEL env var
DEFAULT_MODEL = settings.GEMINI_MODEL
SCENE_MODEL = settings.GEMINI_SCENE_MODEL


class GenerationError(Exception):
    """The model could not produce a usable answer. The message is safe to show."""


chat_config_defaults = dict(
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

# How much of the conversation the scene call sees.
MAX_CONTEXT_CHARS = 6000


def _history_contents(chat_history: Optional[List[Dict[str, str]]]) -> List[types.Content]:
    contents = []
    for msg in chat_history or []:
        role = "user" if msg["role"] == "user" else "model"
        contents.append(types.Content(role=role, parts=[types.Part(text=msg["content"])]))
    return contents


async def stream_chat_response(
    user_query: str, system_prompt: str, chat_history: List[Dict[str, str]] = None
) -> AsyncGenerator[str, None]:
    """Stream text response chunks with chat history.

    The system prompt goes in ``system_instruction``. It used to be sent as the
    first *user* turn, which the model treats as something the user said and can
    answer, quote or argue with.

    Raises ``GenerationError`` if the model fails, so the caller can send an
    error event instead of saving "Error generating response." as the answer.
    """
    contents = _history_contents(chat_history)
    contents.append(types.Content(role="user", parts=[types.Part(text=user_query)]))
    config = types.GenerateContentConfig(
        **chat_config_defaults,
        system_instruction=system_prompt or None,
    )

    try:
        response = await client.aio.models.generate_content_stream(
            model=DEFAULT_MODEL,
            contents=contents,
            config=config,
        )
        async for chunk in response:
            if chunk.text:
                yield chunk.text
    except Exception as e:
        logger.error(f"Streaming error: {str(e)}")
        raise GenerationError("The model could not generate a response.") from e


def _scene_request_text(context: str, trace: Optional[TraceRequest]) -> str:
    text = f"The conversation:\n{context[:MAX_CONTEXT_CHARS]}\n"
    if trace is not None:
        # Suggestions saved by the first version named a specific trace.
        text += (
            f"\nTrace specifically: {trace.problem}, on input {trace.input}, "
            f"using {trace.approach}.\n"
        )
    return text


async def generate_scene(
    context: str, trace: Optional[TraceRequest] = None, language: str = "python"
) -> SceneResult:
    """Trace the conversation's solution, or explain why it can't be traced.

    Called only when the user clicks "Visualize this", so nothing is spent on
    answers whose button is never pressed. The model may decline
    (``feasible: false``) rather than invent an example the problem doesn't
    support. Small inconsistencies are repaired by the ``Scene`` validator;
    only problems it can't repair are sent back once with the error.
    """
    config = types.GenerateContentConfig(
        temperature=0.3,
        max_output_tokens=16000,
        system_instruction=SCENE_PROMPT.replace("{language}", language),
        response_mime_type="application/json",
        response_schema=SceneResult,
    )
    contents: List[types.Content] = [
        types.Content(role="user", parts=[types.Part(text=_scene_request_text(context, trace))])
    ]

    last_error = "unknown error"
    for attempt in range(2):
        try:
            response = await client.aio.models.generate_content(
                model=SCENE_MODEL, contents=contents, config=config
            )
        except Exception as e:
            logger.error(f"Scene generation call failed: {e}")
            raise GenerationError("The visualization service is unavailable right now.") from e

        raw = response.text or ""
        try:
            # Validate from the raw JSON rather than trusting `response.parsed`:
            # the SDK skips Pydantic validators when parsing fails, and the
            # repairs live in those validators.
            result = SceneResult.model_validate_json(raw)
        except ValidationError as e:
            last_error = "; ".join(err["msg"] for err in e.errors()[:5])
            logger.info(f"Scene attempt {attempt + 1} invalid: {last_error}")
            contents += [
                types.Content(role="model", parts=[types.Part(text=raw[:20000])]),
                types.Content(
                    role="user",
                    parts=[types.Part(text=(
                        f"That result is invalid: {last_error}. "
                        "Return the corrected complete result."
                    ))],
                ),
            ]
            continue

        if result.scene is not None and result.scene._repairs:
            logger.info(f"Scene repaired: {'; '.join(result.scene._repairs)}")
        return result

    raise GenerationError(f"Couldn't build a valid visualization ({last_error}).")


async def classify_intent_with_llm(user_query: str) -> str:
    """Use the LLM to classify the user's intent with in-memory caching."""
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
