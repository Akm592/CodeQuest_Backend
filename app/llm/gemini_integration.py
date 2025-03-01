# Gemini API integration with chat history support
import google.generativeai as genai
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
    "gemini-1.5-flash-8b",
    generation_config=visualization_config,
)
chat_model = genai.GenerativeModel(
    "learnlm-1.5-pro-experimental",
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
    Generate contextual text response with chat history.

    Args:
        user_query: The current user query
        system_prompt: System instructions for the model
        chat_history: List of previous messages in the format [{"role": "user", "content": "..."}, {"role": "bot", "content": "..."}]
    """
    try:
        # Start a chat session
        chat = chat_model.start_chat(history=[])

        # Add system prompt as the first message
        if system_prompt:
            await chat.send_message_async(system_prompt)

        # Add chat history if provided
        if chat_history and len(chat_history) > 0:
            for message in chat_history:
                role = "user" if message["role"] == "user" else "model"
                content = message["content"]

                # Add each historical message to the chat
                if role == "user":
                    await chat.send_message_async(content)
                else:
                    # We need to manually add bot responses to the history
                    chat.history.append({"role": "model", "parts": [content]})

        # Send the current user query and get the response
        response = await chat.send_message_async(user_query)
        return response.text.strip()
    except Exception as e:
        logger.error(f"Chat error: {str(e)}")
        return "I couldn't generate a response. Please try again."
