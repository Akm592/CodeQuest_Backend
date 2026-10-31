"""Visualization suggestions and scenes.

Two things replace the old "the model writes free-form JSON and we regex it out
of the answer" approach:

* A **suggestion** is a short, clickable offer shown under an answer: "Visualize
  the hash map lookup for [2,7,11,15]". Suggestions are cheap and are produced
  after every answer; nothing heavy is generated until one is clicked.
* A **scene** is the visualization itself: one or more data structures, the
  code being traced, and a list of steps. Each step carries the state of every
  structure, what to highlight, where the named pointers sit, the variables in
  scope, the code line being executed, and a full-sentence explanation.

Both are produced with Gemini's structured output (``response_schema``), so
the model is constrained to this shape rather than asked nicely in a prompt.
Gemini's schema subset has no free-form maps, which is why everything that
would naturally be a dict (hash map entries, variables, per-structure state) is
a list of objects instead.

The schema still cannot express cross-references ("this highlight names a
structure that exists"), so ``Scene`` checks those itself and the API retries
once with the error message if a generated scene fails.
"""

from typing import List, Literal, Optional

from pydantic import BaseModel, Field, model_validator

# The structure kinds the frontend can draw. Extend this and the frontend's
# renderer registry together.
StructureType = Literal[
    "array",       # boxes with indices; pointers and ranges sit on top
    "bars",        # numeric array drawn as bars, for sorting
    "hashmap",     # key -> value entries
    "set",         # unordered members
    "stack",       # items bottom -> top
    "queue",       # items front -> back
    "linked_list", # node values in list order
    "grid",        # 2-D matrix or DP table
    "variables",   # named scalars, shown as a watch list
]

HighlightRole = Literal[
    "current",   # the element being looked at right now
    "compare",   # elements being compared
    "swap",      # elements being swapped or removed
    "found",     # a match / part of the answer
    "visited",   # already processed
    "window",    # inside the current window or range
]

MAX_SCENE_STEPS = 40
MAX_ITEMS = 40


class Entry(BaseModel):
    """A key/value pair: a hash map entry or a builtin parameter."""

    key: str
    value: str


class GridRow(BaseModel):
    """One row of a grid."""

    cells: List[str]


class StructureSpec(BaseModel):
    """A data structure the scene draws."""

    id: str = Field(description="Short identifier, e.g. 'nums' or 'seen'.")
    type: StructureType
    label: str = Field(description="Caption shown above the structure, e.g. 'nums' or 'seen (value -> index)'.")


class StructureState(BaseModel):
    """The contents of one structure at one step. Use the field matching its type."""

    structure: str = Field(description="The id of a structure declared in `structures`.")
    items: Optional[List[str]] = Field(
        default=None,
        description="array/bars/set/linked_list: values in order. stack: bottom to top. queue: front to back.",
    )
    entries: Optional[List[Entry]] = Field(default=None, description="hashmap: key/value pairs.")
    rows: Optional[List[GridRow]] = Field(default=None, description="grid: rows of cells.")


class Highlight(BaseModel):
    """An element this step draws attention to."""

    structure: str
    target: str = Field(
        description="array/bars/stack/queue/linked_list/set: index as a string. "
        "hashmap: the key. grid: 'row,col'. variables: the variable name."
    )
    role: HighlightRole
    label: Optional[str] = Field(default=None, description="Optional tiny tag, e.g. 'target - 2'.")


class Pointer(BaseModel):
    """A named index drawn as an arrow on a structure."""

    name: str = Field(description="e.g. 'i', 'left', 'right', 'slow', 'fast'.")
    structure: str
    target: str = Field(description="Index as a string, or 'row,col' for a grid.")


class Variable(BaseModel):
    """A scalar the code tracks, shown in the watch list."""

    name: str
    value: str


class SceneStep(BaseModel):
    """One operation of the algorithm, with the full state after it."""

    title: str = Field(description="3-8 words naming what happens, e.g. 'Check the complement of 7'.")
    explanation: str = Field(
        description="1-3 full sentences: what happens in this step and WHY, in terms of the algorithm's idea."
    )
    code_line: Optional[int] = Field(default=None, description="1-based line in `code.lines` being executed.")
    states: List[StructureState] = Field(description="The full contents of every structure at this step.")
    highlights: List[Highlight] = Field(default_factory=list)
    pointers: List[Pointer] = Field(default_factory=list)
    variables: List[Variable] = Field(default_factory=list)


