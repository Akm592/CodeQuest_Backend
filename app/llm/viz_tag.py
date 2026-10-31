"""Strip the visualization marker from a streamed answer.

The tutor ends an answer with ``<viz/>`` when a step-by-step trace would help
(see VIZ_MARKER_RULE in app/llm/prompts.py). The marker arrives inside the
stream like any other text, possibly split across chunks ("<vi" + "z/>"), and
must never reach the user or the database. So text is passed through as it
arrives, except for a tail that could still turn into the marker, which is held
until the next chunk settles it. An ordinary "<" in code (``a < b``,
``vector<int>``) is released as soon as the next character rules the marker out.
"""

import re

# Tolerate the obvious variants a model might write.
_MARKER = re.compile(r"<viz\s*/?>", re.IGNORECASE)
# A tail that could still become the marker once more text arrives.
_PARTIAL = re.compile(r"<(?:v(?:i(?:z\s*/?)?)?)?", re.IGNORECASE)


class VizTagFilter:
    """Feed streamed chunks; get back displayable text, and whether a marker was seen."""

    def __init__(self) -> None:
        """Start with nothing held and no marker seen."""
        self._held = ""
        self.found = False

    def feed(self, chunk: str) -> str:
        """Return the part of this chunk that is safe to show now."""
        text = self._held + chunk
        self._held = ""

        if _MARKER.search(text):
            self.found = True
            text = _MARKER.sub("", text)

        cut = text.rfind("<")
        if cut != -1 and _PARTIAL.fullmatch(text[cut:]):
            self._held = text[cut:]
            text = text[:cut]
        return text

    def finish(self) -> str:
        """Release whatever is held: the stream ended, so it was never a marker."""
        rest, self._held = self._held, ""
        return rest
