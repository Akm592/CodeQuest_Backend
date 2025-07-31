import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from app.llm.gemini_integration import (
    clean_json_response,
    get_visualization_data,
    get_chat_response,
    stream_chat_response,
    get_contextual_visualization_data,
)
from app.llm.prompts import VISUALIZATION_PROMPT

# Test for clean_json_response
def test_clean_json_response():
    assert clean_json_response("```json\n{\"key\": \"value\"}\n```") == '{\"key\": \"value\"}'
    assert clean_json_response("```json\n{\"key\": \"value\"}\n```\n") == '{\"key\": \"value\"}'
    assert clean_json_response("```json\n{\"key\": \"value\"}```") == '{\"key\": \"value\"}'
    assert clean_json_response("{\"key\": \"value\"}") == '{\"key\": \"value\"}'
    assert clean_json_response("  {\"key\": \"value\"}  ") == '{\"key\": \"value\"}'
    assert clean_json_response("```json\n[1, 2, 3]\n```") == '[1, 2, 3]'
    assert clean_json_response("""Some text before ```json
{"key": "value"}
``` some text after""") == '{"key": "value"}'
    assert clean_json_response("No JSON here") == "No JSON here"
    assert clean_json_response("") == ""

@pytest.fixture(autouse=True)
def mock_generative_models():
    with patch('app.llm.gemini_integration.visualization_model') as mock_visualization_model:
        with patch('app.llm.gemini_integration.chat_model') as mock_chat_model:
            yield mock_visualization_model, mock_chat_model

@pytest.mark.asyncio
async def test_get_visualization_data_success(mock_generative_models):
    mock_visualization_model, _ = mock_generative_models
    mock_chat_session = AsyncMock()
    mock_visualization_model.start_chat.return_value = mock_chat_session
    mock_chat_session.send_message_async.return_value.text = '```json\n{\"type\": \"array\", \"data\": [1, 2, 3]}\n```'

    user_query = "visualize array [1,2,3]"
    result = await get_visualization_data(user_query)

    mock_visualization_model.start_chat.assert_called_once()
    mock_chat_session.send_message_async.assert_called_once_with(
        VISUALIZATION_PROMPT + "\n\n" + user_query
    )
    assert result == {"type": "array", "data": [1, 2, 3]}

@pytest.mark.asyncio
async def test_get_visualization_data_invalid_json(mock_generative_models):
    mock_visualization_model, _ = mock_generative_models
    mock_chat_session = AsyncMock()
    mock_visualization_model.start_chat.return_value = mock_chat_session
    mock_chat_session.send_message_async.return_value.text = "Invalid JSON response"

    user_query = "visualize something"
    result = await get_visualization_data(user_query)

    assert result is None

@pytest.mark.asyncio
async def test_get_visualization_data_exception(mock_generative_models):
    mock_visualization_model, _ = mock_generative_models
    mock_visualization_model.start_chat.side_effect = Exception("API error")

    user_query = "visualize something"
    result = await get_visualization_data(user_query)

    assert result is None

@pytest.mark.asyncio
async def test_get_chat_response_success(mock_generative_models):
    _, mock_chat_model = mock_generative_models
    mock_chat_session = AsyncMock()
    mock_chat_model.start_chat.return_value = mock_chat_session
    mock_chat_session.send_message_async.return_value.text = "Hello from bot"

    user_query = "Hi"
    system_prompt = "You are a helpful assistant."
    chat_history = []

    result = await get_chat_response(user_query, system_prompt, chat_history)

    mock_chat_model.start_chat.assert_called_once_with(history=[])
    mock_chat_session.send_message_async.assert_any_call(system_prompt)
    mock_chat_session.send_message_async.assert_called_with(user_query)
    assert result == "Hello from bot"

@pytest.mark.asyncio
async def test_get_chat_response_with_history(mock_generative_models):
    _, mock_chat_model = mock_generative_models
    mock_chat_session = AsyncMock()
    mock_chat_model.start_chat.return_value = mock_chat_session
    mock_chat_session.send_message_async.return_value.text = "Bot response with history"

    user_query = "What is Python?"
    system_prompt = "You are a helpful assistant."
    chat_history = [
        {"role": "user", "content": "Hello"},
        {"role": "model", "content": "Hi there!"},
    ]

    result = await get_chat_response(user_query, system_prompt, chat_history)

    mock_chat_model.start_chat.assert_called_once_with(history=[])
    mock_chat_session.send_message_async.assert_any_call(system_prompt)
    mock_chat_session.send_message_async.assert_any_call("Hello")
    # For model messages, they are appended to chat.history directly, not sent via send_message_async
    assert mock_chat_session.send_message_async.call_count == 2 # system_prompt and user_query
    assert mock_chat_session.history[1] == {'role': 'model', 'parts': ['Hi there!']}
    assert result == "Bot response with history"

