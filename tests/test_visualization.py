"""Prompt construction and language handling for the LeetCode flow."""

from app.api.chat import _examples_text, _problem_text, _solution_prompt, detect_language
from app.schemas.chat_schemas import ChatRequest

# --- Prompt construction ---------------------------------------------------


def test_problem_text_uses_the_formatted_field():
    """The whole dict used to be interpolated, sending the model a Python repr."""
    scraped = {"formatted_content": "ID: 1\nTitle: Two Sum", "content": "raw", "id": "1"}
    text = _problem_text(scraped)
    assert text == "ID: 1\nTitle: Two Sum"
    assert "'id'" not in text


def test_problem_text_is_truncated():
    assert len(_problem_text({"formatted_content": "x" * 50000})) <= 8000


def test_examples_are_rendered_into_the_prompt():
    scraped = {
        "examples": [
            {
                "example_number": 1,
                "input": {"raw": "nums = [2,7], target = 9"},
                "output": {"raw": "[0,1]"},
            }
        ]
    }
    text = _examples_text(scraped)
    assert "Example 1" in text
    assert "nums = [2,7], target = 9" in text


def test_the_solution_prompt_does_not_ask_for_json():
    """Asking for a JSON block in the answer leaked raw JSON into the chat.

    Visualizations are now generated separately, against a schema, when the
    user clicks a suggestion.
    """
    prompt = _solution_prompt({"formatted_content": "Two Sum"}, "python")
    assert "json" not in prompt.lower()
    assert "**python**" in prompt


def test_no_examples_produces_nothing():
    assert _examples_text({"examples": []}) == ""
    assert _examples_text({}) == ""


# --- Language handling -----------------------------------------------------


def test_a_language_in_the_message_is_detected():
    assert detect_language("solve two sum in java") == "java"
    assert detect_language("do it with Python please") == "python"


def test_cpp_is_detected_despite_its_punctuation():
    assert detect_language("write it in c++") == "c++"


def test_no_language_mentioned():
    assert detect_language("explain binary search") is None


def test_an_unsupported_preferred_language_is_dropped():
    assert ChatRequest(user_input="hi", preferred_language="brainfuck").preferred_language is None


def test_a_supported_preferred_language_is_normalised():
    assert ChatRequest(user_input="hi", preferred_language="  Java ").preferred_language == "java"
