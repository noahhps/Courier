"""The pure parts of memory.

No fixtures, no event loop, no database -- which is the whole reason these are
the first tests in the project. Everything here is a function with an input and
an answer, and each one is guarding a specific mistake that was made once.
"""

from __future__ import annotations

import pytest

from app.memory import chunking, facts, search
from app.store import _fts_query


# -- chunking ---------------------------------------------------------------


def test_empty_input_produces_no_chunks():
    # `chunks.content` is NOT NULL and would accept "" happily, then match
    # nothing forever.
    assert chunking.split("") == []
    assert chunking.split("   \n\n  ") == []


def test_short_message_is_kept_whole():
    # MIN_CHARS drops noise between paragraphs, not the entire input -- a
    # three-word answer should rank badly, not vanish from history.
    pieces = chunking.split("ok, thanks")
    assert [p.content for p in pieces] == ["ok, thanks"]


def test_no_piece_is_ever_empty():
    messy = "a\n\n\n\n   \n\nb\n\n\n" + ("x " * 2000)
    assert all(p.content.strip() for p in chunking.split(messy))


def test_ordinals_are_dense_and_ordered():
    pieces = chunking.split("\n\n".join(f"Paragraph {i}. " * 40 for i in range(8)))
    assert [p.ordinal for p in pieces] == list(range(len(pieces)))


def test_one_oversized_paragraph_is_split_with_overlap():
    # The case a paragraph-based overlap degenerates on: a pasted log or a
    # model's long answer arrives as a single enormous paragraph.
    body = " ".join(f"Sentence {i} about the boiler." for i in range(200))
    pieces = chunking.split(body)
    assert len(pieces) > 1
    assert all(len(p.content) <= chunking.TARGET_CHARS + 1 for p in pieces)


def test_page_markers_are_carried_and_stripped():
    # extract.py emits these for PDFs; the number is the only landmark a
    # citation can use, and it must not stay in the embedded text.
    text = "[page 1]\nThe tenancy begins in March.\n\n[page 2]\nThe deposit is protected."
    pieces = chunking.split(text)
    assert [p.page for p in pieces] == [1, 2]
    assert all("[page" not in p.content for p in pieces)


def test_text_before_the_first_page_marker_survives():
    pieces = chunking.split("A preamble with no page.\n\n[page 1]\nBody text here.")
    assert pieces[0].page is None
    assert "preamble" in pieces[0].content


# -- FTS query building -----------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        "landlord won't fix it",   # apostrophe: fts5 syntax error near "'"
        "re-let the flat",         # hyphen: "no such column: let"
        "boiler AND",              # bare operator
        "NEAR heating",
        '"unbalanced quote',
    ],
)
def test_user_text_becomes_a_safe_match_expression(raw):
    built = _fts_query(raw)
    assert '"' in built
    # Every bareword is quoted, so nothing is left to be read as an operator.
    assert " AND " not in built.replace('"AND"', "")


def test_query_with_nothing_searchable_is_empty_not_an_error():
    # The caller reports "no results"; raising here would take down a turn
    # over a punctuation mark.
    assert _fts_query("!!! ?? ...") == ""
    assert _fts_query("") == ""


# -- fusion -----------------------------------------------------------------


def test_agreement_between_retrievers_wins():
    # The whole reason for fusing ranks rather than scores: an id both
    # retrievers like beats one that only tops a single list.
    order = search.fuse([["a", "b", "c"], ["c", "b", "z"]], limit=4)
    assert order[0] in {"b", "c"}
    assert set(order) == {"a", "b", "c", "z"}


def test_fusion_tolerates_one_empty_ranking():
    # The normal state of a server whose embedding model is not pulled.
    assert search.fuse([["a", "b"], []], limit=2) == ["a", "b"]
    assert search.fuse([], limit=5) == []


# -- fact extraction --------------------------------------------------------


def test_clean_json_is_parsed():
    parsed = facts.parse('[{"text":"Lives in Leeds","category":"Home","confidence":0.9}]')
    assert parsed == [{"text": "Lives in Leeds", "category": "Home", "confidence": 0.9}]


@pytest.mark.parametrize(
    "reply",
    [
        "",
        "   ",
        "I could not find anything durable.",
        '{"text": "not a list"}',
        "[",
        "[{}]",
        '[{"text": ""}]',
        "null",
    ],
)
def test_every_malformed_reply_is_an_empty_list(reply):
    # This runs unattended after a turn that already succeeded, so a bad reply
    # must never be able to raise.
    assert facts.parse(reply) == []


def test_fenced_and_narrated_json_are_recovered():
    fenced = '```json\n[{"text":"Prefers short answers","confidence":0.8}]\n```'
    narrated = 'Sure!\n[{"text":"Prefers short answers","confidence":0.8}]\nHope that helps.'
    for reply in (fenced, narrated):
        assert facts.parse(reply)[0]["text"] == "Prefers short answers"


def test_low_confidence_guesses_are_discarded():
    assert facts.parse('[{"text":"Maybe likes cats","confidence":0.1}]') == []


def test_unparseable_confidence_does_not_raise():
    parsed = facts.parse('[{"text":"Works in tech","confidence":"high"}]')
    assert parsed[0]["confidence"] == 0.5


def test_a_pass_cannot_flood_the_page():
    many = "[" + ",".join(f'{{"text":"f{i}","confidence":1}}' for i in range(20)) + "]"
    assert len(facts.parse(many)) == facts.MAX_PER_PASS
