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
