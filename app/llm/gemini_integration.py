import google.generativeai as genai
from typing import AsyncGenerator
from app.core.config import settings
from app.core.logger import logger
import json
import re
from typing import Dict, Any, Optional, List
from app.llm.prompts import VISUALIZATION_PROMPT

genai.configure(api_key=settings.GEMINI_API_KEY)

# Generation configurations
visualization_config = {
    "temperature": 0.7,
    "top_p": 0.95,
    "top_k": 40,
    "max_output_tokens": 8192,
    "response_mime_type": "application/json",
}

chat_config = {
    "temperature": 0.8,
    "top_p": 0.9,
    "top_k": 20,
    "max_output_tokens": 2048,
}

# Initialize models
visualization_model = genai.GenerativeModel(
    "gemini-2.0-flash-lite",
    generation_config=visualization_config,
)
chat_model = genai.GenerativeModel(
    "gemini-2.0-flash-lite",
    generation_config=chat_config,
)

def clean_json_response(raw_text: str) -> str:
    """Extract JSON from model response."""
    text = re.sub(r"```json\s*", "", raw_text)
    text = re.sub(r"```\s*$", "", text)
    return text.strip()

async def get_visualization_data(user_query: str) -> Optional[Dict[str, Any]]:
    """Generate visualization data."""
    try:
        chat_session = visualization_model.start_chat()
        response = await chat_session.send_message_async(
            VISUALIZATION_PROMPT + "\n\n" + user_query
        )
        cleaned_text = clean_json_response(response.text)
        return json.loads(cleaned_text) if cleaned_text else None
    except Exception as e:
        logger.error(f"Visualization error: {str(e)}")
        return None

async def get_chat_response(
    user_query: str, system_prompt: str, chat_history: List[Dict[str, str]] = None
) -> str:
    """
    Generate full text response with chat history (non-streaming).

    Args:
        user_query: The current user query
        system_prompt: System instructions for the model
        chat_history: List of previous messages [{"role": "user", "content": "..."}, ...]
    """
    try:
        chat = chat_model.start_chat(history=[])
        if system_prompt:
            await chat.send_message_async(system_prompt)
        if chat_history:
            for message in chat_history:
                role = "user" if message["role"] == "user" else "model"
                if role == "user":
                    await chat.send_message_async(message["content"])
                else:
                    chat.history.append({"role": "model", "parts": [message["content"]]})
        response = await chat.send_message_async(user_query)
        return response.text.strip()
    except Exception as e:
        logger.error(f"Chat error: {str(e)}")
        return "I couldn't generate a response. Please try again."

async def stream_chat_response(
    user_query: str, system_prompt: str, chat_history: List[Dict[str, str]] = None
) -> AsyncGenerator[str, None]:
    """
    Stream text response chunks with chat history.

    Args:
        user_query: The current user query
        system_prompt: System instructions for the model
        chat_history: List of previous messages [{"role": "user", "content": "..."}, ...]
    """
    try:
        # Construct contents list
        contents = []
        if system_prompt:
            contents.append({"role": "user", "parts": [system_prompt]})
        if chat_history:
            for msg in chat_history:
                role = "user" if msg["role"] == "user" else "model"
                contents.append({"role": role, "parts": [msg["content"]]})
        contents.append({"role": "user", "parts": [user_query]})

        # Stream response
        response = await chat_model.generate_content_async(contents, stream=True)
        async for chunk in response:
            logger.debug(f"Response chunk: {chunk.text}")
            yield chunk.text
    except Exception as e:
        logger.error(f"Streaming error: {str(e)}")
        yield "Error generating response."