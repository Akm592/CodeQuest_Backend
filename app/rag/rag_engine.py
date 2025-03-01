# RAG logic with conversation context support
from typing import Optional, List, Dict
from app.llm.prompts import RAG_PROMPT_TEMPLATE, RAG_WITH_HISTORY_TEMPLATE

# In a real RAG system, you would load and index documents here.
# For this basic example, let's use a simple in-memory knowledge base.
KNOWLEDGE_BASE = {
    "data_structures": "Data structures are ways to organize and store data...",
    "algorithms": "Algorithms are step-by-step procedures...",
    "python_basics": "Python is a high-level programming language...",
    # ... more knowledge snippets ...
}


async def retrieve_relevant_context(query: str) -> Optional[str]:
    """
    Basic context retrieval - in a real RAG, this would involve vector search.
    For now, we'll do a simple keyword-based lookup.
    """
    query_lower = query.lower()
    relevant_context = ""
    for topic, content in KNOWLEDGE_BASE.items():
        if topic in query_lower:  # Very basic keyword matching
            relevant_context += f"\n{topic.upper()}: {content}\n"
    return relevant_context.strip() if relevant_context else None


async def generate_rag_response(
    user_query: str, context: str, chat_history: List[Dict[str, str]] = None
) -> str:
    """
    Generates a response using RAG prompt, retrieved context, and conversation history.

    Args:
        user_query: The current user query
        context: Retrieved context information
        chat_history: List of previous messages in format [{"role": "user", "content": "..."}, {"role": "bot", "content": "..."}]
    """
    from app.llm.gemini_integration import get_chat_response  # Avoid circular import

    # Create a formatted conversation history string
    conversation_context = ""
    if chat_history and len(chat_history) > 0:
        conversation_context = "Previous conversation:\n"
        for message in chat_history:
            role = "Human" if message["role"] == "user" else "Assistant"
            conversation_context += f"{role}: {message['content']}\n"

    # Use a template that includes conversation history
    if conversation_context:
        prompt = RAG_WITH_HISTORY_TEMPLATE.format(
            context=context,
            conversation_history=conversation_context,
            user_question=user_query,
        )
    else:
        # Use the original template if no history exists
        prompt = RAG_PROMPT_TEMPLATE.format(context=context, user_question=user_query)

    # We pass an empty system prompt since the RAG template already contains instructions
    return await get_chat_response(
        user_query=prompt,
        system_prompt="",
        chat_history=None,  # History is already included in the prompt
    )
