"""End-to-end behaviour of the LeetCode chat flow.

These drive the real app through TestClient with only the network edges (Gemini
and the scraper) mocked, so they cover the request/response contract the
frontend actually depends on.
"""

import json
from unittest.mock import AsyncMock, patch

import pytest

from app.api.chat import _SCENE_CACHE, chat_memory
from app.llm.gemini_integration import GenerationError
from app.schemas.visualization_schemas import Scene, Suggestion

SESSION = "aaaaaaaa-0000-0000-0000-000000000001"

SCRAPED = {
    "slug": "two-sum",
    "id": "1",
    "title": "Two Sum",
    "difficulty": "Easy",
    "tags": ["Array", "Hash Table"],
    "content": "Given an array of integers...",
    "examples": [
        {
            "example_number": 1,
            "input": {"raw": "nums = [2,7,11,15], target = 9", "variables": {}},
            "output": {"raw": "[0,1]", "value": [0, 1]},
            "explanation": None,
        }
    ],
    "formatted_content": (
        "ID: 1\nTitle: Two Sum\nDifficulty: Easy\n"
        "Topics: Array, Hash Table\n\nContent:\nGiven an array of integers..."
    ),
}


SUGGESTION = Suggestion.model_validate({
    "title": "Hash map lookup for [2,7,11,15]",
    "kind": "trace",
    "trace": {
        "problem": "Two Sum",
        "input": "nums = [2,7,11,15], target = 9",
        "approach": "one-pass hash map",
    },
})


def frames(response):
    """Parse the SSE body into a list of event payloads."""
    return [
        json.loads(line[len("data: ") :])
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]


@pytest.fixture
def leetcode_flow():
    """Mock the network edges and capture the prompt sent to the model."""
    captured = {}

    async def fake_stream(user_query, system_prompt, chat_history=None):
        captured["prompt"] = user_query
        for chunk in ["Here is ", "the solution."]:
            yield chunk

    suggest = AsyncMock(return_value=[SUGGESTION])
    captured["suggest"] = suggest

    with patch(
        "app.api.chat.gemini_integration.classify_intent_with_llm",
        AsyncMock(return_value="cs_tutor"),
    ), patch(
        "app.api.chat.gemini_integration.suggest_visualizations", suggest
    ), patch(
        "app.api.chat.scrape_leetcode_question", AsyncMock(return_value=SCRAPED)
    ), patch(
        "app.api.chat.fetch_leetcode_question", AsyncMock(return_value=SCRAPED)
    ), patch(
        "app.api.chat.gemini_integration.stream_chat_response", fake_stream
    ):
        yield captured


def test_the_first_turn_hands_pending_context_to_the_client(client, leetcode_flow):
    response = client.post(
        "/chat",
        json={"user_input": "two sum"},
        headers={"X-Session-ID": SESSION},
    )

    assert response.status_code == 200
    events = frames(response)
    pending = next((e["data"] for e in events if e["type"] == "pending"), None)
    assert pending == {"kind": "awaiting_language", "slug": "two-sum", "visualize": False}


def test_the_exchange_survives_the_instance_restarting(client, leetcode_flow):
    """The regression this flow was rebuilt for.

    On Render's free tier the instance spins down when idle, wiping in-process
    state. Holding the pending context server-side meant a user who answered
    "python" after a pause got "I seem to have lost the context" instead of a
    solution. Clearing chat_memory below simulates exactly that.
    """
    first = client.post(
        "/chat", json={"user_input": "two sum"}, headers={"X-Session-ID": SESSION}
    )
    pending = next(e["data"] for e in frames(first) if e["type"] == "pending")

    chat_memory.sessions.clear()  # the instance went to sleep here

    second = client.post(
        "/chat",
        json={"user_input": "python", "pending": pending},
        headers={"X-Session-ID": SESSION},
    )

    text = "".join(e.get("content", "") for e in frames(second) if e["type"] == "text")
    assert "solution" in text
    assert "lost track" not in text


def test_losing_the_context_entirely_degrades_gracefully(client, leetcode_flow):
    """With neither client context nor server state, ask again rather than break."""
    with patch("app.api.chat.fetch_leetcode_question", AsyncMock(return_value=None)):
        response = client.post(
            "/chat",
            json={
                "user_input": "python",
                "pending": {"kind": "awaiting_language", "slug": "two-sum", "visualize": False},
            },
            headers={"X-Session-ID": SESSION},
        )

    text = "".join(e.get("content", "") for e in frames(response) if e["type"] == "text")
    assert "lost track" in text