@pytest.mark.asyncio
async def test_stream_chat_response_success(mock_generative_models):
    _, mock_chat_model = mock_generative_models
    
    # Mock the async generator behavior
    async def mock_generate_content_async(*args, **kwargs):
        yield MagicMock(text="chunk1")
        yield MagicMock(text="chunk2")
        yield MagicMock(text="chunk3")

    mock_chat_model.generate_content_async.side_effect = mock_generate_content_async

    user_query = "Tell me a story"
    system_prompt = "You are a storyteller."
    chat_history = []

    chunks = [chunk async for chunk in stream_chat_response(user_query, system_prompt, chat_history)]

    assert chunks == ["chunk1", "chunk2", "chunk3"]
    mock_chat_model.generate_content_async.assert_called_once()
    args, kwargs = mock_chat_model.generate_content_async.call_args
    assert len(args[0]) == 2 # system_prompt and user_query
    assert args[0][0]['parts'][0] == system_prompt
    assert args[0][1]['parts'][0] == user_query

@pytest.mark.asyncio
async def test_stream_chat_response_with_history(mock_generative_models):
    _, mock_chat_model = mock_generative_models
    
    async def mock_generate_content_async(*args, **kwargs):
        yield MagicMock(text="history_chunk1")
        yield MagicMock(text="history_chunk2")

    mock_chat_model.generate_content_async.side_effect = mock_generate_content_async

    user_query = "Continue the story"
    system_prompt = "You are a storyteller."
    chat_history = [
        {"role": "user", "content": "Once upon a time"},
        {"role": "model", "content": "there was a brave knight"},
    ]

    chunks = [chunk async for chunk in stream_chat_response(user_query, system_prompt, chat_history)]

    assert chunks == ["history_chunk1", "history_chunk2"]
    args, kwargs = mock_chat_model.generate_content_async.call_args
    assert len(args[0]) == 4 # system_prompt, user_msg, model_msg, current_user_query
    assert args[0][0]['parts'][0] == system_prompt
    assert args[0][1]['parts'][0] == "Once upon a time"
    assert args[0][2]['parts'][0] == "there was a brave knight"
    assert args[0][3]['parts'][0] == user_query

@pytest.mark.asyncio
async def test_get_contextual_visualization_data_success(mock_generative_models):
    mock_visualization_model, _ = mock_generative_models
    mock_chat_session = AsyncMock()
    mock_visualization_model.start_chat.return_value = mock_chat_session
    mock_chat_session.send_message_async.return_value.text = '```json\n{\"type\": \"graph\", \"nodes\": [{\"id\": \"A\"}]}\n```'

    user_query = "visualize graph"
    chat_history = []
    algorithm_context = "DFS algorithm"

    result = await get_contextual_visualization_data(user_query, chat_history, algorithm_context)

    mock_visualization_model.start_chat.assert_called_once()
    expected_prompt_part = VISUALIZATION_PROMPT + "\n\nAlgorithm Solution Context:\nDFS algorithm\n\nUser Request: visualize graph"
    mock_chat_session.send_message_async.assert_called_once_with(expected_prompt_part)
    assert result == {"type": "graph", "nodes": [{"id": "A"}]}

@pytest.mark.asyncio
async def test_get_contextual_visualization_data_with_chat_history(mock_generative_models):
    mock_visualization_model, _ = mock_generative_models
    mock_chat_session = AsyncMock()
    mock_visualization_model.start_chat.return_value = mock_chat_session
    mock_chat_session.send_message_async.return_value.text = '```json\n{\"type\": \"array\", \"data\": [5, 4, 3]}\n```'

    user_query = "visualize sorting"
    chat_history = [
        {"role": "user", "content": "Explain bubble sort"},
        {"role": "model", "content": "Bubble sort is a simple sorting algorithm..."},
        {"role": "user", "content": "Show me an example with [5,4,3]"},
    ]
    algorithm_context = None

    result = await get_contextual_visualization_data(user_query, chat_history, algorithm_context)

    mock_visualization_model.start_chat.assert_called_once()
    expected_recent_context = (
        "Recent Conversation Context:\n"
        "user: Explain bubble sort\n"
        "model: Bubble sort is a simple sorting algorithm...\n"
        "user: Show me an example with [5,4,3]\n"
    )
    expected_prompt_part = VISUALIZATION_PROMPT + "\n\n" + expected_recent_context + "User Request: visualize sorting"
    mock_chat_session.send_message_async.assert_called_once_with(expected_prompt_part)
    assert result == {"type": "array", "data": [5, 4, 3]}

@pytest.mark.asyncio
async def test_get_contextual_visualization_data_invalid_json(mock_generative_models):
    mock_visualization_model, _ = mock_generative_models
    mock_chat_session = AsyncMock()
    mock_visualization_model.start_chat.return_value = mock_chat_session
    mock_chat_session.send_message_async.return_value.text = "Invalid JSON"

    user_query = "visualize this"
    result = await get_contextual_visualization_data(user_query)

    assert result is None

@pytest.mark.asyncio
async def test_get_contextual_visualization_data_exception(mock_generative_models):
    mock_visualization_model, _ = mock_generative_models
    mock_visualization_model.start_chat.side_effect = Exception("Contextual API error")

    user_query = "visualize this"
    result = await get_contextual_visualization_data(user_query)

    assert result is None
