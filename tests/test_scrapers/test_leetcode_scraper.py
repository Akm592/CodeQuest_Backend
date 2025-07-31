import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import httpx
from bs4 import BeautifulSoup
from app.scrapers.leetcode_scraper import (
    _fetch_all_problems,
    normalize_text,
    get_title_slug,
    fetch_leetcode_question,
    scrape_leetcode_question,
    extract_examples_from_content,
    parse_input_data,
    parse_output_data,
    LEETCODE_ALL_PROBLEMS_URL,
    LEETCODE_GRAPHQL_URL,
    _problems_cache # Import the global cache
)

# Fixture to mock httpx.AsyncClient and reset global cache
@pytest.fixture(autouse=True)
def mock_httpx_client():
    with patch('httpx.AsyncClient') as MockAsyncClient:
        mock_client_instance = AsyncMock()
        MockAsyncClient.return_value.__aenter__.return_value = mock_client_instance
        
        # Reset the global cache before each test
        global _problems_cache
        original_problems_cache = _problems_cache
        _problems_cache = None
        
        yield mock_client_instance
        
        _problems_cache = original_problems_cache

# Test _fetch_all_problems
@pytest.mark.asyncio
async def test_fetch_all_problems_success(mock_httpx_client):
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "stat_status_pairs": [
            {"stat": {"frontend_question_id": 1, "question__title": "Two Sum", "question__title_slug": "two-sum"}},
            {"stat": {"frontend_question_id": 2, "question__title": "Add Two Numbers", "question__title_slug": "add-two-numbers"}},
        ]
    }
    mock_httpx_client.get.return_value = mock_response

    problems = await _fetch_all_problems()
    assert len(problems) == 2
    assert problems[0]["stat"]["question__title_slug"] == "two-sum"
    mock_httpx_client.get.assert_called_once_with(LEETCODE_ALL_PROBLEMS_URL)

@pytest.mark.asyncio
async def test_fetch_all_problems_http_error(mock_httpx_client):
    mock_httpx_client.get.side_effect = httpx.HTTPStatusError("Not Found", request=httpx.Request("GET", LEETCODE_ALL_PROBLEMS_URL), response=httpx.Response(404))
    problems = await _fetch_all_problems()
    assert problems is None

@pytest.mark.asyncio
async def test_fetch_all_problems_request_error(mock_httpx_client):
    mock_httpx_client.get.side_effect = httpx.RequestError("Connection Error", request=httpx.Request("GET", LEETCODE_ALL_PROBLEMS_URL))
    problems = await _fetch_all_problems()
    assert problems is None

# Test normalize_text
def test_normalize_text():
    assert normalize_text("  Hello World!  ") == "hello world!"
    assert normalize_text("Déjà Vu") == "deja vu"
    assert normalize_text("Multiple   Spaces") == "multiple spaces"
    assert normalize_text(None) == ""

# Test get_title_slug
@pytest.mark.asyncio
async def test_get_title_slug_from_url(mock_httpx_client):
    slug = await get_title_slug("https://leetcode.com/problems/two-sum/description/")
    assert slug == "two-sum"
    mock_httpx_client.get.assert_not_called() # Should not call _fetch_all_problems

@pytest.mark.asyncio
async def test_get_title_slug_from_number(mock_httpx_client):
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "stat_status_pairs": [
            {"stat": {"frontend_question_id": 1, "question__title": "Two Sum", "question__title_slug": "two-sum"}},
        ]
    }
    mock_httpx_client.get.return_value = mock_response
    slug = await get_title_slug("1")
    assert slug == "two-sum"
    mock_httpx_client.get.assert_called_once_with(LEETCODE_ALL_PROBLEMS_URL)

@pytest.mark.asyncio
async def test_get_title_slug_from_title(mock_httpx_client):
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "stat_status_pairs": [
            {"stat": {"frontend_question_id": 1, "question__title": "Two Sum", "question__title_slug": "two-sum"}},
        ]
    }
    mock_httpx_client.get.return_value = mock_response
    slug = await get_title_slug("Two Sum")
    assert slug == "two-sum"
    mock_httpx_client.get.assert_called_once_with(LEETCODE_ALL_PROBLEMS_URL)

@pytest.mark.asyncio
async def test_get_title_slug_not_found(mock_httpx_client):
    mock_response = MagicMock()
    mock_response.json.return_value = {"stat_status_pairs": []}
    mock_httpx_client.get.return_value = mock_response
    slug = await get_title_slug("Non Existent Problem")
    assert slug is None