def test_a_preferred_language_skips_the_clarification_turn(client, leetcode_flow):
    response = client.post(
        "/chat",
        json={"user_input": "two sum", "preferred_language": "java"},
        headers={"X-Session-ID": SESSION},
    )

    events = frames(response)
    assert "pending" not in [e["type"] for e in events]
    assert "java" in leetcode_flow["prompt"].lower()


def test_a_language_named_in_the_message_skips_the_clarification_turn(client, leetcode_flow):
    response = client.post(
        "/chat",
        json={"user_input": "solve two sum in python"},
        headers={"X-Session-ID": SESSION},
    )

    assert "pending" not in [e["type"] for e in frames(response)]
    assert "python" in leetcode_flow["prompt"].lower()


def test_the_prompt_gets_clean_text_not_a_dict_repr(client, leetcode_flow):
    """The scraped dict used to be interpolated whole into the f-string."""
    client.post(
        "/chat",
        json={"user_input": "two sum", "preferred_language": "python"},
        headers={"X-Session-ID": SESSION},
    )

    prompt = leetcode_flow["prompt"]
    assert "ID: 1\nTitle: Two Sum" in prompt
    assert "{'id'" not in prompt
    assert "'formatted_content'" not in prompt


def test_the_prompt_includes_the_scraped_examples(client, leetcode_flow):
    """Structured examples are why the scraper returns a dict; nothing used them."""
    client.post(
        "/chat",
        json={"user_input": "two sum", "preferred_language": "python"},
        headers={"X-Session-ID": SESSION},
    )

    assert "nums = [2,7,11,15], target = 9" in leetcode_flow["prompt"]


def test_guest_messages_are_not_persisted(client, leetcode_flow):
    """Guests reach the model but never the database."""
    with patch("app.api.chat.SupabaseManager.for_user", AsyncMock()) as for_user:
        response = client.post(
            "/chat",
            json={"user_input": "two sum", "preferred_language": "python"},
            headers={"X-Session-ID": SESSION},
        )

    assert response.status_code == 200
    for_user.assert_not_awaited()


# --- Visualization suggestions ----------------------------------------------


def test_an_answer_is_followed_by_suggestions(client, leetcode_flow):
    response = client.post(
        "/chat",
        json={"user_input": "two sum", "preferred_language": "python"},
        headers={"X-Session-ID": SESSION},
    )

    events = frames(response)
    types = [e["type"] for e in events]
    # Suggestions come after the answer text, never before or instead of it.
    assert types[-1] == "suggestions"
    assert "text" in types[: types.index("suggestions")]
    suggestions = events[-1]
    assert suggestions["data"][0]["title"] == "Hash map lookup for [2,7,11,15]"
    assert suggestions["autoOpen"] is False

    question, answer = leetcode_flow["suggest"].await_args.args
    assert "Two Sum" in question
    assert answer == "Here is the solution."


def test_asking_to_visualize_opens_the_first_suggestion(client, leetcode_flow):
    with patch(
        "app.api.chat.gemini_integration.classify_intent_with_llm",
        AsyncMock(return_value="visualization"),
    ), patch("app.api.chat.scrape_leetcode_question", AsyncMock(return_value=None)):
        response = client.post(
            "/chat",
            json={"user_input": "visualize bubble sort on [5,1,4]"},
            headers={"X-Session-ID": SESSION},
        )

    events = frames(response)
    assert events[-1]["type"] == "suggestions"
    assert events[-1]["autoOpen"] is True
    # The answer is told a trace is coming, so it stays short.
    assert "visualization" in leetcode_flow["prompt"].lower()


def test_no_suggestions_means_no_event(client, leetcode_flow):
    leetcode_flow["suggest"].return_value = []
    response = client.post(
        "/chat",
        json={"user_input": "two sum", "preferred_language": "python"},
        headers={"X-Session-ID": SESSION},
    )
    assert "suggestions" not in [e["type"] for e in frames(response)]


def test_general_chit_chat_is_not_offered_visualizations(client, leetcode_flow):
    with patch(
        "app.api.chat.gemini_integration.classify_intent_with_llm",
        AsyncMock(return_value="general"),
    ), patch("app.api.chat.scrape_leetcode_question", AsyncMock(return_value=None)):
        response = client.post(
            "/chat", json={"user_input": "hello there"}, headers={"X-Session-ID": SESSION}
        )

    assert "suggestions" not in [e["type"] for e in frames(response)]
    leetcode_flow["suggest"].assert_not_awaited()


