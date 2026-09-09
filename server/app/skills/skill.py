"""Base Skill interface."""

from __future__ import annotations

from abc import ABC, abstractmethod


class Skill(ABC):
    """One thing the assistant can do on request.

    A skill carries the two things a caller needs before running it -- a name
    to ask for it by, and a description plain enough for the model to decide
    whether this is the right tool -- and a `use` that does the work.
    """

    #: Set by skills whose answer depends on who is asking. The turn loop then
    #: passes the asking session's `Situation` as a `context` keyword.
    #:
    #: Passed per call rather than stored on the skill, because the registry
    #: holds one instance of each and two conversations from two devices can be
    #: in flight at once -- an attribute set before the call would be a race
    #: between them.
    wants_context: bool = False

    def __init__(
        self,
        *,
        name: str,
        description: str,
        parameters: dict | None = None,
        requires: str | None = None,
    ) -> None:
        self.name = name
        self.description = description
        self.parameters = parameters or {"type": "object", "properties": {}}
        self.enabled = True
        self.requires = requires

    @property
    def available(self) -> bool:
        """Whether this skill could run right now."""
        return True

    @abstractmethod
    async def use(self, **kwargs) -> str:
        """Run the skill and return its result as text."""

    def __str__(self) -> str:
        return f"Skill name: {self.name}, Skill Description: {self.description}\n"

    def schema(self) -> dict:
        """Neutral tool declaration."""
        # Ensure parameters has valid structure
        params = dict(self.parameters) if isinstance(self.parameters, dict) else {}
        if "type" not in params:
            params["type"] = "object"
        if "properties" not in params or not isinstance(params["properties"], dict):
            params["properties"] = {}

        return {
            "name": self.name,
            "description": self.description,
            "parameters": params,
        }