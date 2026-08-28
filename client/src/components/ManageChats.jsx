import { useMemo, useState } from "react";

/* Bulk conversation management, on the Settings page.
 *
 * The rail deletes one conversation at a time behind a `confirm()`, which is
 * right for one. Clearing out twenty is a different job, and doing it twenty
 * times with a browser dialog in between is the reason nobody ever does it.
 *
 * The gate is typing the word rather than pressing a second button: this is
 * the one destructive action in the app that cannot be undone -- messages,
 * attachments and stored reasoning all cascade -- and a second button is just
 * the first button twice.
 */
export function ManageChats({ sessions, onDeleted, api }) {
  const [picked, setPicked] = useState(() => new Set());
  const [query, setQuery] = useState("");
  const [confirming, setConfirming] = useState(false);
  const [typed, setTyped] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  const shown = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return sessions;
    return sessions.filter((s) =>
      (s.title || "Untitled").toLowerCase().includes(needle),
    );
  }, [sessions, query]);

  // Only what is both picked and visible: a filter narrowing the list must not
  // quietly leave a selection behind that the button then deletes.
  const targets = useMemo(
    () => shown.filter((s) => picked.has(s.id)).map((s) => s.id),
    [shown, picked],
  );

  const allShownPicked = shown.length > 0 && targets.length === shown.length;

  const toggle = (id) =>
    setPicked((was) => {
      const next = new Set(was);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });

  const toggleAll = () =>
    setPicked((was) => {
      const next = new Set(was);
      if (allShownPicked) shown.forEach((s) => next.delete(s.id));
      else shown.forEach((s) => next.add(s.id));
      return next;
    });

  const armed = typed.trim().toLowerCase() === "delete";

  const run = async () => {
    if (!armed || busy) return;
    setBusy(true);
    try {
      const result = await api.deleteSessions(targets);
      setPicked(new Set());
      setConfirming(false);
      setTyped("");
      setError(null);
      await onDeleted(result);
    } catch (problem) {
      setError(problem.message || String(problem));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="manage">
      <div className="lane">
        <span className="mi" data-strong>
          Conversations · {sessions.length}
        </span>
        <i />
        {shown.length ? (
          <button type="button" className="mi" onClick={toggleAll}>
            {allShownPicked ? "Clear selection" : "Select all"}
          </button>
        ) : null}
      </div>

      {sessions.length > 6 ? (
        <div className="search">
          <input
            type="search"
            value={query}
            placeholder="Filter conversations"
            aria-label="Filter conversations"
            onChange={(event) => setQuery(event.target.value)}
          />
        </div>
      ) : null}

      {sessions.length === 0 ? (
        <p className="p" style={{ color: "var(--text-dim)" }}>
          Nothing to manage yet.
        </p>
      ) : (
        <ul className="manage-list">
          {shown.map((session) => (
            <li key={session.id}>
              <label className="manage-row">
                <input
                  type="checkbox"
                  checked={picked.has(session.id)}
                  onChange={() => toggle(session.id)}
                />
                <span className="manage-title">{session.title || "Untitled"}</span>
                <span className="mi">
                  {session.message_count === 1
                    ? "1 message"
                    : `${session.message_count ?? 0} messages`}
                </span>
              </label>
            </li>
          ))}
        </ul>
      )}

      {error ? (
        <div className="callout" data-tint="ochre">
          <div style={{ flex: 1 }}>
            <span className="h" style={{ fontSize: "var(--t-lg)" }}>
              Could not delete
            </span>
            <p style={{ margin: "7px 0 0", color: "var(--text-dim)", fontSize: "var(--t-sm)" }}>
              {error}
            </p>
          </div>
        </div>
      ) : null}

      {targets.length > 0 && !confirming ? (
        <div className="manage-actions">
          <button type="button" className="btnp" onClick={() => setConfirming(true)}>
            Delete {targets.length === 1 ? "1 conversation" : `${targets.length} conversations`}
          </button>
        </div>
      ) : null}

      {confirming ? (
        <div className="callout" data-tint="ochre">
          <div style={{ flex: 1, display: "flex", flexDirection: "column", gap: "10px" }}>
            <span className="h" style={{ fontSize: "var(--t-lg)" }}>
              Delete {targets.length === 1 ? "this conversation" : `these ${targets.length} conversations`}?
            </span>
            <p style={{ margin: 0, color: "var(--text-dim)", fontSize: "var(--t-sm)", lineHeight: 1.6 }}>
              Every message, attachment and stored reasoning trace goes with
              them. This cannot be undone — there is no trash to recover from.
            </p>
            {/* Not a second OK button. Typing the word is the only part of this
                flow that a reflex cannot get through. */}
            <label className="manage-confirm">
              <span className="mi">Type “delete” to confirm</span>
              <input
                type="text"
                value={typed}
                autoFocus
                autoComplete="off"
                spellCheck="false"
                aria-label="Type delete to confirm"
                onChange={(event) => setTyped(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" && armed) {
                    event.preventDefault();
                    run();
                  }
                }}
              />
            </label>
            <div className="manage-actions">
              <button
                type="button"
                className="btnp"
                disabled={!armed || busy}
                onClick={run}
              >
                {busy ? "Deleting…" : "Delete permanently"}
              </button>
              <button
                type="button"
                className="btn"
                disabled={busy}
                onClick={() => {
                  setConfirming(false);
                  setTyped("");
                }}
              >
                Cancel
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
