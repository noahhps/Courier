# Widgets

A skill's answer, drawn rather than recited.

Every skill returns text, because text is what a model reads. That text is
written for the model — dense, repetitive, full of the question restated — and
it is the wrong shape for the person waiting for the reply. A widget is the
other half of the same call: the same facts, laid out as one small card in the
conversation.

Nothing about them is scheduled, subscribed to, or polled. **The skill is the
trigger.** A card exists because the model reached for something while
answering, which is also why a board belongs to a conversation rather than to
the app: two chats ask for different things and end up looking different.

---

## What you see

Above the answer, where the reasoning already sits, because it happened before
the answer and the answer was written from it:

```
Time                      News
╭────────────────────╮   ╭─────────────────────────╮
│ 14:05  BST         │   │ gimlet labs   3 results │
│ Sunday 6 September │   │ Gimlet raises $300M     │
│                    │   │ reuters.com · The round │
│ current_time       │   │ web_search              │
╰────────────────────╯   ╰─────────────────────────╯
```

The tiles are drawn to the reference boards rather than to the rules at the top
of `styles.css`: a real radius, a shadow and no border, and a sentence-case
label in the body face. Those three departures are scoped to `.widget-board`
and are written down where they are made.

One tile is tinted, as the boards have one — the clock, because it is the
ambient card, the thing that is simply true right now rather than something
looked up. The fill is the accent, so it follows a chosen theme.

The last line inside each card names the skill that produced it, and opens the
result the model was actually given. A card is a *reading* of that result, and
the reading should always be checkable without leaving the conversation.

Two things deliberately stay as they were:

* **a skill still running** has no result yet and so cannot have drawn
  anything. It appears in the trace, saying it is running, and becomes a card
  the moment its result lands. A half-drawn card is indistinguishable from a
  wrong one;
* **a skill with no card** — one whose answer is a sentence, where a panel
  around it would add nothing — keeps its trace row.

---

## The seven presets

Preset, not freeform. A skill cannot invent a card; it picks a shape and fills
it in. The shapes are declared in `server/app/widgets/catalog.py` and drawn in
`client/src/lib/widgetPresets.js`.

| Kind | Drawn by | Carries |
|---|---|---|
| `clock` | `current_time` | `time`, `date`, `zone`, `note` |
| `agenda` | `list_events`, `find_events` | `title`, `subtitle`, `empty`, `events[]` of `title`/`when`/`detail` |
| `sources` | `web_search` | `query`, `subtitle`, `empty`, `results[]` of `title`/`domain`/`url`/`snippet` |
| `recall` | `search_history` | `query`, `subtitle`, `empty`, `passages[]` of `text`/`source`/`when` |
| `fact` | `remember`, `forget` | `action`, `text`, `category` |
| `files` | `list_directory`, `search_files` | `path`, `subtitle`, `empty`, `entries[]` of `name`/`kind`/`size` |
| `tool` | every MCP tool | `name`, `source`, `state` (`done`/`failed`), `summary` |

A list is cut at `MAX_ROWS` and the leftover count arrives as `more`, so a card
showing six of forty says so. Every value is capped at `MAX_CHARS`. Empty and
`None` values are dropped rather than stored: the templates test a field for
presence, so "absent" and "there but blank" have to mean the same thing or half
the cards grow an empty line.

`tool` is the fallback and the reason the preset exists. An MCP tool's answer
has whatever shape the server that wrote it chose, so there is nothing to draw
but the fact of the call — which tool, whose server, whether it worked, its
first line. That is worth drawing anyway: most of the skills on a working
install arrive through MCP, and without it they are the only ones that run
invisibly.

---

## How it travels

```
skill.use()                returns SkillResult(text, widget)
orchestrator._run_skill    lifts the widget off, trims the text
  -> SSE  tool_result      {name, text, widget}       the live turn
  -> messages.skills       [{name, arguments, result, widget}]   the record
useChat                    puts the widget on the skill record
WidgetBoard                renders it, or falls back to the trace
```

No migration. `skills` has been a JSON column since migration 5 and its records
are dicts; `widget` is one more key in them. A row written before widgets
existed simply has no key, which is the same thing a skill that drew nothing
writes today.

`SkillResult` is a `str` subclass, not a pair. "A skill returns text" is a
contract older than this feature and the point of widgets is that it does not
change: every caller that treated a result as a string — the turn loop, the
window it is appended to, the tests written against each skill's exact wording
— keeps working untouched. The attribute does not survive string operations, so
the orchestrator lifts the widget off *before* trimming the text; the other
order would silently drop the card of every skill whose text ran long, which is
exactly the skills with the most to draw.

---

## The escaping contract

Card templates are HTML, written in this repository and reviewed. Everything
substituted into one arrived over the wire — a web result's title is a
stranger's sentence, an MCP tool's summary is whatever some other server chose
to return — so `client/src/lib/widgets.js` escapes every value it substitutes
and nothing that arrives at runtime can add a tag, an attribute or a handler to
a card. It is the same contract `lib/markdown.js` keeps, for the same reason.

Two rules for anything written into a template:

* every value is `{{interpolated}}`, never concatenated into an attribute by
  hand;
* an href is `{{field|url}}`. Escaping a `javascript:` URL leaves it a working
  `javascript:` URL, so that filter passes `http` and `https` and turns
  everything else into a dead anchor.

`npm test` in `client/` runs the tests for exactly this. They are the only
tests in the client, because everything else there needs a DOM and this needs a
string.

---

## Adding one

Five places, in this order. The first two are the contract; skip either and the
card renders blank with nothing anywhere saying why.

1. **`server/app/widgets/catalog.py`** — a `Preset` in `KINDS`: what the card
   requires, what it may also carry, and its one list of rows. One list is
   enough for all seven; a card that needs two is a sign it is really two cards.
2. **`client/src/lib/widgetPresets.js`** — a label and an HTML template using
   those field names, and nothing else.
3. **`client/src/styles.css`** — under `-- widgets --`. Reuse the classes that
   are there before adding one; a card that looks unlike its neighbours reads
   as pasted in from another product. A list row is `widget-row-top` with the
   name and a right-aligned `widget-side`, then a grey `widget-row-sub` under
   both — that shape is most of why the boards scan the way they do.
4. **The skill** — return `SkillResult(text, build("your_kind", ...))` instead
   of the string it already returns. Do not change the text: it is what the
   model reads, and every test of that skill asserts on it.
5. **`server/tests/test_widgets.py`** — the catalogue test walks `KINDS` and
   will pick up a new preset on its own. Add a case for anything the skill
   decides, such as which of its branches deliberately draws nothing.

**Not every branch of a skill should draw.** A card is a statement of fact.
`current_time` draws for the two branches that report a time and not for the
three that report a problem — putting a confident-looking panel around an
apology is worse than showing no card at all. An empty *result*, on the other
hand, is a fact worth drawing: "I searched the history and there was nothing"
and "I never searched" look identical in a reply, and only one of them means
the answer that follows is worth less.
