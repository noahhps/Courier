"""Vectors, packed for SQLite.

numpy is imported lazily. It is a declared dependency, but an environment
created before it was one still has to boot: without it recall degrades to
keyword-only, which is a worse search rather than a broken server.
"""

from __future__ import annotations

from collections.abc import Sequence

_numpy = None
_numpy_checked = False


def numpy():
    """numpy, or None. Cached, because a failed import is not cheap to repeat."""
    global _numpy, _numpy_checked
    if not _numpy_checked:
        _numpy_checked = True
        try:
            import numpy as np

            _numpy = np
        except ImportError:
            print("[memory] numpy is missing -- recall will use keywords only")
    return _numpy


def available() -> bool:
    return numpy() is not None


def pack(vector: Sequence[float]) -> bytes:
    """float32, normalised, as stored.

    Normalised on the way in so similarity is a plain dot product on the way
    out. The alternative is dividing by two norms inside the scoring loop,
    several thousand times per search, for a number that never changes.

    A zero vector -- which an encoder will occasionally return for punctuation
    -- is stored as-is rather than divided by zero. It scores 0 against
    everything, which is the right answer for a chunk with no content.
    """
    np = numpy()
    array = np.asarray(vector, dtype=np.float32)
    norm = float(np.linalg.norm(array))
    if norm:
        array = array / norm
    return array.astype(np.float32).tobytes()


def unpack(blob: bytes):
    np = numpy()
    return np.frombuffer(blob, dtype=np.float32)


def dimensions(vector: Sequence[float]) -> int:
    return len(vector)
