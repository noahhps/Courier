"""What shape a model's reasoning control takes.

Families disagree about this, and the disagreement is not cosmetic -- it is a
different *kind* of value each time:

    gpt-oss           an effort word           "low" | "medium" | "high"
    deepseek, qwen3   a switch                 true | false
    Claude            a token budget           1024 .. 32000
    gemma, llama      nothing at all           the model cannot reason on demand

So the client cannot draw one control and relabel it. It has to be told which
of the four it is looking at, and the server is the side that knows which model
is answering -- putting that knowledge in the browser means a name list that
drifts the moment a model is pulled.

Pure: no I/O, no settings, no provider objects. A provider name and a model id
in, a description out. That makes it testable in a REPL and keeps the shape
decision out of the request path.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

Mode = Literal["effort", "switch", "budget", "none"]

# Claude's minimum is 1024; past about 32k the wait stops being worth it for an
# interactive turn, whatever the model will technically accept.
BUDGET_MIN = 1024
BUDGET_MAX = 32000
BUDGET_STEP = 1024
BUDGET_DEFAULT = 4096


@dataclass(frozen=True)
class ThinkingControl:
    mode: Mode
    # effort only
    options: list[str] = field(default_factory=list)
    # budget only
    min: int | None = None
    max: int | None = None
    step: int | None = None
    # Whatever this control sends when nobody has touched it.
    default: Any = None
    # Shown beside the control. Named per family on purpose: "effort" and
    # "budget" are the words those APIs use, and borrowing one for the other
    # would teach the wrong mental model.
    label: str = "Thinking"

    def to_dict(self) -> dict:
        return {
            "mode": self.mode,
            "options": self.options,
            "min": self.min,
            "max": self.max,
            "step": self.step,
            "default": self.default,
            "label": self.label,
        }


NONE = ThinkingControl(mode="none", label="Thinking")

_EFFORT = ThinkingControl(
    mode="effort",
    options=["low", "medium", "high"],
    default="medium",
    label="Effort",
)

_SWITCH = ThinkingControl(mode="switch", default=False, label="Think first")

_BUDGET = ThinkingControl(
    mode="budget",
    min=BUDGET_MIN,
    max=BUDGET_MAX,
    step=BUDGET_STEP,
    default=BUDGET_DEFAULT,
    label="Thinking budget",
)

# Matched on a prefix of the bare model name, so `qwen3.6:14b-q4` and `qwen3`
# land on the same answer. Ordered: the first hit wins, so a longer, more
# specific prefix has to come before a shorter one that would also match.
_OLLAMA: tuple[tuple[tuple[str, ...], ThinkingControl], ...] = (
    (("gpt-oss", "o1", "o3", "o4"), _EFFORT),
    (("deepseek-r1", "deepseek-v", "deepseek", "qwen3", "qwq", "magistral"), _SWITCH),
    (("gemma", "llama", "mistral", "phi", "codellama", "starcoder"), NONE),
)


def _bare(model: str) -> str:
    """`library/qwen3.6:14b` -> `qwen3.6`. Tag and namespace carry no capability."""
    name = model.strip().lower()
    if "/" in name:
        name = name.rsplit("/", 1)[-1]
    return name.split(":", 1)[0]


def control_for(provider: str, model: str) -> ThinkingControl:
    """The reasoning control this model should be driven by.

    An unrecognised Ollama model gets the switch rather than `none`: Ollama
    accepts `think` against any model and ignores it where it means nothing, so
    offering the toggle costs a wasted field at worst, while defaulting to
    `none` would hide reasoning on a model that has it until this table is
    updated. Wrong in the direction that is visible and fixable.
    """
    if provider == "anthropic":
        return _BUDGET
    # OpenRouter fronts every family at once, so a name table would have to
    # know all four of the shapes above -- and it does not need to. Its
    # `reasoning` field takes an effort word for anything and converts it
    # upstream, into a budget for Claude and a switch for the models that
    # only have one, so effort is the control that is true of every model
    # there. Which models can reason at all is a per-model fact rather than a
    # per-family one, and it travels with the catalogue instead: a row that
    # says `reasoning: false` is drawn without this control.
    if provider == "openrouter":
        return _EFFORT

    name = _bare(model)
    for prefixes, control in _OLLAMA:
        if name.startswith(prefixes):
            return control
    return _SWITCH