def test_suggestions_are_stored_with_the_signed_in_users_message(
    client, leetcode_flow, as_user, alice
):
    """So the chips come back when the conversation is reopened."""
    as_user(alice)
    db = AsyncMock()
    db.ensure_session_owned.return_value = True
    db.get_messages_by_session_id.return_value = []
    with patch("app.api.chat.SupabaseManager.for_user", AsyncMock(return_value=db)):
        client.post(
            "/chat",
            json={"user_input": "two sum", "preferred_language": "python"},
            headers={"X-Session-ID": SESSION},
        )

    bot_rows = [c.kwargs for c in db.store_message.await_args_list if c.kwargs["sender_type"] == "bot"]
    assert bot_rows[-1]["content"] == "Here is the solution."
    assert bot_rows[-1]["metadata"]["suggestions"][0]["kind"] == "trace"


# --- The "which language?" turn ----------------------------------------------


def test_moving_on_instead_of_naming_a_language_answers_the_new_question(
    client, leetcode_flow
):
    """The bot used to re-ask "which language?" for any message at all."""
    first = client.post("/chat", json={"user_input": "two sum"}, headers={"X-Session-ID": SESSION})
    pending = next(e["data"] for e in frames(first) if e["type"] == "pending")

    with patch(
        "app.api.chat.gemini_integration.classify_intent_with_llm",
        AsyncMock(return_value="general"),
    ), patch("app.api.chat.scrape_leetcode_question", AsyncMock(return_value=None)):
        second = client.post(
            "/chat",
            json={"user_input": "actually, what is recursion?", "pending": pending},
            headers={"X-Session-ID": SESSION},
        )

    text = "".join(e.get("content", "") for e in frames(second) if e["type"] == "text")
    assert "Which language" not in text
    assert leetcode_flow["prompt"].startswith("actually, what is recursion?")
    assert not chat_memory.get_session(SESSION).get_state("awaiting_language")


# --- Failures ----------------------------------------------------------------


def test_a_model_failure_is_an_error_event_not_a_saved_answer(client, leetcode_flow):
    async def failing_stream(user_query, system_prompt, chat_history=None):
        raise GenerationError("down")
        yield  # pragma: no cover

    with patch("app.api.chat.gemini_integration.stream_chat_response", failing_stream):
        response = client.post(
            "/chat",
            json={"user_input": "two sum", "preferred_language": "python"},
            headers={"X-Session-ID": SESSION},
        )

    events = frames(response)
    assert events[-1]["type"] == "error"
    assert "try again" in events[-1]["content"]
    assert "text" not in [e["type"] for e in events]


# --- POST /chat/visualize ----------------------------------------------------

SCENE = Scene.model_validate({
    "title": "Two Sum",
    "summary": "Look up each complement before storing.",
    "structures": [{"id": "nums", "type": "array", "label": "nums"}],
    "steps": [{
        "title": "Look at 2",
        "explanation": "9 - 2 = 7 is not stored yet.",
        "states": [{"structure": "nums", "items": ["2", "7"]}],
    }],
})

VISUALIZE_BODY = {
    "trace": SUGGESTION.trace.model_dump(),
    "context": "Q: two sum\nA: ```java\nint[] twoSum(...)\n```",
}


@pytest.fixture
def scene_generator():
    _SCENE_CACHE.clear()
    generate = AsyncMock(return_value=SCENE)
    with patch("app.api.chat.gemini_integration.generate_scene", generate):
        yield generate
    _SCENE_CACHE.clear()


def test_visualize_returns_a_scene(client, scene_generator):
    response = client.post("/chat/visualize", json=VISUALIZE_BODY)

    assert response.status_code == 200
    assert response.json()["scene"]["steps"][0]["title"] == "Look at 2"
    trace, context, language = scene_generator.await_args.args
    assert trace.problem == "Two Sum"
    # The scene traces code in the language the conversation used.
    assert language == "java"


def test_visualize_caches_by_request(client, scene_generator):
    client.post("/chat/visualize", json=VISUALIZE_BODY)
    again = client.post("/chat/visualize", json=VISUALIZE_BODY)

    assert again.status_code == 200
    scene_generator.assert_awaited_once()


def test_visualize_reports_a_generation_failure_as_502(client, scene_generator):
    scene_generator.side_effect = GenerationError("Couldn't build a valid visualization (x).")

    response = client.post("/chat/visualize", json=VISUALIZE_BODY)

    assert response.status_code == 502
    assert "valid visualization" in response.json()["detail"]


def test_visualize_rejects_a_malformed_body(client, scene_generator):
    response = client.post("/chat/visualize", json={"trace": {"problem": "x"}})
    assert response.status_code == 422
    scene_generator.assert_not_awaited()
