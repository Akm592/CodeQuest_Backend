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

from pydantic import BaseModel, Field, PrivateAttr, model_validator

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

    # What _repair changed, for logging. Not part of the schema.
    _repairs: List[str] = PrivateAttr(default_factory=list)

    @model_validator(mode="after")
    def _repair(self) -> "Scene":
        """Fix the slips that can be fixed; fail only on what can't be drawn.

        The first version rejected a whole scene over any inconsistency: one
        highlight naming a misspelled structure, a code line one past the
        end, a 41st step. Those are exactly the slips a model makes on a big
        trace, and after one retry the user got "Couldn't build a valid
        visualization" for a scene that was 99% right. They are now repaired
        (and logged). Only problems with no sensible repair fail.
        """
        repairs = self._repairs
        if not self.structures:
            raise ValueError("a scene needs at least one structure")
        if not self.steps:
            raise ValueError("a scene needs at least one step")

        # Duplicate ids: keep the first declaration.
        seen_ids = set()
        unique = []
        for spec in self.structures:
            if spec.id in seen_ids:
                repairs.append(f"dropped duplicate structure '{spec.id}'")
                continue
            seen_ids.add(spec.id)
            unique.append(spec)
        self.structures = unique

        if len(self.steps) > MAX_SCENE_STEPS:
            repairs.append(f"truncated {len(self.steps)} steps to {MAX_SCENE_STEPS}")
            self.steps = self.steps[:MAX_SCENE_STEPS]
            last = self.steps[-1]
            last.explanation = last.explanation.rstrip() + " (The trace is cut short here to keep it readable.)"

        # Bars that hold text can't be drawn as bars, but can as an array.
        for spec in self.structures:
            if spec.type != "bars":
                continue
            for step in self.steps:
                for state in step.states:
                    if state.structure == spec.id and any(not _is_number(x) for x in state.items or []):
                        spec.type = "array"
                        repairs.append(f"bars '{spec.id}' hold non-numbers; drawn as an array")
                        break
                if spec.type != "bars":
                    break

        known = {s.id for s in self.structures}
        types = {s.id: s.type for s in self.structures}
        line_count = len(self.code.lines) if self.code else 0
        previous: dict = {}

        for n, step in enumerate(self.steps, start=1):
            if not step.explanation.strip():
                raise ValueError(f"step {n} has no explanation")

            if step.code_line is not None and not 1 <= step.code_line <= line_count:
                repairs.append(f"step {n}: dropped code line {step.code_line} (code has {line_count})")
                step.code_line = None

            for field in ("highlights", "pointers"):
                kept = [x for x in getattr(step, field) if x.structure in known]
                if len(kept) != len(getattr(step, field)):
                    repairs.append(f"step {n}: dropped {field} naming undeclared structures")
                setattr(step, field, kept)

            states = {}
            for state in step.states:
                if state.structure not in known:
                    repairs.append(f"step {n}: dropped state of undeclared '{state.structure}'")
                    continue
                if state.items is not None and len(state.items) > MAX_ITEMS:
                    repairs.append(f"step {n}: truncated '{state.structure}' to {MAX_ITEMS} items")
                    state.items = state.items[:MAX_ITEMS]
                if _has_contents(types[state.structure], state):
                    states[state.structure] = state

            # A structure missing from a step (or given the wrong field) keeps
            # its contents from the step before. Only the first step has
            # nothing to fall back on.
            for spec in self.structures:
                if spec.type == "variables" or spec.id in states:
                    continue
                if spec.id not in previous:
                    raise ValueError(
                        f"step {n} gives no contents for {spec.type} '{spec.id}' "
                        f"({_FIELD_FOR.get(spec.type, 'items')} is required)"
                    )
                repairs.append(f"step {n}: carried '{spec.id}' over from the previous step")
                states[spec.id] = previous[spec.id].model_copy(deep=True)

            step.states = [states[s.id] for s in self.structures if s.id in states]
            previous = states
        return self


_FIELD_FOR = {"hashmap": "entries", "grid": "rows"}


def _is_number(value: str) -> bool:
    try:
        float(value)
        return True
    except ValueError:
        return False


def _has_contents(kind: str, state: "StructureState") -> bool:
    if kind == "variables":
        return True
    if kind == "hashmap":
        return state.entries is not None
    if kind == "grid":
        return state.rows is not None
    return state.items is not None


class SceneResult(BaseModel):
    """Either a scene, or a reason why this can't be traced faithfully."""

    feasible: bool = Field(
        description="False when the problem cannot be traced faithfully step by step on a small input."
    )
    reason: str = Field(
        default="",
        description="When not feasible: one sentence saying why, addressed to the learner.",
    )
    scene: Optional[Scene] = None

    @model_validator(mode="after")
    def _scene_when_feasible(self) -> "SceneResult":
        if self.feasible and self.scene is None:
            raise ValueError("a feasible result needs a scene")
        if not self.feasible and not self.reason.strip():
            self.reason = "This one doesn't break down into clear steps on a small example."
        return self


class TraceRequest(BaseModel):
    """A specific trace. Sent by suggestions saved before the single "Visualize this" button."""

    problem: str = Field(description="The problem or algorithm, e.g. 'Two Sum'.")
    input: str = Field(description="Concrete small input, e.g. 'nums = [2,7,11,15], target = 9'.")
    approach: str = Field(description="The approach to trace, e.g. 'one-pass hash map of complements'.")


class Suggestion(BaseModel):
    """A visualization offered under an answer."""

    title: str
    # "context": trace whatever the conversation is about, chosen on click.
    # "trace": a specific request, from suggestions saved by the first version.
    kind: Literal["context", "trace"]
    trace: Optional[TraceRequest] = None

    @model_validator(mode="after")
    def _matches_kind(self) -> "Suggestion":
        if self.kind == "trace" and self.trace is None:
            raise ValueError("a trace suggestion needs `trace`")
        return self


CONTEXT_SUGGESTION = Suggestion(title="Visualize this with an example", kind="context")


class VisualizeRequest(BaseModel):
    """Body of POST /chat/visualize."""

    # The question and answer the button sits under: the scene traces the same
    # solution the user just read, on an example drawn from it.
    context: str = Field(min_length=1, max_length=12000)
    trace: Optional[TraceRequest] = None
