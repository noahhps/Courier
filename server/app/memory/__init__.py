"""Memory: what can be looked up, and what is known without looking.

Two halves, deliberately separate. `chunking`/`embedding`/`search`/`indexer`
build recall over everything ever said; `facts` curates the short list that
rides in every system prompt. The first is a filing cabinet, the second is
what you know without opening it.
"""


# The three switches on the Memory page, with the positions they take before
# anyone has touched them. Conservative on purpose: memory is on because that
# is the point of the product, and nothing inferred is kept without being
# confirmed first.
#
# Defaults live here rather than in the table so an upgrade adds a switch
# without a migration, and so the meaning of "unset" is written down once.
MEMORY_DEFAULTS = {
    "memory.between_chats": True,   # use, and add to, memory across sessions
    "memory.confirm": True,         # inferred facts wait for a human first
    "memory.share": False,          # reserved: there are no projects yet
}
