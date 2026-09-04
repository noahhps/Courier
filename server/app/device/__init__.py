"""The machine Courier is running on, as far as the operating system allows.

Everything in here is platform code behind a flat Python interface, so the
skills above it never import a framework or branch on `sys.platform`. A module
that cannot work on this machine says so through `available()` and
`unavailable_reason()` rather than raising on import -- the server has to boot
on Windows and on Linux, where none of this exists.

The split is the same one `memory/` uses: the mechanism lives here, the thing
the model is offered lives in `skills/`.
"""

from __future__ import annotations

__all__ = ["mac_calendar"]
