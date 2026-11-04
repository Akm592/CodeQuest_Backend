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
def no_retry_pause():
    """Retries back off with real sleeps; tests shouldn't wait for them."""
    with patch("app.llm.gemini_integration.asyncio.sleep", AsyncMock()) as sleep:
        yield sleep


@pytest.fixture(autouse=True)
def mock_genai_client(monkeypatch):
    monkeypatch.setattr(gemini_integration, "_discovered", None)
    with patch("app.llm.gemini_integration.client") as mock_client:
        mock_client.aio.models.generate_content = AsyncMock()
        # By default the key lists no models, so discovery adds nothing.
        mock_client.aio.models.list = AsyncMock(return_value=pager([]))
        yield mock_client


def model(name, actions=("generateContent",)):
    m = MagicMock()
    m.name = f"models/{name}"
    m.supported_actions = list(actions)
    return m


def pager(models):
    async def it():
        for m in models:
            yield m
    return it()


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
    # 2.5-flash is refused to new keys (404 "no longer available to new users").
    assert used == "gemini-3.8-flash"


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


# --- Fallbacks when a way of asking is refused -------------------------------
#
# Production showed "The visualization service is unavailable right now" on
# every click: the API refused the structured-output request outright. These
# pin down the fallbacks that replaced giving up.


@pytest.mark.asyncio
async def test_a_refused_schema_falls_back_to_plain_json(mock_genai_client):
    mock_genai_client.aio.models.generate_content.side_effect = [
        RuntimeError("400 INVALID_ARGUMENT: schema produces too many states"),
        response(feasible()),
    ]

    result = await generate_scene(CONTEXT)

    assert result.feasible
    second = mock_genai_client.aio.models.generate_content.call_args_list[1].kwargs
    assert second["model"] == gemini_integration.SCENE_MODEL
    assert second["config"].response_schema is None
    assert "JSON Schema" in second["config"].system_instruction


@pytest.mark.asyncio
async def test_a_missing_scene_model_goes_straight_to_the_answer_model(mock_genai_client):
    """Skip a model that 404s.

    A 404 means the model doesn't exist for this key; asking it again in JSON
    mode (as the first fallback did) only adds a wasted round trip.
    """
    mock_genai_client.aio.models.generate_content.side_effect = [
        RuntimeError("404 NOT_FOUND. models/gemini-2.5-flash is no longer available to new users"),
        response(feasible()),
    ]

    result = await generate_scene(CONTEXT)

    assert result.feasible
    calls = mock_genai_client.aio.models.generate_content.call_args_list
    assert len(calls) == 2
    assert calls[1].kwargs["model"] == gemini_integration.DEFAULT_MODEL


@pytest.mark.asyncio
async def test_only_when_every_way_is_refused_is_it_unavailable(mock_genai_client):
    mock_genai_client.aio.models.generate_content.side_effect = RuntimeError("403")

    with pytest.raises(GenerationError, match="unavailable"):
        await generate_scene(CONTEXT)
    assert mock_genai_client.aio.models.generate_content.await_count == 3


@pytest.mark.asyncio
async def test_plain_json_wrapped_in_a_code_fence_is_accepted(mock_genai_client):
    mock_genai_client.aio.models.generate_content.side_effect = [
        RuntimeError("400 schema"),
        response("```json\n" + feasible() + "\n```"),
    ]
    assert (await generate_scene(CONTEXT)).feasible


def test_the_fallback_order_has_no_duplicates_when_models_match(monkeypatch):
    monkeypatch.setattr(gemini_integration, "SCENE_MODEL", gemini_integration.DEFAULT_MODEL)
    attempts = gemini_integration._scene_attempts()
    assert attempts == [(gemini_integration.DEFAULT_MODEL, True), (gemini_integration.DEFAULT_MODEL, False)]


# --- Transient failures (429/5xx) are retried ---------------------------------


def overloaded():
    return RuntimeError("503 UNAVAILABLE. This model is currently experiencing high demand.")


@pytest.mark.asyncio
async def test_an_overloaded_model_is_retried_before_falling_back(mock_genai_client, no_retry_pause):
    mock_genai_client.aio.models.generate_content.side_effect = [overloaded(), response(feasible())]

    result = await generate_scene(CONTEXT)

    assert result.feasible
    calls = mock_genai_client.aio.models.generate_content.call_args_list
    assert [c.kwargs["model"] for c in calls] == [gemini_integration.SCENE_MODEL] * 2
    assert calls[1].kwargs["config"].response_schema is SceneResult  # same mode, not a fallback
    no_retry_pause.assert_awaited_once_with(1.0)


