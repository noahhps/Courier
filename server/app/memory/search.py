"""Ranking. Two retrievers, one order.

The keyword half and the vector half disagree about what a good match is, and
that is the point of running both: one finds the message that says "boiler",
the other finds the one that says "the heating has packed in". Neither is
reliably better, so neither is trusted alone.
"""

from __future__ import annotations

from collections.abc import Sequence

from . import embedding

# Reciprocal rank fusion's damping constant. 60 is the value from the original
# paper and results are famously insensitive to it; what matters is that this
# combines *ranks* rather than scores, because bm25 (negative, unbounded) and
# cosine (-1..1) share no scale and adding them would be meaningless.
RRF_K = 60


def fuse(rankings: Sequence[Sequence[str]], *, limit: int) -> list[str]:
    """Several ranked lists of ids, interleaved into one.

    An id near the top of both lists beats one that merely tops a single list,
    which is exactly the behaviour worth having: agreement between two
    different notions of relevance is the strongest signal available here.
    """
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, key in enumerate(ranking):
            scores[key] = scores.get(key, 0.0) + 1.0 / (RRF_K + rank)
    ordered = sorted(scores.items(), key=lambda pair: (-pair[1], pair[0]))
    return [key for key, _ in ordered[:limit]]


def cosine_ranking(query_vector, chunks, *, limit: int, floor: float) -> list[str]:
    """The nearest chunks above `floor`, best first.

    One matmul over the whole table. At a few thousand chunks that is single-
    digit milliseconds and a couple of megabytes; sqlite-vec earns its place
    somewhere past a hundred thousand rows, and not before.

    The floor is the part that is easy to leave out and expensive to miss.
    Nearest-neighbour search always returns neighbours: ask an unrelated
    question and it hands back the least-unrelated passages in the corpus,
    ranked confidently. Fusion then discards the scores, so nothing downstream
    can tell a good match from the best of a bad lot, and the model is handed
    four irrelevant passages introduced as the best available -- which is
    exactly how a confident wrong answer gets made.

    Its failure mode is deliberately the safe one: set too high, this returns
    nothing and search falls back to keywords; set too low, results get noisy
    but nothing breaks.
    """
    np = embedding.numpy()
    if np is None or not len(chunks):
        return []

    # Rows that disagree about width cannot go in the matrix. That should not
    # happen -- `embedded_chunks` filters on the model that produced them --
    # but a half-finished re-embed after a model change is exactly when search
    # would otherwise raise instead of returning slightly less.
    width = len(query_vector)
    usable = [c for c in chunks if c.embedding is not None and c.dims == width]
    if not usable:
        return []

    matrix = np.frombuffer(b"".join(c.embedding for c in usable), dtype=np.float32)
    matrix = matrix.reshape(len(usable), width)
    scores = matrix @ query_vector  # both sides normalised: this is cosine

    keep = np.flatnonzero(scores >= floor)
    if not len(keep):
        return []
    take = min(limit, len(keep))
    top = keep[np.argpartition(-scores[keep], take - 1)[:take]]
    top = top[np.argsort(-scores[top])]
    return [usable[i].id for i in top]
