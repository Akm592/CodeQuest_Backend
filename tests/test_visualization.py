"""Validation of the visualization payloads the model produces.

Nothing used to check these beyond the presence of a "visualizationType" key, so
an incomplete payload reached the p5 renderer and drew an empty canvas — which
reads to a user as a broken product rather than a failed request.
"""

from app.api.chat import _examples_text, _extract_visualization, _problem_text, detect_language
from app.schemas.chat_schemas import ChatRequest, validate_visualization


def _sorting_payload(**overrides):
    payload = {
        "visualizationType": "sorting",
        "algorithm": "bubble",
        "array": [3, 1, 2],
        "steps": [{"array": [3, 1, 2], "message": "start"}],
    }
    payload.update(overrides)
    return payload


def test_a_complete_payload_is_accepted():
    assert validate_visualization(_sorting_payload()) is not None


def test_a_payload_missing_its_data_is_rejected():
    payload = _sorting_payload()
    del payload["array"]
    assert validate_visualization(payload) is None


def test_a_payload_with_no_steps_is_rejected():
    assert validate_visualization(_sorting_payload(steps=[])) is None


def test_an_unknown_type_is_rejected():
    assert validate_visualization(_sorting_payload(visualizationType="hologram")) is None


def test_the_type_is_normalised():
    result = validate_visualization(_sorting_payload(visualizationType="  Sorting  "))
    assert result["visualizationType"] == "sorting"


def test_table_is_supported_even_though_the_prompt_omits_it():
    payload = {"visualizationType": "table", "steps": [{"message": "row"}]}
    assert validate_visualization(payload) is not None


def test_non_dict_input_is_rejected():
    assert validate_visualization(None) is None
    assert validate_visualization("nope") is None
    assert validate_visualization([1, 2, 3]) is None


def test_steps_are_capped():
    payload = _sorting_payload(steps=[{"message": str(i)} for i in range(500)])
    result = validate_visualization(payload)
    assert result is not None
    assert len(result["steps"]) <= 60


# --- Extraction from model output -----------------------------------------


def test_a_fenced_json_block_is_extracted():
    text = 'Here you go.\n```json\n{"visualizationType":"table","steps":[{"message":"a"}]}\n```'
    assert _extract_visualization(text) is not None


def test_an_invalid_payload_in_a_fence_yields_nothing():
    """Falling back to text-only beats emitting a frame that renders blank."""
    text = '```json\n{"visualizationType":"sorting"}\n```'
    assert _extract_visualization(text) is None


def test_malformed_json_yields_nothing():
    assert _extract_visualization("```json\n{not json\n```") is None


def test_plain_prose_yields_nothing():
    assert _extract_visualization("Just an explanation, no data.") is None


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
