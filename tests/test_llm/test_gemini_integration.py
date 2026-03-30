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
from google.genai import types

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
def mock_genai_client():
    with patch('app.llm.gemini_integration.client') as mock_client:
        yield mock_client

@pytest.mark.asyncio
async def test_get_visualization_data_success(mock_genai_client):
    mock_chat_session = AsyncMock()
    mock_genai_client.aio.chats.create.return_value = mock_chat_session
    mock_chat_session.send_message.return_value.text = '```json\n{\"type\": \"array\", \"data\": [1, 2, 3]}\n```'

    user_query = "visualize array [1,2,3]"
    result = await get_visualization_data(user_query)

    mock_genai_client.aio.chats.create.assert_called_once()
    mock_chat_session.send_message.assert_called_once_with(
        VISUALIZATION_PROMPT + "\n\n" + user_query
    )
    assert result == {"type": "array", "data": [1, 2, 3]}

@pytest.mark.asyncio
@pytest.mark.asyncio
async def test_get_visualization_data_invalid_json(mock_genai_client):
    mock_chat_session = AsyncMock()
    mock_genai_client.aio.chats.create.return_value = mock_chat_session
    mock_chat_session.send_message.return_value.text = "Invalid JSON response"

    user_query = "visualize something"
    result = await get_visualization_data(user_query)

    assert result is None

@pytest.mark.asyncio
@pytest.mark.asyncio
async def test_get_visualization_data_exception(mock_genai_client):
    mock_genai_client.aio.chats.create.side_effect = Exception("API error")

    user_query = "visualize something"
    result = await get_visualization_data(user_query)

    assert result is None

@pytest.mark.asyncio
@pytest.mark.asyncio
async def test_get_chat_response_success(mock_genai_client):
    mock_chat_session = AsyncMock()
    mock_genai_client.aio.chats.create.return_value = mock_chat_session
    mock_chat_session.send_message.return_value.text = "Hello from bot"

    user_query = "Hi"
    system_prompt = "You are a helpful assistant."
    chat_history = []

    result = await get_chat_response(user_query, system_prompt, chat_history)

    mock_genai_client.aio.chats.create.assert_called_once()
    mock_chat_session.send_message.assert_any_call(system_prompt)
    mock_chat_session.send_message.assert_called_with(user_query)
    assert result == "Hello from bot"

@pytest.mark.asyncio
@pytest.mark.asyncio
async def test_get_chat_response_with_history(mock_genai_client):
    mock_chat_session = AsyncMock()
    mock_genai_client.aio.chats.create.return_value = mock_chat_session
    mock_chat_session.send_message.return_value.text = "Bot response with history"

    user_query = "What is Python?"
    system_prompt = "You are a helpful assistant."
    chat_history = [
        {"role": "user", "content": "Hello"},
        {"role": "model", "content": "Hi there!"},
    ]

    result = await get_chat_response(user_query, system_prompt, chat_history)

    mock_genai_client.aio.chats.create.assert_called_once()
    # Check if history was passed in create call
    _, kwargs = mock_genai_client.aio.chats.create.call_args
    assert len(kwargs['history']) == 2
    assert kwargs['history'][0].role == 'user'
    assert kwargs['history'][0].parts[0].text == 'Hello'
    
    mock_chat_session.send_message.assert_any_call(system_prompt)
    mock_chat_session.send_message.assert_any_call(user_query)
    assert result == "Bot response with history"

@pytest.mark.asyncio
@pytest.mark.asyncio
async def test_stream_chat_response_success(mock_genai_client):
    # Mock the behavior: await generate_content_stream returns an async iterable
    async def mock_iter():
        yield MagicMock(text="chunk1")
        yield MagicMock(text="chunk2")
        yield MagicMock(text="chunk3")

    # Set as return_value of the AsyncMock. 
    # Calling the mock returns a coroutine (because it's AsyncMock child of MagicMock?) 
    # actually mock_genai_client.aio... is likely a MagicMock unless we specify.
    # Let's forcibly make generate_content_stream an AsyncMock.
    mock_genai_client.aio.models.generate_content_stream = AsyncMock(return_value=mock_iter())

    user_query = "Tell me a story"
    system_prompt = "You are a storyteller."
    chat_history = []

    chunks = [chunk async for chunk in stream_chat_response(user_query, system_prompt, chat_history)]

    assert chunks == ["chunk1", "chunk2", "chunk3"]
    mock_genai_client.aio.models.generate_content_stream.assert_called_once()
    _, kwargs = mock_genai_client.aio.models.generate_content_stream.call_args
    # Verify contents structure
    contents = kwargs['contents']
    assert len(contents) == 2 
    assert contents[0].role == 'user'
    assert contents[0].parts[0].text == system_prompt
    assert contents[1].role == 'user'
    assert contents[1].parts[0].text == user_query

