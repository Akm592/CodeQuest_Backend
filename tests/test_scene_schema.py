"""The scene and suggestion schemas.

Gemini's structured output guarantees the shape, but not that a step refers to
structures that exist or code lines that are there. These checks are what make
the retry in generate_scene worth anything, and what stop a half-consistent
scene from reaching the player.
"""

import copy

import pytest
from pydantic import ValidationError

from app.schemas.visualization_schemas import Scene, Suggestion

TWO_SUM = {
    "title": "Two Sum with a hash map",
    "summary": "Store each value's index; look up the complement before storing.",
    "structures": [
        {"id": "nums", "type": "array", "label": "nums"},
        {"id": "seen", "type": "hashmap", "label": "seen (value -> index)"},
        {"id": "vars", "type": "variables", "label": "variables"},
    ],
    "code": {
        "language": "python",
        "lines": [
            "def two_sum(nums, target):",
            "    seen = {}",
            "    for i, x in enumerate(nums):",
            "        if target - x in seen:",
            "            return [seen[target - x], i]",
            "        seen[x] = i",
        ],
    },
    "steps": [
        {
            "title": "Start with an empty map",
            "explanation": "Nothing has been seen yet, so the map is empty.",
            "code_line": 2,
            "states": [
                {"structure": "nums", "items": ["2", "7", "11", "15"]},
                {"structure": "seen", "entries": []},
            ],
            "variables": [{"name": "target", "value": "9"}],
        },
        {
            "title": "Store 2 at index 0",
            "explanation": "9 - 2 = 7 is not in the map yet, so remember 2 at index 0.",
            "code_line": 6,
            "states": [
                {"structure": "nums", "items": ["2", "7", "11", "15"]},
                {"structure": "seen", "entries": [{"key": "2", "value": "0"}]},
            ],
            "highlights": [{"structure": "nums", "target": "0", "role": "current"}],
            "pointers": [{"name": "i", "structure": "nums", "target": "0"}],
        },
        {
            "title": "Complement 2 found",
            "explanation": "9 - 7 = 2 is in the map at index 0, so the answer is [0, 1].",
            "code_line": 5,
            "states": [
                {"structure": "nums", "items": ["2", "7", "11", "15"]},
                {"structure": "seen", "entries": [{"key": "2", "value": "0"}]},
            ],
            "highlights": [
                {"structure": "nums", "target": "1", "role": "found"},
                {"structure": "seen", "target": "2", "role": "found"},
            ],
        },
    ],
}


def scene(**changes):
    data = copy.deepcopy(TWO_SUM)
    data.update(changes)
    return data


def test_a_well_formed_scene_validates():
    parsed = Scene.model_validate(TWO_SUM)
    assert len(parsed.steps) == 3
    assert parsed.steps[1].pointers[0].name == "i"


def test_a_step_may_not_name_an_undeclared_structure():
    data = scene()
    data["steps"][1]["highlights"][0]["structure"] = "ghost"
    with pytest.raises(ValidationError, match="ghost"):
        Scene.model_validate(data)


def test_a_code_line_must_exist():
    data = scene()
    data["steps"][0]["code_line"] = 99
    with pytest.raises(ValidationError, match="code line 99"):
        Scene.model_validate(data)


def test_a_code_line_needs_code():
    data = scene(code=None)
    with pytest.raises(ValidationError, match="code line"):
        Scene.model_validate(data)


def test_each_structure_type_needs_its_own_field():
    data = scene()
    data["steps"][0]["states"][1] = {"structure": "seen", "items": ["2"]}
    with pytest.raises(ValidationError, match="needs `entries`"):
        Scene.model_validate(data)


def test_bars_must_be_numeric():
    data = scene(structures=[{"id": "a", "type": "bars", "label": "a"}], code=None)
    data["steps"] = [{
        "title": "t", "explanation": "e",
        "states": [{"structure": "a", "items": ["3", "x"]}],
    }]
    with pytest.raises(ValidationError, match="non-numeric"):
        Scene.model_validate(data)


def test_blank_explanations_are_rejected():
    data = scene()
    data["steps"][2]["explanation"] = "   "
    with pytest.raises(ValidationError, match="no explanation"):
        Scene.model_validate(data)


def test_duplicate_structure_ids_are_rejected():
    data = scene()
    data["structures"].append({"id": "nums", "type": "array", "label": "again"})
    with pytest.raises(ValidationError, match="unique"):
        Scene.model_validate(data)


def test_too_many_steps_are_rejected():
    data = scene()
    data["steps"] = data["steps"][1:2] * 41
    with pytest.raises(ValidationError, match="too many steps"):
        Scene.model_validate(data)


def test_unknown_structure_types_are_rejected():
    data = scene()
    data["structures"][0]["type"] = "hologram"
    with pytest.raises(ValidationError):
        Scene.model_validate(data)


def test_a_suggestion_must_carry_the_request_its_kind_needs():
    Suggestion.model_validate({
        "title": "Two Sum", "kind": "trace",
        "trace": {"problem": "Two Sum", "input": "[2,7]", "approach": "hash map"},
    })
    with pytest.raises(ValidationError, match="needs `trace`"):
        Suggestion.model_validate({"title": "Two Sum", "kind": "trace"})
    with pytest.raises(ValidationError, match="needs `builtin`"):
        Suggestion.model_validate({"title": "DP", "kind": "builtin"})
