"""FastAPI application entry point."""

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import chat, health
from app.core.config import settings
from app.core.logger import logger
from app.scrapers.leetcode_scraper import warm_problem_list


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Validate configuration on startup, then run."""
    settings.validate()
    logger.info("Starting up.")
    if settings.WARM_LEETCODE_CACHE:
        # Not awaited: startup must not wait on LeetCode. Kept on app.state
        # because the event loop holds only a weak reference to a task.
        app.state.warm_task = asyncio.create_task(warm_problem_list())
    logger.info(f"CORS origins: {settings.CORS_ORIGINS}")
    yield
    logger.info("Shutting down.")


app = FastAPI(title="CodeQuest101 Chatbot Backend", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(chat.router)
app.include_router(health.router)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
