"""Request/response models for the chat API."""

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator

# Languages we will generate solutions in. Also used to detect a language
# mentioned in the user's own message so the clarification turn can be skipped.
SUPPORTED_LANGUAGES = [
    "python",
    "java",
    "c++",
    "c",
    "c#",
    "javascript",
    "typescript",
    "go",
    "rust",
    "kotlin",
    "swift",
    "ruby",
    "php",
    "scala",
]


class PendingContext(BaseModel):
    """State handed to the client and echoed back on the next turn.

    The LeetCode flow spans two messages ("which language?" then the answer).
    Keeping that state in process memory meant it vanished whenever the
    instance restarted or spun down — which the free tier does after a few
    minutes idle — leaving the user with "I seem to have lost the context".
    Round-tripping it through the client makes the exchange stateless.

    Only the slug travels, not the scraped problem: it is small, and the worst a
    tampered value achieves is scraping a different public LeetCode problem,
    which the user could do by typing it anyway.
    """

    kind: Literal["awaiting_language"]
    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,119}$")
    visualize: bool = False


class ChatRequest(BaseModel):
    """One turn of user input, plus optional client-held context."""

    user_input: str
    # When set, the "which language?" round trip is skipped entirely.
    preferred_language: Optional[str] = Field(default=None, max_length=24)
    pending: Optional[PendingContext] = None

    @field_validator("preferred_language")
    @classmethod
    def _known_language(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        candidate = value.strip().lower()
        return candidate if candidate in SUPPORTED_LANGUAGES else None


class ChatResponse(BaseModel):
    """A non-streaming chat reply."""

    bot_response: str
    visualization_data: Optional[Dict[str, Any]] = None
    response_type: str = "text"


# --- Visualization payload validation -------------------------------------
#
# The prompt in app/llm/prompts.py documents the fields each visualization type
# needs, but nothing enforced them: the API only checked that a
# "visualizationType" key existed. A payload missing its data reached the p5
# renderer and drew an empty canvas, which reads as a broken product rather than
# a failed request.

# Field the renderer needs, per type. The renderer also handles "table", which
# the prompt does not currently document.
REQUIRED_FIELDS_BY_TYPE: Dict[str, List[str]] = {
    "array": ["array", "steps"],
    "sorting": ["array", "steps"],
    "graph": ["nodes", "edges", "steps"],
    "tree": ["nodes", "steps"],
    "stack": ["stack", "steps"],
    "queue": ["queue", "steps"],
    "hashmap": ["hashmap", "steps"],
    "matrix": ["matrix", "steps"],
    "linked_list": ["nodes", "steps"],
    "table": ["steps"],
}

MAX_STEPS = 60


def validate_visualization(payload: Any) -> Optional[Dict[str, Any]]:
    """Return the payload if the renderer can actually draw it, else None.

    Callers should fall back to a text-only reply rather than emitting a
    visualization frame the frontend will render as a blank box.
    """
    if not isinstance(payload, dict):
        return None

    vis_type = payload.get("visualizationType")
    if not isinstance(vis_type, str):
        return None

    vis_type = vis_type.strip().lower()
    if vis_type not in REQUIRED_FIELDS_BY_TYPE:
        return None

    for field in REQUIRED_FIELDS_BY_TYPE[vis_type]:
        if field not in payload or payload[field] is None:
            return None

    steps = payload.get("steps")
    if not isinstance(steps, list) or not steps:
        return None
    if len(steps) > MAX_STEPS:
        payload["steps"] = steps[:MAX_STEPS]
    if not all(isinstance(step, dict) for step in payload["steps"]):
        return None

    payload["visualizationType"] = vis_type
    return payload
