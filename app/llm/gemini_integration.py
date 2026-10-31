import json
import time
from typing import AsyncGenerator, Dict, List, Optional, Tuple

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


# The JSON Schema the plain-JSON fallback asks for in words. Built once.
_SCENE_JSON_SCHEMA = json.dumps(SceneResult.model_json_schema(), separators=(",", ":"))


def _scene_attempts() -> List[Tuple[str, bool]]:
    """(model, structured) in the order to try them, without duplicates.

    Structured output (``response_schema``) constrains the model best, but the
    API refuses a schema it considers too large or too deeply nested, and the
    scene model may not be enabled for every key. Either failure used to end
    the request with "unavailable". Each fallback drops one of those risks:
    plain JSON mode with the schema in the instructions, then the answer model,
    which the chat already proves works with this key.
    """
    attempts = [(SCENE_MODEL, True), (SCENE_MODEL, False), (DEFAULT_MODEL, False)]
    seen, ordered = set(), []
    for attempt in attempts:
        if attempt not in seen:
            seen.add(attempt)
            ordered.append(attempt)
    return ordered


def _scene_config(language: str, structured: bool) -> types.GenerateContentConfig:
    instruction = SCENE_PROMPT.replace("{language}", language)
    if structured:
        return types.GenerateContentConfig(
            temperature=0.3,
            max_output_tokens=16000,
            system_instruction=instruction,
            response_mime_type="application/json",
            response_schema=SceneResult,
        )
    return types.GenerateContentConfig(
        temperature=0.3,
        max_output_tokens=16000,
        system_instruction=(
            instruction
            + "\n\nReturn ONLY a JSON object matching this JSON Schema "
            + "(no markdown, no commentary):\n"
            + _SCENE_JSON_SCHEMA
        ),
        response_mime_type="application/json",
    )


async def generate_scene(
    context: str, trace: Optional[TraceRequest] = None, language: str = "python"
) -> SceneResult:
    """Trace the conversation's solution, or explain why it can't be traced.

    Called only when the user clicks "Visualize this", so nothing is spent on
    answers whose button is never pressed. The model may decline
    (``feasible: false``) rather than invent an example the problem doesn't
    support. Small inconsistencies are repaired by the ``Scene`` validator;
    only problems it can't repair are sent back once with the error.

    If a call is *refused* (schema rejected, model unavailable), the next way
    of asking is tried; see _scene_attempts.
    """
    request = types.Content(role="user", parts=[types.Part(text=_scene_request_text(context, trace))])
    refusals: List[str] = []
    last_invalid: Optional[str] = None

    for model, structured in _scene_attempts():
        mode = f"{model} ({'schema' if structured else 'json'})"
        config = _scene_config(language, structured)
        contents: List[types.Content] = [request]

        for repair in range(2):
            try:
                response = await client.aio.models.generate_content(
                    model=model, contents=contents, config=config
                )
            except Exception as e:
                refusals.append(f"{mode}: {e}")
                logger.error(f"Scene generation call failed with {mode}: {e}")
                break  # this way of asking is refused; try the next one

            raw = response.text or ""
            try:
                # Validate from the raw JSON rather than trusting
                # `response.parsed`: the SDK skips Pydantic validators when
                # parsing fails, and the repairs live in those validators.
                result = SceneResult.model_validate_json(_strip_fences(raw))
            except ValidationError as e:
                last_invalid = "; ".join(err["msg"] for err in e.errors()[:5])
                logger.info(f"Scene from {mode}, attempt {repair + 1}, invalid: {last_invalid}")
                contents = [
                    request,
                    types.Content(role="model", parts=[types.Part(text=raw[:20000])]),
                    types.Content(
                        role="user",
                        parts=[types.Part(text=(
                            f"That result is invalid: {last_invalid}. "
                            "Return the corrected complete result."
                        ))],
                    ),
                ]
                continue

            if result.scene is not None and result.scene._repairs:
                logger.info(f"Scene from {mode} repaired: {'; '.join(result.scene._repairs)}")
            logger.info(f"Scene generated with {mode}.")
            return result
        else:
            # Both tries answered but neither was usable: asking another way
            # won't help a model that understood the request.
            raise GenerationError(f"Couldn't build a valid visualization ({last_invalid}).")

    logger.error(f"Every way of generating a scene was refused: {' | '.join(refusals)}")
    raise GenerationError("The visualization service is unavailable right now.")


def _strip_fences(raw: str) -> str:
    """Drop a ```json fence, which plain JSON mode occasionally adds anyway."""
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
        text = text.rsplit("```", 1)[0]
    return text


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
