"""Logging setup.

This used to hardcode ``level=DEBUG`` and always attach a FileHandler. On a chat
product DEBUG meant full prompts and message content were written to the log,
which is a privacy problem, and the log file lived on an ephemeral container
filesystem where nobody could read it anyway.
"""

import logging

from app.core.config import settings


def configure_logger() -> logging.Logger:
    """Configure and return the application logger."""
    level = getattr(logging, settings.LOG_LEVEL, logging.INFO)

    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if settings.APP_LOG_FILE:
        handlers.append(logging.FileHandler(settings.APP_LOG_FILE))

    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(message)s - {%(pathname)s:%(lineno)d}",
        handlers=handlers,
    )
    return logging.getLogger("codequest")


logger = configure_logger()
