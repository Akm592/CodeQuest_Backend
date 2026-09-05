"""LeetCode scraper.

The previous version of this file reset the module cache by rebinding its own
imported copy of ``_problems_cache``, which left the real module-level cache
untouched. Tests therefore polluted each other in call order and several
"failures" were just a warm cache. Here the cache is reset on the module object.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.scrapers import leetcode_scraper
from app.scrapers.leetcode_scraper import (
    extract_examples_from_content,
    fetch_leetcode_question,
    get_title_slug,
    normalize_text,
    parse_input_data,
    parse_output_data,
    scrape_leetcode_question,
)

PROBLEMS = [
    {
        "stat": {
            "frontend_question_id": 1,
            "question__title": "Two Sum",
            "question__title_slug": "two-sum",
        }
    },
    {
        "stat": {
            "frontend_question_id": 217,
            "question__title": "Contains Duplicate",
            "question__title_slug": "contains-duplicate",
        }
    },
]

# Shaped like real LeetCode content: an "Example N:" heading followed by a
# <pre> block. The scraper flattens this with a space separator before parsing.
QUESTION_HTML = (
    "<p>Given an array, return indices.</p>"
    "<p><strong class=\"example\">Example 1:</strong></p>"
    "<pre><strong>Input:</strong> nums = [2,7,11,15], target = 9\n"
    "<strong>Output:</strong> [0,1]\n"
    "<strong>Explanation:</strong> Because nums[0] + nums[1] == 9.</pre>"
)


@pytest.fixture(autouse=True)
def reset_problem_cache():
    """Clear the real module-level cache around every test."""
    leetcode_scraper._problems_cache = None
    yield
    leetcode_scraper._problems_cache = None


@pytest.fixture
def http_client():
    """Patch httpx.AsyncClient and hand back the mocked instance."""
    with patch("httpx.AsyncClient") as mock_cls:
        instance = AsyncMock()
        mock_cls.return_value.__aenter__.return_value = instance
        yield instance


def _json_response(payload):
    response = MagicMock()
    response.json.return_value = payload
    response.raise_for_status = MagicMock()
    return response


# --- Problem list ----------------------------------------------------------


async def test_fetch_all_problems_caches_the_result(http_client):
    http_client.get.return_value = _json_response({"stat_status_pairs": PROBLEMS})

    first = await leetcode_scraper._fetch_all_problems()
    second = await leetcode_scraper._fetch_all_problems()

    assert first == PROBLEMS
    assert second == PROBLEMS
    # The cache means only one network call, which is the point of it.
    assert http_client.get.await_count == 1


async def test_fetch_all_problems_handles_an_http_error(http_client):
    http_client.get.side_effect = httpx.HTTPStatusError(
        "boom", request=MagicMock(), response=MagicMock(status_code=500)
    )
    assert await leetcode_scraper._fetch_all_problems() is None


async def test_fetch_all_problems_handles_a_connection_error(http_client):
    http_client.get.side_effect = httpx.RequestError("no route")
    assert await leetcode_scraper._fetch_all_problems() is None


async def test_fetch_all_problems_handles_a_malformed_payload(http_client):
    http_client.get.return_value = _json_response({"unexpected": []})
    assert await leetcode_scraper._fetch_all_problems() is None


# --- Identifier resolution -------------------------------------------------


async def test_a_url_resolves_without_fetching_the_problem_list(http_client):
    slug = await get_title_slug("https://leetcode.com/problems/two-sum/")
    assert slug == "two-sum"
    http_client.get.assert_not_awaited()


async def test_a_url_with_query_parameters_resolves():
    assert await get_title_slug("https://leetcode.com/problems/two-sum/?envType=list") == "two-sum"


async def test_a_number_resolves(http_client):
    http_client.get.return_value = _json_response({"stat_status_pairs": PROBLEMS})
    assert await get_title_slug("1") == "two-sum"


async def test_a_number_and_title_resolves(http_client):
    http_client.get.return_value = _json_response({"stat_status_pairs": PROBLEMS})
    assert await get_title_slug("1. Two Sum") == "two-sum"


async def test_a_title_resolves(http_client):
    http_client.get.return_value = _json_response({"stat_status_pairs": PROBLEMS})
    assert await get_title_slug("Contains Duplicate") == "contains-duplicate"


async def test_an_unknown_identifier_resolves_to_nothing(http_client):
    http_client.get.return_value = _json_response({"stat_status_pairs": PROBLEMS})
    assert await get_title_slug("this problem does not exist anywhere") is None


@pytest.mark.parametrize("bad", [None, "", 123, []])
async def test_a_non_string_identifier_is_rejected(bad):
    assert await get_title_slug(bad) is None


# --- Question fetch --------------------------------------------------------


async def test_fetch_returns_structured_data(http_client):
    http_client.post.return_value = _json_response(
        {
            "data": {
                "question": {
                    "questionFrontendId": "1",
                    "title": "Two Sum",
                    "content": QUESTION_HTML,
                    "difficulty": "Easy",
                    "topicTags": [{"name": "Array"}, {"name": "Hash Table"}],
                }
            }
        }
    )

    result = await fetch_leetcode_question("two-sum")

    assert result is not None
    assert result["id"] == "1"
    assert result["title"] == "Two Sum"
    assert result["difficulty"] == "Easy"
    assert result["tags"] == ["Array", "Hash Table"]
    # The slug round-trips so a later turn can re-resolve the problem without
    # relying on in-process state surviving.
    assert result["slug"] == "two-sum"
    assert result["formatted_content"].startswith("ID: 1\nTitle: Two Sum")


async def test_fetch_extracts_examples(http_client):
    http_client.post.return_value = _json_response(
        {
            "data": {
                "question": {
                    "questionFrontendId": "1",
                    "title": "Two Sum",
                    "content": QUESTION_HTML,
                    "difficulty": "Easy",
                    "topicTags": [],
                }
            }
        }
    )

    result = await fetch_leetcode_question("two-sum")
    assert result["examples"]
    assert result["examples"][0]["input"]["variables"]["nums"] == [2, 7, 11, 15]


async def test_fetch_returns_none_on_a_graphql_error(http_client):
    http_client.post.return_value = _json_response({"errors": [{"message": "not found"}]})
    assert await fetch_leetcode_question("nope") is None


async def test_fetch_returns_none_on_an_http_error(http_client):
    http_client.post.side_effect = httpx.HTTPStatusError(
        "boom", request=MagicMock(), response=MagicMock(status_code=403)
    )
    assert await fetch_leetcode_question("two-sum") is None


async def test_fetch_returns_none_on_a_connection_error(http_client):
    http_client.post.side_effect = httpx.RequestError("no route")
    assert await fetch_leetcode_question("two-sum") is None


async def test_scrape_returns_none_when_the_identifier_cannot_be_resolved(http_client):
    http_client.get.return_value = _json_response({"stat_status_pairs": PROBLEMS})
    assert await scrape_leetcode_question("nonsense that matches nothing") is None


# --- Parsing helpers -------------------------------------------------------


def test_normalize_text():
    assert normalize_text("  Two   Sum  ") == "two sum"
    assert normalize_text("Café") == "cafe"
    assert normalize_text(None) == ""
    assert normalize_text(123) == ""


def test_examples_are_extracted_from_cleaned_text():
    """The pipeline only ever feeds this function BeautifulSoup-cleaned text."""
    cleaned = (
        "Example 1: Input: nums = [2,7,11,15], target = 9 Output: [0,1] "
        "Explanation: Because nums[0] + nums[1] == 9."
    )
    examples = extract_examples_from_content(cleaned)
    assert examples
    assert examples[0]["input"]["variables"]["nums"] == [2, 7, 11, 15]
    assert examples[0]["output"]["value"] == [0, 1]


def test_no_examples_in_plain_prose():
    assert extract_examples_from_content("This problem has no worked examples.") == []


def test_parse_input_data_captures_every_variable():
    """A consuming delimiter meant only the first variable was ever returned."""
    parsed = parse_input_data("nums = [2,7,11,15], target = 9")
    assert parsed["variables"] == {"nums": [2, 7, 11, 15], "target": 9}


def test_parse_input_data_handles_nested_and_string_values():
    assert parse_input_data("grid = [[1,1],[0,1]]")["variables"] == {"grid": [[1, 1], [0, 1]]}
    assert parse_input_data('s = "abc", t = "def"')["variables"] == {"s": "abc", "t": "def"}


def test_parse_input_data_keeps_the_raw_text():
    assert parse_input_data("x = 5")["raw"] == "x = 5"


def test_parse_output_data():
    assert parse_output_data("[0,1]") == {"raw": "[0,1]", "value": [0, 1]}
    assert parse_output_data("Output: [0,1]") == {"raw": "Output: [0,1]", "value": [0, 1]}
    assert parse_output_data("true")["value"] is True
    assert parse_output_data("-123")["value"] == -123
