/* QuickView: a temporary conversation, summoned over whatever you were doing.
 *
 * Its own entry point rather than a route in the main app. The panel is 640px
 * of frosted glass that opens on a keystroke and is dismissed seconds later, so
 * loading the rail, the router and the Skills page first -- none of which it can
 * show -- would put the whole app's startup between the key and the caret.
 *
 * Two states, and the empty one is the point. With nothing said yet it is a
 * composer and nothing else, the way Spotlight is a text field: no chrome, no
 * header, no empty transcript. The panel only becomes a panel once there is a
 * conversation to hold.
 */

import { StrictMode, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";

import { createApi, readEvents } from "./lib/api";
import "./quickview.css";

const TOKEN_KEY = "unified-llm-token";

/* Talking to the shell.
 *
 * Imported dynamically rather than at the top of the file, because this page is
 * also servable in a plain browser -- `npm run dev`, or the server's own
 * /quickview.html -- where there is no shell and the import would throw on load.
 * A dynamic import lets the panel degrade to "a page you can look at" instead of
 * a blank screen. (`withGlobalTauri` is off, so there is no window.__TAURI__ to
 * feature-detect; the package is the only route in.)
 */
async function shell() {
  try {
    const [event, window_] = await Promise.all([
      import("@tauri-apps/api/event"),
      import("@tauri-apps/api/window"),
    ]);
    return { event, window: window_ };
  } catch {
    return null;
  }
}

/* The shell tells the page when it has been summoned. */
function useSummoned(onOpen) {
  useEffect(() => {
    let stop = () => {};
    let live = true;
    shell().then((api) => {
      if (!api || !live) return;
      api.event.listen("quickview://opened", onOpen).then((off) => {
        if (live) stop = off;
        else off();
      });
    });
    return () => {
      live = false;
      stop();
    };
  }, [onOpen]);
}

function Composer({ onSend, busy, autoFocus }) {
  const [value, setValue] = useState("");
  const input = useRef(null);

  useEffect(() => {
    if (autoFocus) input.current?.focus();
  }, [autoFocus]);

  // Grows with the text, up to a point. Past that it scrolls: a panel that
  // keeps growing would eventually be the size of the screen it is floating
  // over, which is the one thing it must never be.
  const autosize = () => {
    const node = input.current;
    if (!node) return;
    node.style.height = "auto";
    node.style.height = Math.min(node.scrollHeight, 160) + "px";
  };

  const submit = (event) => {
    event?.preventDefault();
    const text = value.trim();
    if (!text || busy) return;
    setValue("");
    requestAnimationFrame(autosize);
    onSend(text);
  };

  return (
    <form className="qv-composer" onSubmit={submit}>
      <textarea
        ref={input}
        rows={1}
        value={value}
        placeholder="Ask me. Task me."
        aria-label="Ask or task"
        disabled={busy}
        onChange={(e) => {
          setValue(e.target.value);
          autosize();
        }}
        onKeyDown={(e) => {
          // Enter sends. A panel opened for one question should not need a
          // modifier to ask it; Shift+Enter is there for the rare long one.
          if (e.key === "Enter" && !e.shiftKey) submit(e);
        }}
      />
      <div className="qv-composer-row">
        <button type="button" className="qv-round" aria-label="Attach a file" disabled>
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <path d="M21 11.5l-8.6 8.6a5 5 0 01-7-7l8.6-8.6a3.3 3.3 0 014.7 4.7l-8.6 8.6a1.7 1.7 0 01-2.3-2.3l7.9-7.9" />
          </svg>
        </button>
        <span className="qv-spacer" />
        <button
          type="submit"
          className="qv-round qv-send"
          aria-label="Send"
          disabled={busy || !value.trim()}
        >
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <path d="M12 19V5M6 11l6-6 6 6" />
          </svg>
        </button>
      </div>
    </form>
  );
}

function Turn({ role, content, streaming }) {
  return (
    <div className="qv-turn" data-role={role}>
      {content || (streaming ? "…" : "")}
    </div>
  );
}

function QuickView() {
  const [token] = useState(() => localStorage.getItem(TOKEN_KEY) || "");
  const [turns, setTurns] = useState([]);
  const [busy, setBusy] = useState(false);
  const [title, setTitle] = useState("");
  const [error, setError] = useState(null);
  const [focusToken, setFocusToken] = useState(0);
  const session = useRef(null);
  const thread = useRef(null);

  const api = useMemo(() => (token ? createApi(token) : null), [token]);

  /* Summoned means a clean slate. The panel is for a question you are having
   * *now*; re-opening it onto the last one would make it a window, and there is
   * already a window. */
  const reset = useCallback(() => {
    setTurns([]);
    setTitle("");
    setError(null);
    session.current = null;
    setFocusToken((n) => n + 1);
  }, []);

  useSummoned(reset);

  useEffect(() => {
    setFocusToken((n) => n + 1);
  }, []);

  // Esc puts it away. Handled here because the keystroke lands in the page, and
  // the shell has no way to see it first.
  useEffect(() => {
    const onKey = (event) => {
      if (event.key !== "Escape") return;
      shell().then((api) => api?.window.getCurrentWindow().hide());
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  useEffect(() => {
    const node = thread.current;
    if (node) node.scrollTop = node.scrollHeight;
  }, [turns]);

  const send = useCallback(
    async (text) => {
      if (!api) {
        setError("This device has no access token yet. Open Courier and sign in first.");
        return;
      }
      setTurns((was) => [
        ...was,
        { key: `u${was.length}`, role: "user", content: text },
        { key: `a${was.length}`, role: "assistant", content: "", streaming: true },
      ]);
      if (!title) setTitle(text.length > 48 ? text.slice(0, 47) + "…" : text);
      setBusy(true);
      setError(null);

      try {
        const response = await api.chat(text, session.current);
        for await (const { event, data } of readEvents(response)) {
          if (event === "session") session.current = data.session_id;
          if (event === "delta") {
            setTurns((was) =>
              was.map((t, i) =>
                i === was.length - 1 ? { ...t, content: t.content + data.text } : t,
              ),
            );
          }
          if (event === "error") setError(data.message);
          if (event === "done" || event === "error") {
            setTurns((was) =>
              was.map((t, i) => (i === was.length - 1 ? { ...t, streaming: false } : t)),
            );
          }
        }
      } catch (problem) {
        setError(problem.message || String(problem));
        setTurns((was) =>
          was.map((t, i) => (i === was.length - 1 ? { ...t, streaming: false } : t)),
        );
      } finally {
        setBusy(false);
        setFocusToken((n) => n + 1);
      }
    },
    [api, title],
  );

  const started = turns.length > 0;

  return (
    <div className="qv" data-started={started ? "" : undefined}>
      {started ? (
        <div className="qv-thread" ref={thread}>
          {turns.map((t) => (
            <Turn key={t.key} role={t.role} content={t.content} streaming={t.streaming} />
          ))}
          {error ? <div className="qv-error">{error}</div> : null}
        </div>
      ) : null}

      <Composer onSend={send} busy={busy} autoFocus={focusToken} />
    </div>
  );
}

createRoot(document.getElementById("quickview")).render(
  <StrictMode>
    <QuickView />
  </StrictMode>,
);
