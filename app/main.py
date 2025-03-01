# FastAPI app initialization
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api import chat, health
from app.core.config import settings
from app.core.logger import logger
from app.database.supabase_client import SupabaseManager

app = FastAPI(title="AI Chatbot Backend")

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(chat.router)
app.include_router(health.router)


@app.on_event("startup")
async def startup_event():
    logger.info("Starting up the application...")
    # Initialize Supabase client on startup (optional, can also be lazy-loaded)
    SupabaseManager.get_client()  # Initialize Supabase client at startup


@app.on_event("shutdown")
async def shutdown_event():
    logger.info("Shutting down the application...")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app, host="0.0.0.0", port=8000, reload=True
    )  # reload=True for development
