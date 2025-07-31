# Pydantic models for requests/responses
from typing import Any, Dict, Optional

from pydantic import BaseModel


class ChatRequest(BaseModel):
    user_input: str


class ChatResponse(BaseModel):
    bot_response: str
    visualization_data: Optional[Dict[str, Any]] = None
    response_type: str = "text"
