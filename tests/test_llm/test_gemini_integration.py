"""The Gemini wrapper, with the client mocked out."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.llm import gemini_integration
from app.llm.gemini_integration import GenerationError, generate_scene, stream_chat_response
from app.schemas.visualization_schemas import SceneResult, TraceRequest

CONTEXT = "Question: two sum\n\nAnswer: use a hash map of complements."
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


# --- Scenes ---------------------------------------------------------------------


def feasible(scene=VALID_SCENE):
    return json.dumps({"feasible": True, "reason": "", "scene": scene})


@pytest.mark.asyncio
async def test_a_scene_is_generated_from_the_conversation(mock_genai_client):
    mock_genai_client.aio.models.generate_content.return_value = response(feasible())

    result = await generate_scene(CONTEXT, language="java")

    assert isinstance(result, SceneResult) and result.feasible
    kwargs = mock_genai_client.aio.models.generate_content.call_args.kwargs
    assert kwargs["config"].response_schema is SceneResult
    assert "java" in kwargs["config"].system_instruction
    assert CONTEXT in kwargs["contents"][0].parts[0].text


@pytest.mark.asyncio
async def test_traces_use_the_scene_model_not_the_answer_model(mock_genai_client):
    """Generated only on click, so it can afford the stronger model."""
    mock_genai_client.aio.models.generate_content.return_value = response(feasible())
    await generate_scene(CONTEXT)
    used = mock_genai_client.aio.models.generate_content.call_args.kwargs["model"]
    assert used == gemini_integration.SCENE_MODEL
    assert used == "gemini-2.5-flash"


@pytest.mark.asyncio
async def test_a_saved_specific_trace_is_still_honoured(mock_genai_client):
    mock_genai_client.aio.models.generate_content.return_value = response(feasible())
    await generate_scene(CONTEXT, trace=TRACE)
    text = mock_genai_client.aio.models.generate_content.call_args.kwargs["contents"][0].parts[0].text
    assert "nums=[2,7], target=9" in text


@pytest.mark.asyncio
async def test_declining_is_a_normal_result(mock_genai_client):
    mock_genai_client.aio.models.generate_content.return_value = response(
        json.dumps({"feasible": False, "reason": "This is a proof, not a process."})
    )
    result = await generate_scene(CONTEXT)
    assert not result.feasible and result.reason == "This is a proof, not a process."
    assert mock_genai_client.aio.models.generate_content.await_count == 1


@pytest.mark.asyncio
async def test_repairable_slips_are_fixed_without_a_retry(mock_genai_client):
    """An out-of-range code line used to fail the whole scene."""
    sloppy = dict(VALID_SCENE, steps=[dict(VALID_SCENE["steps"][0], code_line=40)])
    mock_genai_client.aio.models.generate_content.return_value = response(feasible(sloppy))

    result = await generate_scene(CONTEXT)

    assert result.scene.steps[0].code_line is None
    assert mock_genai_client.aio.models.generate_content.await_count == 1


@pytest.mark.asyncio
async def test_an_unrepairable_result_is_retried_with_the_error(mock_genai_client):
    blank = dict(VALID_SCENE, steps=[dict(VALID_SCENE["steps"][0], explanation="  ")])
    mock_genai_client.aio.models.generate_content.side_effect = [
        response(feasible(blank)),
        response(feasible()),
    ]

    result = await generate_scene(CONTEXT)

    assert result.feasible
    retry = mock_genai_client.aio.models.generate_content.call_args.kwargs["contents"]
    assert [c.role for c in retry] == ["user", "model", "user"]
    assert "no explanation" in retry[-1].parts[0].text


@pytest.mark.asyncio
async def test_two_unusable_results_give_up_with_a_readable_error(mock_genai_client):
    mock_genai_client.aio.models.generate_content.return_value = response("{not json")

    with pytest.raises(GenerationError, match="valid visualization"):
        await generate_scene(CONTEXT)
    assert mock_genai_client.aio.models.generate_content.await_count == 2


@pytest.mark.asyncio
async def test_an_unavailable_model_is_reported(mock_genai_client):
    mock_genai_client.aio.models.generate_content.side_effect = RuntimeError("down")

    with pytest.raises(GenerationError, match="unavailable"):
        await generate_scene(CONTEXT)
