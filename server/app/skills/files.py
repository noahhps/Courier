"""The device's own files, as three read-only skills.

Read-only on purpose, and not as a placeholder. A local model handed write and
delete on a person's Desktop is one hallucinated path away from destroying
something that was never backed up, and the card that would let the reader
approve a write (`PermissionAsk`) is built but not yet wired to the turn loop.
Reading is the half that is safe to ship without it; writing lands the day
there is a gate in front of it.

Everything is confined to `settings.device_roots` by `resolve`, which is the
only function here allowed to turn a model's string into a real path. It
resolves first and checks second, so a symlink pointing out of a root fails the
same way `../../..` does.

Names are chosen for what the model has to decide between: `list_directory`,
`read_file`, `search_files`. It should be able to pick one from the name alone.
"""

from __future__ import annotations

import fnmatch
from pathlib import Path

from .skill import Skill

# What never gets listed or read, however it is asked for. These are the things
# a home directory is full of that a model has no business seeing: credentials,
# keys, browser profiles, and the machinery of the tools it is running inside.
HIDDEN = {
    ".ssh", ".aws", ".gnupg", ".config", ".kube", ".docker", ".netrc",
    ".env", ".git", ".gitconfig", ".npmrc", ".pypirc", "node_modules",
    "Library", ".Trash", ".venv", "__pycache__",
}

# Extensions read as text. Anything else is reported by name and size rather
# than decoded -- handing a model a megabyte of mangled JPEG bytes wastes the
# window and tells it nothing.
TEXT_SUFFIXES = {
    ".txt", ".md", ".markdown", ".rst", ".csv", ".tsv", ".json", ".yaml", ".yml",
    ".toml", ".ini", ".cfg", ".conf", ".log", ".py", ".js", ".jsx", ".ts", ".tsx",
    ".rs", ".go", ".java", ".c", ".h", ".cpp", ".css", ".html", ".xml", ".sh",
    ".sql", ".swift", ".kt", ".rb", ".php", ".lua", ".r", ".tex",
}

MAX_ENTRIES = 200
MAX_MATCHES = 60


class OutsideRoots(ValueError):
    """A path that resolved outside every configured root."""


def _hidden(path: Path, roots: tuple[Path, ...]) -> bool:
    """Whether any part of `path` below its root is on the deny list.

    Checked from the root down rather than over the whole absolute path, so a
    root that itself sits inside something named `.config` still works -- the
    reader chose that root, and this is here to stop the model wandering, not
    to overrule them.
    """
    for root in roots:
        if path == root or root in path.parents:
            relative = path.relative_to(root)
            return any(part in HIDDEN or part.startswith(".") for part in relative.parts)
    return True


def resolve(given: str, roots: tuple[Path, ...]) -> Path:
    """One model-supplied string as a real path inside a root, or an error.

    `.resolve()` before the containment check, never after: a symlink is only a
    way out of a root if nobody follows it before looking.
    """
    if not roots:
        raise OutsideRoots(
            "No folders have been shared with me. Set DEVICE_ROOTS to the "
            "directories I may look in."
        )
    text = (given or "").strip()
    if not text:
        raise OutsideRoots("Give me a path.")
    candidate = Path(text).expanduser()
    if not candidate.is_absolute():
        # A relative path naming a root -- "Documents", or "Documents/tax.pdf"
        # -- means that root, not a child of the first one. Without this, a
        # model that reads a root's name out of a listing and hands it back
        # gets Documents/Documents and has to guess again.
        head, _, tail = text.partition("/")
        by_name = next((r for r in roots if r.name == head), None)
        candidate = (by_name / tail if tail else by_name) if by_name else roots[0] / candidate
    try:
        target = candidate.resolve()
    except OSError as exc:
        raise OutsideRoots(f"{given!r} is not a path I can read: {exc}") from exc

    if not any(target == root or root in target.parents for root in roots):
        allowed = ", ".join(str(r) for r in roots)
        raise OutsideRoots(f"{given!r} is outside the folders I can see. I can look in: {allowed}.")
    if _hidden(target, roots):
        raise OutsideRoots(f"{given!r} is in a hidden or protected folder, which I do not read.")
    return target


def _describe(path: Path) -> str:
    try:
        stat = path.stat()
    except OSError:
        return f"{path.name}  (unreadable)"
    if path.is_dir():
        return f"{path.name}/"
    size = stat.st_size
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            shown = f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
            break
        size /= 1024
    return f"{path.name}  {shown}"


class _Rooted(Skill):
    """Shared construction for the skills that take a path."""

    def __init__(self, *, name: str, description: str, parameters, settings) -> None:
        super().__init__(name=name, description=description, parameters=parameters)
        self.settings = settings

    @property
    def roots(self) -> tuple[Path, ...]:
        return self.settings.device_roots

    @property
    def available(self) -> bool:
        return bool(self.roots)


