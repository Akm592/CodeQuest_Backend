# Health check endpoint
import os

from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
async def health_check():
    """Return the health status, and the deployed commit when the host sets it."""
    # Render sets RENDER_GIT_COMMIT; it shows which code is live.
    return {"status": "OK", "commit": os.getenv("RENDER_GIT_COMMIT", "")[:7]}
