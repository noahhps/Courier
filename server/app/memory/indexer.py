"""Keeping the chunk table level with everything else, and searching it.

One object owns both halves because both need the same two facts: which
encoder is in use, and how to reach it. Splitting them would mean two places
that have to agree about `chunks.model`, and disagreement there does not raise
-- it just returns nothing, quietly.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..config import Settings
from ..providers import ProviderError, ProviderRouter
from ..store import Store, StoredChunk
from . import chunking, embedding, search

# Chunks embedded per request to Ollama. Large enough that a backfill is a
# handful of calls, small enough that a failure loses little work and the
# runner is not handed a megabyte of text at once.
EMBED_BATCH = 64

# How many chunks each retriever contributes before fusion. Wider than the
# final answer on purpose: fusion can only reward agreement it can see, and a
# result ranked 15th by keywords and 2nd by vectors is exactly the one worth
# surfacing.
CANDIDATES = 30


@dataclass(frozen=True)
class Hit:
    """One search result, with enough provenance to cite it."""

    chunk: StoredChunk
    source: dict


class Indexer:
    def __init__(self, settings: Settings, store: Store, router: ProviderRouter) -> None:
        self.settings = settings
        self.store = store
        self.router = router
        # Latched after the first failure so a missing embedding model prints
        # one line rather than one per turn forever.
        self._embedding_warned = False

    @property
    def model(self) -> str:
        return self.settings.embed_model

    # -- writing ----------------------------------------------------------

    async def catch_up(self, session_id: str | None = None) -> dict:
        """Chunk what has no chunks, embed what has no embedding.

        Idempotent and interruptible, which is what lets one method serve as
        both the one-off backfill over months of history and the top-up after
        every turn. There is no "have I run this yet" flag to get wrong: the
        answer is always in the tables.
        """
        chunked = self._chunk_pending(session_id)
        embedded = await self._embed_pending()
        return {"chunked": chunked, "embedded": embedded}

    def _chunk_pending(self, session_id: str | None) -> int:
        written = 0
        for message in self.store.messages_needing_chunks(session_id):
            pieces = chunking.split(message.content)
            written += self.store.add_chunks(pieces, message_id=message.id)

        # Attachments are session-independent -- an unchunked one from another
        # conversation is still worth indexing, and there is no cheap join
        # from a file to a session that does not go through its message.
        for attachment in self.store.attachments_needing_chunks():
            text = _readable(attachment)
            pieces = chunking.split(text)
            if pieces:
                written += self.store.add_chunks(pieces, attachment_id=attachment.id)
        return written

    async def _embed_pending(self) -> int:
        """Give vectors to chunks that lack them.

        Every failure leaves the rows alone. `idx_chunks_pending` means the
        next pass finds them again, and until then search runs on keywords --
        a worse answer rather than no answer.
        """
        if not embedding.available():
            return 0

        done = 0
        while True:
            pending = self.store.chunks_pending_embedding(EMBED_BATCH)
            if not pending:
                return done
            try:
                vectors = await self.router.local.embed(
                    _prefix(self.model, [c.content for c in pending], query=False)
                )
            except (ProviderError, Exception) as exc:  # httpx raises its own
                self._warn_once(exc)
                return done
            if len(vectors) != len(pending):
                self._warn_once(
                    f"asked for {len(pending)} vectors and got back {len(vectors)}"
                )
                return done
            self.store.set_embeddings(
                [
                    (chunk.id, embedding.pack(vector), len(vector), self.model)
                    for chunk, vector in zip(pending, vectors)
                ]
            )
            done += len(pending)
            # A batch smaller than the cap means the queue is drained.
            if len(pending) < EMBED_BATCH:
                return done

    def _warn_once(self, problem) -> None:
        if self._embedding_warned:
            return
        self._embedding_warned = True
        print(
            f"[memory] cannot embed with {self.model!r}: {problem}. "
            f"Recall is running on keywords alone -- `ollama pull {self.model}` "
            "and it will catch up on the next turn."
        )

    # -- reading ----------------------------------------------------------

    async def search(self, query: str, *, limit: int = 6) -> list[Hit]:
        """The best chunks for a query, best first.

        Keyword and vector rankings are fused; either may be empty. An empty
        vector side is the normal state of a server whose embedding model has
        not been pulled, and the keyword side alone is a perfectly usable
        search -- the whole point of ranking by position rather than by score.
        """
        keyword = [chunk_id for chunk_id, _ in
                   self.store.search_chunks_fts(query, CANDIDATES)]
        vector = await self._vector_ranking(query)

        rankings = [r for r in (keyword, vector) if r]
        if not rankings:
            return []
        ids = search.fuse(rankings, limit=limit)

        chunks = self.store.chunks_by_id(ids)
        sources = self.store.chunk_sources(ids)
        return [
            Hit(chunk=chunks[cid], source=sources.get(cid, {}))
            for cid in ids
            if cid in chunks
        ]

    async def _vector_ranking(self, query: str) -> list[str]:
        if not embedding.available():
            return []
        rows = self.store.embedded_chunks(self.model)
        if not rows:
            return []
        try:
            # Always the local provider, never `router.resolve()`: the cloud
            # path raises by design, and a query embedded by a different
            # encoder than the corpus would silently rank at random.
            vectors = await self.router.local.embed(
                _prefix(self.model, [query], query=True)
            )
        except Exception as exc:
            self._warn_once(exc)
            return []
        if not vectors:
            return []
        query_vector = embedding.unpack(embedding.pack(vectors[0]))
        return search.cosine_ranking(
            query_vector, rows,
            limit=CANDIDATES,
            floor=self.settings.memory_min_similarity,
        )


# nomic-embed-text is trained with task prefixes and expects them at inference:
# a stored passage is a "search_document", the thing being looked up is a
# "search_query". Without them both sides land in the same undifferentiated
# region of the space, every pair looks moderately similar, and the ranking
# flattens -- which reads as "retrieval is a bit rubbish" rather than as a
# missing string.
#
# Applied by model name because it is that family's convention, not a general
# one: prefixing a model that was not trained on them makes results worse.
_PREFIXED = ("nomic-embed",)


def _prefix(model: str, texts: list[str], *, query: bool) -> list[str]:
    if not any(family in model for family in _PREFIXED):
        return texts
    tag = "search_query: " if query else "search_document: "
    return [tag + text for text in texts]


def _readable(attachment) -> str:
    """The words in an attachment.

    A document was read at upload and the result is in the column beside the
    bytes; a text file is its own bytes. Mirrors the window builder's rule,
    because a chunk of a file and the prompt version of that file should be
    the same text.
    """
    if attachment.kind == "document":
        return attachment.text or ""
    return (attachment.data or b"").decode("utf-8", "replace")