class ListDirectory(_Rooted):
    def __init__(self, settings) -> None:
        super().__init__(
            name="list_directory",
            description=(
                "List what is in one of the user's folders. Call with no path "
                "to see which folders are available. Use before reading a file "
                "so you know the name rather than guessing it."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Folder to list. Omit to list the shared folders themselves.",
                    }
                },
            },
            settings=settings,
        )

    async def use(self, path: str | None = None) -> str:
        roots = self.roots
        if not roots:
            return "No folders have been shared with me."
        # With one shared folder there is nothing to choose between, so listing
        # it is what "no path" must mean. Naming it instead cost a real turn
        # four rounds: the model read the folder back, passed its name, and got
        # Shared/Shared. Only offer the choice when there is one to make.
        if not (path or "").strip() and len(roots) > 1:
            listed = "\n".join(f"{r}/" for r in roots)
            return f"I can look in these folders:\n{listed}"
        try:
            target = roots[0] if not (path or "").strip() else resolve(path, roots)
        except OutsideRoots as exc:
            return str(exc)
        if not target.is_dir():
            return f"{target} is a file, not a folder. Use read_file for it."
        try:
            entries = sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        except OSError as exc:
            return f"I could not read {target}: {exc}"
        visible = [e for e in entries if not _hidden(e, roots)]
        if not visible:
            return f"{target} is empty."
        shown = visible[:MAX_ENTRIES]
        out = "\n".join(_describe(e) for e in shown)
        if len(visible) > MAX_ENTRIES:
            out += f"\n… and {len(visible) - MAX_ENTRIES} more."
        return f"{target}:\n{out}"


class ReadFile(_Rooted):
    def __init__(self, settings) -> None:
        super().__init__(
            name="read_file",
            description=(
                "Read a text file from the user's folders. Long files are cut "
                "and the result says where, so ask for a later part with "
                "'offset' rather than assuming you saw all of it."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "The file to read."},
                    "offset": {
                        "type": "integer",
                        "description": "Line to start at, 1-based. Defaults to the beginning.",
                    },
                },
                "required": ["path"],
            },
            settings=settings,
        )

    async def use(self, path: str, offset: int = 1) -> str:
        try:
            target = resolve(path, self.roots)
        except OutsideRoots as exc:
            return str(exc)
        if target.is_dir():
            return f"{target} is a folder. Use list_directory for it."
        if not target.is_file():
            return f"There is no file at {target}."
        if target.suffix.lower() not in TEXT_SUFFIXES:
            return (
                f"{target.name} is not a text file ({target.suffix or 'no extension'}), "
                "so I cannot read it as words. I can tell you it exists and how big it is."
            )
        try:
            body = target.read_text("utf-8", errors="replace")
        except OSError as exc:
            return f"I could not read {target}: {exc}"

        lines = body.splitlines()
        try:
            start = max(1, int(offset))
        except (TypeError, ValueError):
            start = 1
        window = lines[start - 1 :]
        budget = self.settings.device_read_chars
        kept, used = [], 0
        for line in window:
            if used + len(line) + 1 > budget:
                break
            kept.append(line)
            used += len(line) + 1
        end = start + len(kept) - 1
        head = f"{target}  (lines {start}-{max(end, start)} of {len(lines)})\n"
        numbered = "\n".join(f"{start + i}\t{line}" for i, line in enumerate(kept))
        if end < len(lines):
            numbered += f"\n… cut here. Call again with offset={end + 1} for the rest."
        return head + numbered


class SearchFiles(_Rooted):
    def __init__(self, settings) -> None:
        super().__init__(
            name="search_files",
            description=(
                "Find files by name across the user's folders, e.g. '*.pdf' or "
                "'*tax*'. Use when the user names a file but not where it is."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Filename pattern, e.g. '*.csv' or '*invoice*'.",
                    },
                    "path": {
                        "type": "string",
                        "description": "Folder to search under. Defaults to every shared folder.",
                    },
                },
                "required": ["pattern"],
            },
            settings=settings,
        )

    async def use(self, pattern: str, path: str | None = None) -> str:
        roots = self.roots
        needle = (pattern or "").strip()
        if not needle:
            return "Give me something to search for, like '*.pdf'."
        # A bare word means "contains", which is what a person means by it.
        if not any(ch in needle for ch in "*?["):
            needle = f"*{needle}*"

        if (path or "").strip():
            try:
                bases = [resolve(path, roots)]
            except OutsideRoots as exc:
                return str(exc)
        else:
            bases = list(roots)

        found: list[Path] = []
        for base in bases:
            if not base.is_dir():
                continue
            for candidate in base.rglob("*"):
                if len(found) >= MAX_MATCHES:
                    break
                if candidate.is_dir() or _hidden(candidate, roots):
                    continue
                if fnmatch.fnmatch(candidate.name.lower(), needle.lower()):
                    found.append(candidate)

        if not found:
            return f"Nothing matching {pattern!r} in {', '.join(str(b) for b in bases)}."
        out = "\n".join(str(f) for f in found)
        if len(found) >= MAX_MATCHES:
            out += f"\n… stopped at {MAX_MATCHES} matches; narrow the pattern."
        return out
