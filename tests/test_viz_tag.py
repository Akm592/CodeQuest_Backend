"""The streaming filter that hides the <viz/> marker."""

import pytest

from app.llm.viz_tag import VizTagFilter

ANSWER = (
    "Use a hash map.\n\n```cpp\nvector<int> twoSum(vector<int>& a, int t) {\n"
    "  for (int i = 0; i < n; i++) if (a[i] <= t) {}\n}\n```\n"
    "Time O(n).\n<viz/>"
)
SHOWN = ANSWER.replace("<viz/>", "")


def run(chunks):
    f = VizTagFilter()
    out = "".join(f.feed(c) for c in chunks) + f.finish()
    return out, f.found


def test_the_marker_is_removed_and_noticed():
    assert run([ANSWER]) == (SHOWN, True)


def test_any_split_gives_the_same_result():
    """The marker can arrive in pieces; "<vi" + "z/>" must still be caught."""
    for at in range(1, len(ANSWER)):
        assert run([ANSWER[:at], ANSWER[at:]]) == (SHOWN, True), f"split at {at}"


def test_one_character_at_a_time():
    assert run(list(ANSWER)) == (SHOWN, True)


def test_no_marker_means_everything_is_shown():
    text = "Recursion is a function calling itself. a < b, <vector>, <v."
    assert run([text]) == (text, False)
    assert run(list(text)) == (text, False)


@pytest.mark.parametrize("variant", ["<viz/>", "<viz />", "<VIZ/>", "<viz>"])
def test_variants_of_the_marker(variant):
    assert run(["Done.\n" + variant]) == ("Done.\n", True)


def test_ordinary_angle_brackets_are_not_held_back():
    """Text must flow while streaming, not wait for the end."""
    f = VizTagFilter()
    assert f.feed("if a < b:") == "if a < b:"
    assert f.feed(" vector<int>") == " vector<int>"
    assert f.feed(" <vizier>") == " <vizier>"


def test_a_partial_marker_at_the_very_end_is_released():
    assert run(["x <vi"]) == ("x <vi", False)
