"""End-to-end behaviour of the LeetCode chat flow.

These drive the real app through TestClient with only the network edges (Gemini
and the scraper) mocked, so they cover the request/response contract the
frontend actually depends on.
"""

import json
from unittest.mock import AsyncMock, patch

import pytest

from app.api.chat import chat_memory

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

    with patch(
        "app.api.chat.gemini_integration.classify_intent_with_llm",
        AsyncMock(return_value="cs_tutor"),
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