class CodeBlock(BaseModel):
    """The solution being traced."""

    language: str
    lines: List[str] = Field(description="The solution, one source line per entry, no line numbers.")


class Scene(BaseModel):
    """A complete step-by-step visualization."""

    title: str
    summary: str = Field(description="2-3 sentences: the approach and why it works.")
    structures: List[StructureSpec]
    code: Optional[CodeBlock] = None
    steps: List[SceneStep]

    @model_validator(mode="after")
    def _consistent(self) -> "Scene":
        if not self.structures:
            raise ValueError("a scene needs at least one structure")
        if not self.steps:
            raise ValueError("a scene needs at least one step")
        if len(self.steps) > MAX_SCENE_STEPS:
            raise ValueError(f"too many steps ({len(self.steps)}); keep it to {MAX_SCENE_STEPS} or fewer")

        ids = [s.id for s in self.structures]
        if len(set(ids)) != len(ids):
            raise ValueError("structure ids must be unique")
        known = set(ids)
        types = {s.id: s.type for s in self.structures}
        line_count = len(self.code.lines) if self.code else 0

        for n, step in enumerate(self.steps, start=1):
            if not step.explanation.strip():
                raise ValueError(f"step {n} has no explanation")
            if step.code_line is not None and not 1 <= step.code_line <= line_count:
                raise ValueError(f"step {n} points at code line {step.code_line}, but the code has {line_count} lines")

            for ref in [*(s.structure for s in step.states),
                        *(h.structure for h in step.highlights),
                        *(p.structure for p in step.pointers)]:
                if ref not in known:
                    raise ValueError(f"step {n} refers to structure '{ref}', which is not declared")

            for state in step.states:
                kind = types[state.structure]
                if kind == "hashmap" and state.entries is None:
                    raise ValueError(f"step {n}: hashmap '{state.structure}' needs `entries`")
                if kind == "grid" and state.rows is None:
                    raise ValueError(f"step {n}: grid '{state.structure}' needs `rows`")
                if kind not in ("hashmap", "grid", "variables") and state.items is None:
                    raise ValueError(f"step {n}: {kind} '{state.structure}' needs `items`")
                if state.items is not None and len(state.items) > MAX_ITEMS:
                    raise ValueError(f"step {n}: '{state.structure}' has more than {MAX_ITEMS} items")
                if kind == "bars":
                    for item in state.items or []:
                        try:
                            float(item)
                        except ValueError:
                            raise ValueError(f"step {n}: bars '{state.structure}' holds non-numeric '{item}'")
        return self


class TraceRequest(BaseModel):
    """What to trace, for a suggestion that needs a generated scene."""

    problem: str = Field(description="The problem or algorithm, e.g. 'Two Sum'.")
    input: str = Field(description="Concrete small input, e.g. 'nums = [2,7,11,15], target = 9'.")
    approach: str = Field(description="The approach to trace, e.g. 'one-pass hash map of complements'.")


class BuiltinRequest(BaseModel):
    """A visualizer the site already has; the browser runs it, no generation needed."""

    key: str
    # Free-form parameters as key/value pairs; the frontend validates them per key.
    params: List[Entry] = Field(default_factory=list)


class Suggestion(BaseModel):
    """A visualization offered under an answer."""

    title: str = Field(description="Button text, under 60 characters, e.g. 'Hash map lookup for [2,7,11,15]'.")
    kind: Literal["trace", "builtin"]
    trace: Optional[TraceRequest] = None
    builtin: Optional[BuiltinRequest] = None

    @model_validator(mode="after")
    def _matches_kind(self) -> "Suggestion":
        if self.kind == "trace" and self.trace is None:
            raise ValueError("a trace suggestion needs `trace`")
        if self.kind == "builtin" and self.builtin is None:
            raise ValueError("a builtin suggestion needs `builtin`")
        return self


class SuggestionList(BaseModel):
    """The visualizations offered under one answer."""

    suggestions: List[Suggestion] = Field(default_factory=list, description="0 to 3 suggestions.")


class VisualizeRequest(BaseModel):
    """Body of POST /chat/visualize."""

    trace: TraceRequest
    # The question and answer the suggestion came from, so the scene traces the
    # same solution the user just read.
    context: Optional[str] = Field(default=None, max_length=12000)