@pytest.mark.asyncio
async def test_get_title_slug_invalid_identifier():
    slug = await get_title_slug(123) # Invalid type
    assert slug is None

# Test fetch_leetcode_question
@pytest.mark.asyncio
async def test_fetch_leetcode_question_success(mock_httpx_client):
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "data": {
            "question": {
                "questionFrontendId": "1",
                "title": "Two Sum",
                "content": "<p>Given an array of integers, return indices of the two numbers...</p>",
                "difficulty": "Easy",
                "topicTags": [{"name": "Array"}, {"name": "Hash Table"}],
                "codeSnippets": [],
            }
        }
    }
    mock_httpx_client.post.return_value = mock_response

    question_data = await fetch_leetcode_question("two-sum")
    assert question_data["id"] == "1"
    assert question_data["title"] == "Two Sum"
    assert "Given an array of integers" in question_data["content"]
    assert question_data["difficulty"] == "Easy"
    assert "Array" in question_data["tags"]
    assert question_data["examples"] == [] # No examples in this mock content
    mock_httpx_client.post.assert_called_once_with(
        LEETCODE_GRAPHQL_URL,
        json={
            "query": """
    query getQuestionDetail($titleSlug: String!) {
      question(titleSlug: $titleSlug) {
        questionId
        questionFrontendId
        title
        content
        difficulty
        topicTags {
          name
          slug
        }
        codeSnippets { # Optionally fetch code snippets
            langSlug
            code
        }
      }
    }
    """,
            "variables": {"titleSlug": "two-sum"},
        },
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/108.0.0.0 Safari/537.36",
            "Referer": "https://leetcode.com/problems/two-sum/",
            "Origin": "https://leetcode.com",
        },
    )

@pytest.mark.asyncio
async def test_fetch_leetcode_question_with_examples(mock_httpx_client):
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "data": {
            "question": {
                "questionFrontendId": "1",
                "title": "Two Sum",
                "content": """<p>Given an array of integers, return indices of the two numbers...</p>
<pre><b>Example 1:</b>
Input: nums = [2,7,11,15], target = 9
Output: [0,1]
Explanation: Because nums[0] + nums[1] == 9, we return [0, 1].
</pre>
""",
                "difficulty": "Easy",
                "topicTags": [],
                "codeSnippets": [],
            }
        }
    }
    mock_httpx_client.post.return_value = mock_response

    question_data = await fetch_leetcode_question("two-sum")
    assert len(question_data["examples"]) == 1
    assert question_data["examples"][0]["input"]["variables"] == {"nums": [2, 7, 11, 15], "target": 9}
    assert question_data["examples"][0]["output"]["value"] == [0,1]

@pytest.mark.asyncio
async def test_fetch_leetcode_question_graphql_error(mock_httpx_client):
    mock_response = MagicMock()
    mock_response.json.return_value = {"errors": [{"message": "Error fetching data"}]}
    mock_httpx_client.post.return_value = mock_response
    question_data = await fetch_leetcode_question("invalid-slug")
    assert question_data is None

@pytest.mark.asyncio
async def test_fetch_leetcode_question_http_error(mock_httpx_client):
    mock_httpx_client.post.side_effect = httpx.HTTPStatusError("Bad Request", request=httpx.Request("POST", LEETCODE_GRAPHQL_URL), response=httpx.Response(400))
    question_data = await fetch_leetcode_question("some-slug")
    assert question_data is None

# Test scrape_leetcode_question (integration of get_title_slug and fetch_leetcode_question)
@pytest.mark.asyncio
async def test_scrape_leetcode_question_success(mock_httpx_client):
    # Mock _fetch_all_problems for get_title_slug
    mock_all_problems_response = MagicMock()
    mock_all_problems_response.json.return_value = {
        "stat_status_pairs": [
            {"stat": {"frontend_question_id": 1, "question__title": "Two Sum", "question__title_slug": "two-sum"}},
        ]
    }
    mock_httpx_client.get.return_value = mock_all_problems_response

    # Mock fetch_leetcode_question
    mock_graphql_response = MagicMock()
    mock_graphql_response.json.return_value = {
        "data": {
            "question": {
                "questionFrontendId": "1",
                "title": "Two Sum",
                "content": "<p>Problem content</p>",
                "difficulty": "Easy",
                "topicTags": [],
                "codeSnippets": [],
            }
        }
    }
    mock_httpx_client.post.return_value = mock_graphql_response

    question = await scrape_leetcode_question("1")
    assert question["title"] == "Two Sum"
    mock_httpx_client.get.assert_called_once() # Called by get_title_slug
    mock_httpx_client.post.assert_called_once() # Called by fetch_leetcode_question

