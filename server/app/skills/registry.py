from collections.abc import Iterator

from .skill import Skill


class Registry:
    """The skills this server knows about, keyed by name.

    Deliberately in memory only. A skill is code that ships with the server,
    not user data, so the set of them is rebuilt on every boot and there is
    nothing here worth a table.
    """

    def __init__(self) -> None:
        self._skills: dict[str, Skill] = {}

    def get(self, name: str) -> Skill | None:
        return self._skills.get(name)

    def all(self) -> Iterator[tuple[str, Skill]]:
        """Every registered skill, as (name, skill) pairs.

        `yield from`, not `return`: a return inside a loop hands back the first
        pair and stops, which is what this did before. Yielding makes it a
        generator -- the caller drives it, and it walks the whole registry.
        """
        yield from self._skills.items()

    def enabled(self) -> Iterator[tuple[str, Skill]]:
        """Only the skills currently switched on.

        This is what the turn loop should offer the model. `all()` is for the
        screen that lists them, which has to show the off ones too or there
        would be no way to switch them back on.
        """
        for name, skill in self._skills.items():
            if skill.enabled:
                yield name, skill

    def set_enabled(self, name: str, enabled: bool) -> bool:
        """Switch one on or off. False if there is no such skill.

        In memory, like the rest of the registry: a restart brings everything
        back on. Persisting this means a table, and that is a decision about
        user data rather than about code that ships with the server.
        """
        skill = self._skills.get(name)
        if skill is None:
            return False
        skill.enabled = enabled
        return True

    def register(self, skill: Skill) -> str:
        if skill.name in self._skills:
            return f"failed to add {skill.name} to registry"
        self._skills[skill.name] = skill
        return f"successfully added {skill.name} to registry"

    def delete(self, name: str) -> str:
        if name not in self._skills:
            return f"Failed: {name} is not registered."
        del self._skills[name]
        return f"Successfully deleted {name}."
