import { useMemo, useState } from "react";

import { gridFor, iso, useCalendar } from "../hooks/useCalendar";
import { useChat } from "../hooks/useChat";
import { Icon } from "./Icon";

const DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const MONTHS = [
  "January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December",
];

/* A conversation beside the calendar rather than a page away.
 *
 * Its own `useChat`, so it gets its own session: asking "what am I doing on
 * Thursday" should not land in the middle of whatever the main thread was
 * about, and the skills it calls read the same calendar either way. */
function SideChat({ api, onSessionsChanged, onAnswered }) {
  const chat = useChat(api, { onSessionsChanged });
  const [value, setValue] = useState("");

  const send = async (event) => {
    event.preventDefault();
    const text = value.trim();
    if (!text || chat.streaming) return;
    setValue("");
    await chat.send(text);
    // The model may have added an event through a skill, and the grid has no
    // way to know that happened.
    onAnswered();
  };

  return (
    <div className="sidechat">
      <div className="lane">
        <span className="mi" data-strong>
          Ask about your calendar
        </span>
        <i />
        {chat.messages.length ? (
          <button type="button" className="mi" onClick={chat.startNew}>
            Clear
          </button>
        ) : null}
      </div>

      <div className="sidechat-thread">
        {chat.messages.length === 0 ? (
          <p className="p sidechat-empty">
            “What’s on next week?” · “Put a dentist appointment on Tuesday at 9”
          </p>
        ) : (
          chat.messages.map((m) => (
            <div key={m.key} className="sidechat-turn" data-role={m.role}>
              {m.content || (m.streaming ? "…" : "")}
            </div>
          ))
        )}
      </div>

      <form className="sidechat-form" onSubmit={send}>
        <input
          type="text"
          value={value}
          placeholder="Ask, or add an event"
          aria-label="Ask about your calendar"
          disabled={chat.streaming}
          onChange={(e) => setValue(e.target.value)}
        />
        {/* Same swap as the main composer: while a turn runs, the control is
            the way out of it rather than a greyed-out arrow. */}
        {chat.streaming ? (
          <button
            type="button"
            className="send"
            aria-label="Stop generating"
            onClick={chat.stop}
          >
            <Icon name="stop" />
          </button>
        ) : (
          <button type="submit" className="send" aria-label="Send">
            <Icon name="send" />
          </button>
        )}
      </form>
    </div>
  );
}