@pytest.mark.asyncio
async def test_scrape_leetcode_question_title_slug_failure(mock_httpx_client):
    # Mock _fetch_all_problems to return no problems
    mock_all_problems_response = MagicMock()
    mock_all_problems_response.json.return_value = {"stat_status_pairs": []}
    mock_httpx_client.get.return_value = mock_all_problems_response

    question = await scrape_leetcode_question("Non Existent Problem")
    assert question is None
    mock_httpx_client.get.assert_called_once()
    mock_httpx_client.post.assert_not_called()

@pytest.mark.asyncio
async def test_scrape_leetcode_question_fetch_failure(mock_httpx_client):
    with patch('app.scrapers.leetcode_scraper._fetch_all_problems') as mock_fetch_all_problems:
        mock_fetch_all_problems.return_value = [
            {"stat": {"frontend_question_id": 1, "question__title": "Two Sum", "question__title_slug": "two-sum"}},
        ]

        mock_httpx_client.post.side_effect = httpx.HTTPStatusError("Not Found", request=httpx.Request("POST", LEETCODE_GRAPHQL_URL), response=httpx.Response(404))

        question = await scrape_leetcode_question("1")
        assert question is None
        mock_fetch_all_problems.assert_called_once()
        mock_httpx_client.post.assert_called_once()

# Test extract_examples_from_content
def test_extract_examples_from_content_success():
    content = """<p>Problem description.</p>
<pre><b>Example 1:</b>
Input: nums = [2,7,11,15], target = 9
Output: [0,1]
Explanation: Because nums[0] + nums[1] == 9, we return [0, 1].
</pre>
<pre><b>Example 2:</b>
Input: x = 123, y = 456
Output: 579
</pre>
"""
    examples = extract_examples_from_content(content)
    assert len(examples) == 2
    assert examples[0]["example_number"] == 1
    assert examples[0]["input"]["variables"] == {"nums": [2,7,11,15], "target": 9}
    assert examples[0]["output"]["value"] == [0,1]
    assert examples[0]["explanation"] == "Because nums[0] + nums[1] == 9, we return [0, 1]."
    assert examples[1]["example_number"] == 2
    assert examples[1]["input"]["variables"] == {"x": 123, "y": 456}
    assert examples[1]["output"]["value"] == 579
    assert examples[1]["explanation"] is None

def test_extract_examples_from_content_no_examples():
    content = "<p>Problem description with no examples.</p>"
    examples = extract_examples_from_content(content)
    assert examples == []

# Test parse_input_data
def test_parse_input_data():
    assert parse_input_data("nums = [2,7,11,15], target = 9") == {"raw": "nums = [2,7,11,15], target = 9", "variables": {"nums": [2, 7, 11, 15], "target": 9}}
    assert parse_input_data("s = \"hello\"") == {"raw": "s = \"hello\"", "variables": {"s": "hello"}}
    assert parse_input_data("grid = [['1','1','1'],['0','1','0']]") == {"raw": "grid = [['1','1','1'],['0','1','0']]", "variables": {"grid": [['1'], ['1'], ['1'], ['0'], ['1'], ['0']]}}
    assert parse_input_data("num = -123") == {"raw": "num = -123", "variables": {"num": -123}}
    assert parse_input_data("val = 3.14") == {"raw": "val = 3.14", "variables": {"val": 3.14}}
    assert parse_input_data("single_var = value_text") == {"raw": "single_var = value_text", "variables": {"single_var": "value_text"}}

# Test parse_output_data
def test_parse_output_data():
    assert parse_output_data("[0,1]") == {"raw": "[0,1]", "value": [0,1]}
    assert parse_output_data("Output: [0,1]") == {"raw": "Output: [0,1]", "value": [0,1]}
    assert parse_output_data("579") == {"raw": "579", "value": 579}
    assert parse_output_data("\"hello\"") == {"raw": "\"hello\"", "value": "hello"}
    assert parse_output_data("true") == {"raw": "true", "value": True}
    assert parse_output_data("false") == {"raw": "false", "value": False}
    assert parse_output_data("some text output") == {"raw": "some text output", "value": "some text output"}
