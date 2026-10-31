"""Request/response models for the chat API."""

from typing import Literal, Optional

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