@pytest.mark.asyncio
async def test_retries_are_bounded(mock_genai_client, no_retry_pause):
    mock_genai_client.aio.models.generate_content.side_effect = overloaded()

    with pytest.raises(GenerationError, match="unavailable"):
        await generate_scene(CONTEXT)

    # Three ways of asking, each tried three times (two retries).
    assert mock_genai_client.aio.models.generate_content.await_count == 9
    assert [c.args[0] for c in no_retry_pause.await_args_list[:2]] == [1.0, 3.0]


@pytest.mark.asyncio
async def test_a_permanent_error_is_not_retried(mock_genai_client, no_retry_pause):
    mock_genai_client.aio.models.generate_content.side_effect = [
        RuntimeError("400 INVALID_ARGUMENT"),
        response(feasible()),
    ]
    await generate_scene(CONTEXT)
    no_retry_pause.assert_not_awaited()


@pytest.mark.asyncio
async def test_the_answer_stream_is_retried_when_it_fails_before_any_text(mock_genai_client):
    async def chunks():
        yield MagicMock(text="hello")

    mock_genai_client.aio.models.generate_content_stream = AsyncMock(side_effect=[overloaded(), chunks()])

    out = [c async for c in stream_chat_response("q", "sys", [])]

    assert out == ["hello"]


@pytest.mark.asyncio
async def test_the_answer_stream_is_not_retried_after_text_was_sent(mock_genai_client):
    """A retry then would repeat text the user already has."""
    async def breaks_midway():
        yield MagicMock(text="half an ")
        raise overloaded()

    mock_genai_client.aio.models.generate_content_stream = AsyncMock(return_value=breaks_midway())

    out = []
    with pytest.raises(GenerationError):
        async for c in stream_chat_response("q", "sys", []):
            out.append(c)

    assert out == ["half an "]
    assert mock_genai_client.aio.models.generate_content_stream.await_count == 1


@pytest.mark.asyncio
async def test_intent_classification_survives_one_overload(mock_genai_client):
    gemini_integration._intent_cache.clear()
    mock_genai_client.aio.models.generate_content.side_effect = [overloaded(), response("cs_tutor")]

    assert await gemini_integration.classify_intent_with_llm("explain two sum") == "cs_tutor"


# --- Discovering models when the configured ones are gone ----------------------


@pytest.mark.asyncio
async def test_when_configured_models_are_refused_a_listed_model_is_used(mock_genai_client):
    """Model names get retired; the key's own model list is the last resort."""
    mock_genai_client.aio.models.list.return_value = pager([
        model("gemini-2.5-flash-lite"),
        model("gemini-9.1-flash"),
        model("gemini-9.1-flash-tts"),
        model("text-embedding-004", actions=("embedContent",)),
        model("gemini-9.1-pro"),
    ])
    mock_genai_client.aio.models.generate_content.side_effect = [
        RuntimeError("404 retired"),  # scene model
        RuntimeError("404 retired"),  # answer model
        response(feasible()),
    ]

    result = await generate_scene(CONTEXT)

    assert result.feasible
    used = [c.kwargs["model"] for c in mock_genai_client.aio.models.generate_content.call_args_list]
    assert used[-1] == "gemini-9.1-flash"


def test_discovered_models_are_newest_first_and_skip_lite():
    names = ["gemini-2.0-flash", "gemini-3.8-flash-lite", "gemini-3.8-flash", "gemini-3.8-flash-preview"]
    assert sorted(names, key=gemini_integration._version_key) == [
        "gemini-3.8-flash", "gemini-3.8-flash-preview", "gemini-3.8-flash-lite", "gemini-2.0-flash",
    ]


@pytest.mark.asyncio
async def test_a_failed_model_listing_still_reports_unavailable(mock_genai_client):
    mock_genai_client.aio.models.list.side_effect = RuntimeError("403")
    mock_genai_client.aio.models.generate_content.side_effect = RuntimeError("404 gone")
    with pytest.raises(GenerationError, match="unavailable"):
        await generate_scene(CONTEXT)


@pytest.mark.asyncio
async def test_overload_is_named_as_the_cause(mock_genai_client):
    mock_genai_client.aio.models.generate_content.side_effect = RuntimeError("503 high demand")
    with pytest.raises(GenerationError, match="overloaded"):
        await generate_scene(CONTEXT)
