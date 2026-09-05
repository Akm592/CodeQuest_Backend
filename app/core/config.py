"""Application configuration.

Note that nothing here raises at import time. Validation lives in ``validate()``,
which the app calls on startup. Raising during module import made the settings
module impossible to import without a fully populated environment, which is why
the test suite could not even collect.
"""

import json
import os
from typing import Dict, List

from dotenv import load_dotenv

load_dotenv()

_DEFAULT_CORS_ORIGINS = [
    "http://localhost:5173",
    "https://codequest101.vercel.app",
]


def _split_origins(raw: str) -> List[str]:
    """Parse a comma-separated origin list.

    Trailing slashes are stripped: an ``Origin`` header never has a path, so
    ``https://example.com/`` can never match and is silently dead config.
    """
    origins = []
    for part in raw.split(","):
        origin = part.strip().rstrip("/")
        if origin:
            origins.append(origin)
    return origins


class Settings:
    """Environment-backed settings."""

    def __init__(self) -> None:
        self.GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
        self.GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite")
        self.SUPABASE_URL: str = os.getenv("SUPABASE_URL", "")
        self.SUPABASE_ANON_KEY: str = os.getenv("SUPABASE_ANON_KEY", "")

        # JSON object mapping a client IP to its own limit, e.g.
        # {"1.2.3.4": 100, "5.6.7.8": -1}. -1 means unlimited.
        # Unlisted callers get GUEST_RATE_LIMIT.
        self.RATE_LIMIT_RULES: Dict[str, int] = self._parse_rate_limit_rules(
            os.getenv("RATE_LIMIT_RULES", "{}")
        )
        self.GUEST_RATE_LIMIT: int = int(os.getenv("GUEST_RATE_LIMIT", "10"))

        # Number of reverse proxies in front of the app. Render puts exactly one
        # there. The client IP is taken this many entries from the *right* of
        # X-Forwarded-For; entries further left are supplied by the caller and
        # cannot be trusted.
        self.TRUSTED_PROXY_HOPS: int = int(os.getenv("TRUSTED_PROXY_HOPS", "1"))

        self.LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO").upper()
        # Empty means "log to stdout only". The container filesystem is
        # ephemeral, so a log file is of limited use in production anyway.
        self.APP_LOG_FILE: str = os.getenv("APP_LOG_FILE", "")

        self.CORS_ORIGINS: List[str] = (
            _split_origins(os.getenv("CORS_ORIGINS", "")) or list(_DEFAULT_CORS_ORIGINS)
        )

    @staticmethod
    def _parse_rate_limit_rules(raw: str) -> Dict[str, int]:
        """Parse the rules once, at startup, and complain loudly if they are broken.

        This used to be parsed per request inside a bare ``except`` that swallowed
        the error, so a typo silently degraded every caller to the default limit
        with nothing in the logs.
        """
        if not raw or not raw.strip():
            return {}
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            # Cannot use app.core.logger here: it imports this module.
            print(f"WARNING: RATE_LIMIT_RULES is not valid JSON ({exc}); ignoring it.")
            return {}
        if not isinstance(parsed, dict):
            print("WARNING: RATE_LIMIT_RULES must be a JSON object; ignoring it.")
            return {}

        rules: Dict[str, int] = {}
        for key, value in parsed.items():
            try:
                rules[str(key)] = int(value)
            except (TypeError, ValueError):
                print(f"WARNING: RATE_LIMIT_RULES entry {key!r} is not an integer; ignoring it.")
        return rules

    def validate(self) -> None:
        """Fail fast on startup if required configuration is missing."""
        missing = [
            name
            for name in ("GEMINI_API_KEY", "SUPABASE_URL", "SUPABASE_ANON_KEY")
            if not getattr(self, name)
        ]
        if missing:
            raise ValueError(
                "Missing required environment variable(s): "
                + ", ".join(missing)
                + ". See .env.example."
            )
        if "*" in self.CORS_ORIGINS:
            # Browsers reject this combination anyway; better to fail at boot
            # than to debug it as a CORS error later.
            raise ValueError(
                "CORS_ORIGINS cannot be '*' because credentials are allowed. "
                "List the frontend origins explicitly."
            )


settings = Settings()
