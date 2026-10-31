"""The Gemini wrapper, with the client mocked out."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.llm import gemini_integration
from app.llm.gemini_integration import (
    GenerationError,
    generate_scene,
    stream_chat_response,
    suggest_visualizations,
)
from app.schemas.visualization_schemas import Scene, SuggestionList, TraceRequest

TRACE = TraceRequest(problem="Two Sum", input="nums=[2,7], target=9", approach="hash map")

VALID_SCENE = {
    "title": "Two Sum",
    "summary": "Look up each complement before storing the value.",
    "structures": [{"id": "nums", "type": "array", "label": "nums"}],
    "code": {"language": "python", "lines": ["for i, x in enumerate(nums):", "    ..."]},
    "steps": [{
        "title": "Look at 2",
        "explanation": "9 - 2 = 7 has not been seen, so store 2.",
        "code_line": 1,
        "states": [{"structure": "nums", "items": ["2", "7"]}],
    }],
}


@pytest.fixture(autouse=True)
def mock_genai_client():
    with patch("app.llm.gemini_integration.client") as mock_client:
        mock_client.aio.models.generate_content = AsyncMock()
        yield mock_client


def response(text="", parsed=None):
    r = MagicMock()
    r.text = text
    r.parsed = parsed
    return r


# --- Streaming answers -------------------------------------------------------


@pytest.mark.asyncio
async def test_the_system_prompt_is_a_system_instruction_not_a_user_turn(mock_genai_client):
    """It was sent as the first user message, which the model can argue with."""
    async def chunks():
        yield MagicMock(text="chunk1")
        yield MagicMock(text="chunk2")

    mock_genai_client.aio.models.generate_content_stream = AsyncMock(return_value=chunks())

    out = [c async for c in stream_chat_response("Tell me a story", "You are a storyteller.", [])]

    assert out == ["chunk1", "chunk2"]
    _, kwargs = mock_genai_client.aio.models.generate_content_stream.call_args
    assert [c.parts[0].text for c in kwargs["contents"]] == ["Tell me a story"]
    assert kwargs["config"].system_instruction == "You are a storyteller."


@pytest.mark.asyncio
async def test_history_precedes_the_query(mock_genai_client):
    async def chunks():
        yield MagicMock(text="ok")

    mock_genai_client.aio.models.generate_content_stream = AsyncMock(return_value=chunks())
    history = [
        {"role": "user", "content": "Once upon a time"},
        {"role": "model", "content": "there was a knight"},
    ]

    [c async for c in stream_chat_response("Continue", "sys", history)]

    contents = mock_genai_client.aio.models.generate_content_stream.call_args.kwargs["contents"]
    assert [c.role for c in contents] == ["user", "model", "user"]
    assert contents[-1].parts[0].text == "Continue"


@pytest.mark.asyncio
async def test_a_model_failure_raises_instead_of_becoming_the_answer(mock_genai_client):
    """It used to yield "Error generating response." which was saved as the reply."""
    mock_genai_client.aio.models.generate_content_stream = AsyncMock(side_effect=RuntimeError("503"))

    with pytest.raises(GenerationError):
        [c async for c in stream_chat_response("q", "sys", [])]


# --- Suggestions -------------------------------------------------------------


@pytest.mark.asyncio
async def test_suggestions_come_back_parsed(mock_genai_client):
    parsed = SuggestionList.model_validate({"suggestions": [{
        "title": "Hash map lookup for [2,7]", "kind": "trace",
        "trace": {"problem": "Two Sum", "input": "[2,7]", "approach": "hash map"},
    }]})
    mock_genai_client.aio.models.generate_content.return_value = response(parsed=parsed)

    result = await suggest_visualizations("two sum?", "Use a hash map.")

    assert [s.title for s in result] == ["Hash map lookup for [2,7]"]
    config = mock_genai_client.aio.models.generate_content.call_args.kwargs["config"]
    assert config.response_schema is SuggestionList


@pytest.mark.asyncio
async def test_suggestion_failures_never_raise(mock_genai_client):
    """Suggestions are an extra: a failure must not cost the user the answer."""
    mock_genai_client.aio.models.generate_content.side_effect = RuntimeError("quota")
    assert await suggest_visualizations("q", "a") == []


@pytest.mark.asyncio
async def test_unknown_builtin_keys_are_dropped_and_the_list_is_capped(mock_genai_client):
    trace = {"problem": "p", "input": "i", "approach": "a"}
    parsed = SuggestionList.model_validate({"suggestions": [
        {"title": "Made up", "kind": "builtin", "builtin": {"key": "teleporter"}},
        *[{"title": f"T{n}", "kind": "trace", "trace": trace} for n in range(4)],
    ]})
    mock_genai_client.aio.models.generate_content.return_value = response(parsed=parsed)

    result = await suggest_visualizations("q", "a")

    assert [s.title for s in result] == ["T0", "T1", "T2"]


# --- Scenes ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_valid_scene_is_returned(mock_genai_client):
    mock_genai_client.aio.models.generate_content.return_value = response(json.dumps(VALID_SCENE))

    scene = await generate_scene(TRACE, context="Q: two sum", language="java")

    assert isinstance(scene, Scene)
    kwargs = mock_genai_client.aio.models.generate_content.call_args.kwargs
    assert kwargs["config"].response_schema is Scene
    assert "java" in kwargs["config"].system_instruction
    assert "Q: two sum" in kwargs["contents"][0].parts[0].text


@pytest.mark.asyncio
async def test_an_invalid_scene_is_retried_with_the_error(mock_genai_client):
    bad = dict(VALID_SCENE, steps=[dict(VALID_SCENE["steps"][0], code_line=40)])
    mock_genai_client.aio.models.generate_content.side_effect = [
        response(json.dumps(bad)),
        response(json.dumps(VALID_SCENE)),
    ]

    scene = await generate_scene(TRACE)

    assert scene.steps[0].code_line == 1
    retry_contents = mock_genai_client.aio.models.generate_content.call_args.kwargs["contents"]
    assert [c.role for c in retry_contents] == ["user", "model", "user"]
    assert "code line 40" in retry_contents[-1].parts[0].text


@pytest.mark.asyncio
async def test_two_invalid_scenes_give_up_with_a_readable_error(mock_genai_client):
    mock_genai_client.aio.models.generate_content.return_value = response("{not json")

    with pytest.raises(GenerationError, match="valid visualization"):
        await generate_scene(TRACE)
    assert mock_genai_client.aio.models.generate_content.await_count == 2


@pytest.mark.asyncio
async def test_an_unavailable_model_is_reported(mock_genai_client):
    mock_genai_client.aio.models.generate_content.side_effect = RuntimeError("down")

    with pytest.raises(GenerationError, match="unavailable"):
        await generate_scene(TRACE)


def test_no_builtins_means_the_prompt_says_trace_only():
    assert "none available" in gemini_integration._builtins_text()