@pytest.mark.asyncio
@pytest.mark.asyncio
async def test_stream_chat_response_with_history(mock_genai_client):
    async def mock_iter():
        yield MagicMock(text="history_chunk1")
        yield MagicMock(text="history_chunk2")

    mock_genai_client.aio.models.generate_content_stream = AsyncMock(return_value=mock_iter())

    user_query = "Continue the story"
    system_prompt = "You are a storyteller."
    chat_history = [
        {"role": "user", "content": "Once upon a time"},
        {"role": "model", "content": "there was a brave knight"},
    ]

    chunks = [chunk async for chunk in stream_chat_response(user_query, system_prompt, chat_history)]

    assert chunks == ["history_chunk1", "history_chunk2"]
    _, kwargs = mock_genai_client.aio.models.generate_content_stream.call_args
    contents = kwargs['contents']
    assert len(contents) == 4
    assert contents[0].parts[0].text == system_prompt
    assert contents[1].parts[0].text == "Once upon a time"
    assert contents[2].role == "model"
    assert contents[2].parts[0].text == "there was a brave knight"
    assert contents[3].parts[0].text == user_query

@pytest.mark.asyncio
async def test_get_contextual_visualization_data_success(mock_genai_client):
    mock_chat_session = AsyncMock()
    mock_genai_client.aio.chats.create.return_value = mock_chat_session
    mock_chat_session.send_message.return_value.text = '```json\n{\"type\": \"graph\", \"nodes\": [{\"id\": \"A\"}]}\n```'

    user_query = "visualize graph"
    chat_history = []
    algorithm_context = "DFS algorithm"

    print("Calling get_contextual_visualization_data...")
    result = await get_contextual_visualization_data(user_query, chat_history, algorithm_context)
    print(f"Result: {result}")

    print(f"Client calls: {mock_genai_client.mock_calls}")
    print(f"Chat calls: {mock_chat_session.mock_calls}")

    mock_genai_client.aio.chats.create.assert_called_once()
    # Note the extra newline due to how context_prompt is constructed in the app
    expected_prompt_part = VISUALIZATION_PROMPT + "\n\nAlgorithm Solution Context:\nDFS algorithm\n\n\nUser Request: visualize graph"
    mock_chat_session.send_message.assert_called_once_with(expected_prompt_part)
    assert result == {"type": "graph", "nodes": [{"id": "A"}]}

@pytest.mark.asyncio
@pytest.mark.asyncio
async def test_get_contextual_visualization_data_with_chat_history(mock_genai_client):
    mock_chat_session = AsyncMock()
    mock_genai_client.aio.chats.create.return_value = mock_chat_session
    mock_chat_session.send_message.return_value.text = '```json\n{\"type\": \"array\", \"data\": [5, 4, 3]}\n```'

    user_query = "visualize sorting"
    chat_history = [
        {"role": "user", "content": "Explain bubble sort"},
        {"role": "model", "content": "Bubble sort is a simple sorting algorithm..."},
        {"role": "user", "content": "Show me an example with [5,4,3]"},
    ]
    algorithm_context = None

    result = await get_contextual_visualization_data(user_query, chat_history, algorithm_context)

    mock_genai_client.aio.chats.create.assert_called_once()
    expected_recent_context = (
        "Recent Conversation Context:\n"
        "model: Bubble sort is a simple sorting algorithm......\n"
        "user: Show me an example with [5,4,3]...\n"
    )
    expected_prompt_part = VISUALIZATION_PROMPT + "\n\n" + expected_recent_context + "\n\nUser Request: visualize sorting"
    # mock_chat_session.send_message.assert_called_once_with(expected_prompt_part)
    mock_chat_session.send_message.assert_called_once()
    actual_prompt = mock_chat_session.send_message.call_args[0][0]
    assert actual_prompt == expected_prompt_part
    assert result == {"type": "array", "data": [5, 4, 3]}

@pytest.mark.asyncio
@pytest.mark.asyncio
async def test_get_contextual_visualization_data_invalid_json(mock_genai_client):
    mock_chat_session = AsyncMock()
    mock_genai_client.aio.chats.create.return_value = mock_chat_session
    mock_chat_session.send_message.return_value.text = "Invalid JSON"

    user_query = "visualize this"
    result = await get_contextual_visualization_data(user_query)

    assert result is None

@pytest.mark.asyncio
@pytest.mark.asyncio
async def test_get_contextual_visualization_data_exception(mock_genai_client):
    mock_genai_client.aio.chats.create.side_effect = Exception("Contextual API error")

    user_query = "visualize this"
    result = await get_contextual_visualization_data(user_query)

    assert result is None
