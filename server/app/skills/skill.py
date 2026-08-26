from abc import ABC, abstractmethod


class Skill(ABC):
    """One thing the assistant can do on request.

    A skill carries the two things a caller needs before running it -- a name
    to ask for it by, and a description plain enough for the model to decide
    whether this is the right tool -- and a `use` that does the work.
    """

    def __init__(self, *, name: str, description: str, parameters = None):
        self.name = name;
        self.description = description;
        self.parameters = parameters or {"type": "object", "properties": {}};

    @abstractmethod
    async def use(self, **kwargs) -> str:
        """Run the skill and return its result as text.

        Text because the result is going back into a prompt: whatever a skill
        produces has to survive being read by a model, and a string is the one
        shape that always does.

        Async because skills reach SQLite, subprocesses and the network, and
        the turn loop has to await them -- a blocking call here would stall the
        whole server, not just this conversation.

        Keyword arguments only. A model always sends named arguments, matching
        the JSON Schema in `parameters`, and `*args` could never be validated
        against it. Subclasses should name their parameters explicitly rather
        than take `**kwargs`: a hallucinated argument name then raises
        TypeError, which the loop catches and hands back for the model to
        correct, instead of being silently swallowed.
        """
        
    def __str__(self) -> str:
        return f"Skill name: {self.name}, Skill Description: {self.description}\n"

    def schema(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
        }