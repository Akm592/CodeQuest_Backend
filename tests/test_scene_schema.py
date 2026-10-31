"""The scene and suggestion schemas.

Gemini's structured output guarantees the shape, but not that a step refers to
structures that exist or code lines that are there. Slips that can be repaired
are; only what can't be drawn at all fails (and is retried once).
"""

import copy

import pytest
from pydantic import ValidationError

from app.schemas.visualization_schemas import CONTEXT_SUGGESTION, Scene, SceneResult, Suggestion

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


def test_highlights_naming_an_undeclared_structure_are_dropped():
    """It used to reject the whole scene, which the user saw as an error."""
    data = scene()
    data["steps"][1]["highlights"][0]["structure"] = "ghost"
    parsed = Scene.model_validate(data)
    assert parsed.steps[1].highlights == []
    assert any("undeclared" in r for r in parsed._repairs)


def test_an_out_of_range_code_line_is_dropped():
    data = scene()
    data["steps"][0]["code_line"] = 99
    assert Scene.model_validate(data).steps[0].code_line is None


def test_code_lines_without_code_are_dropped():
    data = scene(code=None)
    assert all(step.code_line is None for step in Scene.model_validate(data).steps)


def test_a_structure_missing_from_a_step_is_carried_over():
    data = scene()
    del data["steps"][2]["states"][1]  # the map is not restated in the last step
    parsed = Scene.model_validate(data)
    assert parsed.steps[2].states[1].entries[0].key == "2"


def test_the_first_step_must_give_every_structure_contents():
    """Nothing to carry over from, so this one can't be repaired."""
    data = scene()
    data["steps"][0]["states"][1] = {"structure": "seen", "items": ["2"]}
    with pytest.raises(ValidationError, match="entries is required"):
        Scene.model_validate(data)


def test_bars_holding_text_are_drawn_as_an_array():
    data = scene(structures=[{"id": "a", "type": "bars", "label": "a"}], code=None)
    data["steps"] = [{
        "title": "t", "explanation": "e",
        "states": [{"structure": "a", "items": ["3", "x"]}],
    }]
    assert Scene.model_validate(data).structures[0].type == "array"


def test_blank_explanations_are_rejected():
    data = scene()
    data["steps"][2]["explanation"] = "   "
    with pytest.raises(ValidationError, match="no explanation"):
        Scene.model_validate(data)


def test_a_duplicate_structure_is_dropped():
    data = scene()
    data["structures"].append({"id": "nums", "type": "array", "label": "again"})
    assert [s.label for s in Scene.model_validate(data).structures].count("again") == 0


def test_too_many_steps_are_truncated_with_a_note():
    data = scene()
    data["steps"] = data["steps"][1:2] * 45
    parsed = Scene.model_validate(data)
    assert len(parsed.steps) == 40
    assert "cut short" in parsed.steps[-1].explanation


def test_unknown_structure_types_are_rejected():
    data = scene()
    data["structures"][0]["type"] = "hologram"
    with pytest.raises(ValidationError):
        Scene.model_validate(data)


def test_a_feasible_result_needs_a_scene():
    with pytest.raises(ValidationError, match="needs a scene"):
        SceneResult.model_validate({"feasible": True})


def test_a_decline_always_carries_a_reason():
    assert SceneResult.model_validate({"feasible": False}).reason
    assert SceneResult.model_validate({"feasible": False, "reason": "A proof."}).reason == "A proof."


def test_suggestion_kinds():
    assert CONTEXT_SUGGESTION.kind == "context"
    Suggestion.model_validate({
        "title": "Two Sum", "kind": "trace",
        "trace": {"problem": "Two Sum", "input": "[2,7]", "approach": "hash map"},
    })
    with pytest.raises(ValidationError, match="needs `trace`"):
        Suggestion.model_validate({"title": "Two Sum", "kind": "trace"})
