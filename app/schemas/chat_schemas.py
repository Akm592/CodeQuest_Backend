# Pydantic models for requests/responses
from pydantic import BaseModel
from typing import Optional, Dict, Any


class ChatRequest(BaseModel):
    user_input: str


class ChatResponse(BaseModel):
    bot_response: str
    visualization_data: Optional[Dict[str, Any]] = None
    response_type: str = "text"
