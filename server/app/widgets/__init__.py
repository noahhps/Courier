"""Widgets: the shown half of a skill's answer.

`Widget` and `SkillResult` are the shapes; `catalog` is the list of cards that
exist and the validator that keeps a skill inside it.
"""

from .catalog import KINDS, MAX_ROWS, UnknownWidget, build
from .widget import SkillResult, Widget

__all__ = ["KINDS", "MAX_ROWS", "SkillResult", "UnknownWidget", "Widget", "build"]