export function Calendar({ api, onSessionsChanged }) {
  const [cursor, setCursor] = useState(() => new Date());
  const [picked, setPicked] = useState(() => iso(new Date()));
  const { events, loading, error, refresh, add, remove } = useCalendar(api, cursor);
  const [draft, setDraft] = useState({ title: "", time: "", notes: "" });
  const [saving, setSaving] = useState(false);

  // Bucketed by date once per fetch, rather than filtering 42 times while
  // rendering the grid.
  const byDay = useMemo(() => {
    const map = new Map();
    for (const event of events) {
      const key = event.starts_at.slice(0, 10);
      if (!map.has(key)) map.set(key, []);
      map.get(key).push(event);
    }
    return map;
  }, [events]);

  const cells = useMemo(() => gridFor(cursor), [cursor]);
  const today = iso(new Date());
  const dayEvents = byDay.get(picked) || [];
  // `events` covers the whole six-week grid, including the days either side of
  // the month. The label says "this month", so it has to count that and not
  // what was fetched.
  const thisMonth = useMemo(
    () => events.filter((e) => Number(e.starts_at.slice(5, 7)) === cursor.getMonth() + 1
      && Number(e.starts_at.slice(0, 4)) === cursor.getFullYear()).length,
    [events, cursor],
  );

  const step = (by) =>
    setCursor((was) => new Date(was.getFullYear(), was.getMonth() + by, 1));

  const submit = async (event) => {
    event.preventDefault();
    const title = draft.title.trim();
    if (!title || saving) return;
    setSaving(true);
    try {
      await add({
        title,
        // No time given means an all-day event, which is the same rule the
        // skill applies -- one calendar, one definition.
        starts_at: draft.time ? `${picked}T${draft.time}` : picked,
        all_day: !draft.time,
        notes: draft.notes.trim() || null,
      });
      setDraft({ title: "", time: "", notes: "" });
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="page">
      <div className="page-head" data-tint="violet">
        <div className="sw" style={{ left: "-90px", top: "-110px", width: "280px", height: "280px", background: "var(--violet-field)" }} />
        <div className="inner">
          <div>
            <h1 className="h">Calendar</h1>
            <p>
              What is coming up. The assistant can read this and add to it —
              ask it in the panel on the right.
            </p>
          </div>
          <div className="actions">
            <button type="button" className="btn" onClick={() => step(-1)} aria-label="Previous month">
              ←
            </button>
            <button type="button" className="btn" onClick={() => setCursor(new Date())}>
              Today
            </button>
            <button type="button" className="btn" onClick={() => step(1)} aria-label="Next month">
              →
            </button>
          </div>
        </div>
      </div>

      <div className="page-body">
        <div className="page-col">
          <div className="lane">
            <span className="mi" data-strong>
              {MONTHS[cursor.getMonth()]} {cursor.getFullYear()}
            </span>
            <i />
            <span className="mi">
              {loading ? "loading" : `${thisMonth} this month`}
            </span>
          </div>

          {error ? (
            <div className="callout" data-tint="ochre">
              <div style={{ flex: 1 }}>
                <span className="h" style={{ fontSize: "var(--t-lg)" }}>
                  Could not load the calendar
                </span>
                <p style={{ margin: "7px 0 0", color: "var(--text-dim)", fontSize: "var(--t-sm)" }}>
                  {error}
                </p>
              </div>
              <button type="button" className="btn" onClick={refresh}>
                Try again
              </button>
            </div>
          ) : null}

          <div className="cal">
            <div className="cal-head">
              {DAY_NAMES.map((d) => (
                <span key={d} className="mi">{d}</span>
              ))}
            </div>
            <div className="cal-grid">
              {cells.map((d) => {
                const key = iso(d);
                const list = byDay.get(key) || [];
                return (
                  <button
                    key={key}
                    type="button"
                    className="cal-day"
                    data-outside={d.getMonth() !== cursor.getMonth() ? "" : undefined}
                    data-today={key === today ? "" : undefined}
                    data-picked={key === picked ? "" : undefined}
                    aria-pressed={key === picked}
                    onClick={() => setPicked(key)}
                  >
                    <span className="cal-date">{d.getDate()}</span>
                    {list.slice(0, 3).map((e) => (
                      <span key={e.id} className="cal-chip" title={e.title}>
                        {e.title}
                      </span>
                    ))}
                    {list.length > 3 ? (
                      <span className="cal-more mi">+{list.length - 3}</span>
                    ) : null}
                  </button>
                );
              })}
            </div>
          </div>

          <div className="lane" style={{ marginTop: "6px" }}>
            <span className="mi" data-strong>{picked}</span>
            <i />
          </div>

          {dayEvents.length === 0 ? (
            <p className="p" style={{ color: "var(--text-dim)" }}>
              Nothing on this day yet.
            </p>
          ) : (
            <ul className="cal-list">
              {dayEvents.map((e) => (
                <li key={e.id}>
                  <span className="cal-when mi">
                    {e.all_day ? "all day" : e.starts_at.slice(11)}
                  </span>
                  <span className="cal-title">{e.title}</span>
                  {e.notes ? <span className="cal-notes">{e.notes}</span> : null}
                  <button
                    type="button"
                    className="cal-del"
                    aria-label={`Delete ${e.title}`}
                    onClick={() => remove(e.id)}
                  >
                    ×
                  </button>
                </li>
              ))}
            </ul>
          )}

          <form className="cal-add" onSubmit={submit}>
            <input
              type="text"
              value={draft.title}
              placeholder={`Add to ${picked}`}
              aria-label="Event title"
              onChange={(e) => setDraft({ ...draft, title: e.target.value })}
            />
            <input
              type="time"
              value={draft.time}
              aria-label="Start time — leave blank for all day"
              onChange={(e) => setDraft({ ...draft, time: e.target.value })}
            />
            <button type="submit" className="btnp" disabled={!draft.title.trim() || saving}>
              {saving ? "Adding…" : "Add"}
            </button>
          </form>
        </div>

        <div className="page-side">
          <SideChat
            api={api}
            onSessionsChanged={onSessionsChanged}
            onAnswered={refresh}
          />
        </div>
      </div>
    </div>
  );
}
