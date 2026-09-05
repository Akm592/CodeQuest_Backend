# Health check endpoint
from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
async def health_check():
    """Return the health status of the service."""
    return {"status": "OK"}
