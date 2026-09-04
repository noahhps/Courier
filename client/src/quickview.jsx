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

// -- Attachment helpers ---------------------------------------------------
/** Strip the `data:…;base64,` prefix so the server validates the payload. */
function extractBase64(dataUrl) {
  const comma = dataUrl.indexOf(",");
  return comma > -1 ? dataUrl.slice(comma + 1) : dataUrl;
}

/** Read a raw File as base64 and return { name, mime, data }. */
async function fileToAttachment(file) {
  const data = await new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.readAsDataURL(file);
    reader.onload = () => resolve(reader.result);
    reader.onerror = reject;
  });
  return { name: file.name, mime: file.type, data: extractBase64(data) };
}

/** Format a byte count to KB or MB for display. */
function formatBytes(bytes) {
  if (bytes < 1024) return bytes + " B";
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + " KB";
  return (bytes / (1024 * 1024)).toFixed(1) + " MB";
}

// -- Staged attachment pills (before sending) -----------------------------
function StagedAttachments({ items, onRemove }) {
  if (!items.length) return null;

  return (
    <ul className="qv-staged">
      {items.map((item, i) => (
        <li key={item.name + item.mime + i} className="qv-staged-item">
          {item.preview ? (
            <img className="qv-staged-thumb" src={item.preview} alt="" />
          ) : null}
          <span className="qv-staged-name">{item.name}</span>
          <span className="qv-staged-size">{formatBytes(item.size ?? 0)}</span>
          <button
            type="button"
            className="qv-staged-remove"
            aria-label={`Remove ${item.name}`}
            onClick={() => onRemove(item)}
          >
            ×
          </button>
        </li>
      ))}
    </ul>
  );
}

/* -- Drop zone overlay, shown while files are over the window ---------------
 *
 * A count and a label, and no thumbnails -- not a simplification, a limit. A
 * drag in progress exposes only `dataTransfer.items`: how many things there
 * are and what kind they claim to be. The `files` themselves are withheld
 * until the drop, because until then the page has not been given them, and a
 * page that could read a file merely because the cursor passed over it would
 * be a way to steal one by hovering.
 *
 * So there is nothing to make a preview from yet. The count is honest about
 * what is known at this moment. */
function DropZone({ count }) {
  if (!count) return null;

  return (
    <div className="qv-drop-zone">
      {count > 1 ? <span className="qv-drop-count">{count} files</span> : null}
      <span className="qv-drop-label">Drop {count === 1 ? "the file" : "them"} here</span>
    </div>
  );
}

const TOKEN_KEY = "unified-llm-token";

/* Talking to the shell. */
async function shell() {
  // The import is not the test. `@tauri-apps/api` is an ordinary dependency
  // that Vite bundles into the browser build too, so it resolves everywhere and
  // would answer "yes" on a page with no shell behind it -- where
  // `getCurrentWindow()` then throws into an unhandled rejection. The injected
  // IPC bridge is the only honest signal.
  if (typeof window.__TAURI_INTERNALS__ === "undefined") return null;
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

// ------------------------------------------------------------------------
function Composer({ onSend, busy, autoFocus }) {
  const [value, setValue] = useState("");
  const [staged, setStaged] = useState([]);          // processed attachments
  const [dropping, setDropping] = useState(false);   // dragging-files-over flag
  const [dragCount, setDragCount] = useState(0);      // how many files are over the window
  const input = useRef(null);
  const picker = useRef(null);
  const dragDepth = useRef(0);

  useEffect(() => { if (autoFocus) input.current?.focus(); }, [autoFocus]);

  /* Autosize the textarea, capped at 160 px. */
  const autosize = useCallback(() => {
    const node = input.current;
    if (!node) return;
    node.style.height = "auto";
    node.style.height = Math.min(node.scrollHeight, 160) + "px";
  }, []);

  /* File-picker callback. */
  const handlePickerChange = useCallback(() => {
    const files = [...picker.current.files];
    picker.current.value = ""; // reset so picking the same file again fires
    if (!files.length) return;
    Promise.all(files.map(fileToAttachment)).then((items) =>
      setStaged((prev) => [
        ...prev,
        ...items.map((a, i) => ({
          ...a,
          size: files[i]?.size ?? 0,
          preview: a.mime?.startsWith("image/") ? URL.createObjectURL(files[i]) : null,
        })),
      ]),
    );
  }, []);

  /* Remove one staged item (revokes its object URL). */
  const unstage = useCallback((item) => {
    setStaged((prev) => {
      if (item?.preview) URL.revokeObjectURL(item.preview);
      return prev.filter((it) => it !== item);
    });
  }, []);

  /*
   * Window-level drag-and-drop listeners.
   * Depth-counted because `dragenter`/`dragleave` fire for every child node
   * crossed while moving the cursor.
   */
  useEffect(() => {
    const onEnter = (event) => {
      if (![...(event.dataTransfer?.types || [])].includes("Files")) return;
      dragDepth.current += 1;
      setDropping(true);
      // How many, from `items` -- which is readable mid-drag. `files` is not:
      // it is empty until the drop event, by design, so reading it here was
      // always going to give nothing.
      const items = [...(event.dataTransfer.items || [])];
      setDragCount(items.filter((i) => i.kind === "file").length || items.length);
    };

    const onLeave = () => {
      dragDepth.current = Math.max(0, dragDepth.current - 1);
      if (!dragDepth.current) {
        setDropping(false);
        setDragCount(0);
      }
    };

    const onOver = (event) => {
      event.preventDefault(); // must prevent for drop to work; also keeps cursor at hover rect top.
    };

    const onDrop = async (event) => {
      event.preventDefault();
      dragDepth.current = 0;
      setDropping(false);
      setDragCount(0);

      // Read from the event, and only here. This is the one moment the files
      // are actually handed over -- the previous version compared against a
      // list captured on `dragenter`, which is always empty, so every drop
      // returned early and nothing was ever staged.
      const dropped = [...(event.dataTransfer?.files || [])];
      if (!dropped.length) return;

      const processed = await Promise.all(dropped.map(fileToAttachment));
      setStaged((prev) => [
        ...prev,
        ...processed.map((a, i) => ({
          ...a,
          size: dropped[i]?.size ?? 0,
          preview: a.mime?.startsWith("image/") ? URL.createObjectURL(dropped[i]) : null,
        })),
      ]);
    };

    window.addEventListener("dragenter", onEnter, { passive: true });
    window.addEventListener("dragleave", onLeave, { passive: true });
    window.addEventListener("dragover", onOver, { passive: false });
    window.addEventListener("drop", onDrop);
    return () => {
      window.removeEventListener("dragenter", onEnter);
      window.removeEventListener("dragleave", onLeave);
      window.removeEventListener("dragover", onOver);
      window.removeEventListener("drop", onDrop);
    };
  }, []);

  /* Clean up all object URLs on unmount. */
  useEffect(
    () => () => staged.forEach((i) => i.preview && URL.revokeObjectURL(i.preview)),
    [],     // Runs once on remount because the list is fresh each Composer instance.
  );

  /* Form submission – sends text + attachments to the parent via `onSend`. */
  const handleSubmit = async (event) => {
    event?.preventDefault();
    if (!value.trim() && !staged.length) return; // nothing to send
    if (busy) return; // guard: hotkeys still fire

    const text = value.trim();
    setValue("");
    // Strip base64 from the staging objects before sending to api.js.
    const attachments = staged.map(({ name, mime, data }) => ({ name, mime, data }));
    setStaged([]);
    requestAnimationFrame(autosize);
    onSend(text, attachments);
  };

  return (
    <form
      className="qv-composer"
      onSubmit={handleSubmit}
      data-dropping={dropping ? "" : undefined}
    >
      {/* Drop-zone overlay appears while dragging. */}
      {dropping ? <DropZone count={dragCount} /> : null}

      <textarea
        ref={input}
        rows={1}
        value={value}
        placeholder="Ask me. Task me."
        aria-label="Ask or task"
        disabled={busy}
        onChange={(e) => { setValue(e.target.value); autosize(); }}
        onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) handleSubmit(e); }}
      />

      {/* Staged attachment pills */}
      <StagedAttachments items={staged} onRemove={unstage} />

      {/* Action row: attach button + spacer + send */}
      <div className="qv-composer-row">
        <input
          ref={picker}
          type="file"
          multiple
          hidden
          onChange={handlePickerChange}
        />

        <button
          type="button"
          className="qv-round qv-attach"
          aria-label="Attach a file"
          onClick={() => picker.current.click()}
        >
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <path d="M21 11.5l-8.6 8.6a5 5 0 01-7-7l8.6-8.6a3.3 3.3 0 014.7 4.7l-8.6 8.6a1.7 1.7 0 01-2.3-2.3l7.9-7.9" />
          </svg>
        </button>

        <span className="qv-spacer" />

        <button
          type="submit"
          className="qv-round qv-send"
          aria-label="Send"
          disabled={busy || (!value.trim() && !staged.length)}
        >
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <path d="M12 19V5M6 11l6-6 6 6" />
          </svg>
        </button>
      </div>
    </form>
  );
}

// ------------------------------------------------------------------------
function Turn({ role, content, streaming, attachments }) {
  const attachmentEls = (attachments || []).map((a) => {
    if (a.mime?.startsWith("image/")) {
      return (
        <img
          key={a.name}
          src={`data:${a.mime};base64,${a.data}`}
          alt={a.name}
          className="qv-attach-thumb"
        />
      );
    }
    return <span key={a.name} className="qv-attach-chip">{a.name}</span>;
  });

  return (
    <div className="qv-turn" data-role={role}>
      {content || (streaming ? "…" : "")}
      {role === "user" && attachmentEls.length > 0 && (
        <div className="qv-attachments">{attachmentEls}</div>
      )}
    </div>
  );
}

// ------------------------------------------------------------------------
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

  /* Summoned means a clean slate. */
  const reset = useCallback(() => {
    setTurns([]);
    setTitle("");
    setError(null);
    session.current = null;
    setFocusToken((n) => n + 1);
  }, []);

  useSummoned(reset);

  useEffect(() => setFocusToken((n) => n + 1), []);

  /* Esc puts it away. */
  useEffect(() => {
    const onKey = (event) => {
      if (event.key !== "Escape") return;
      shell().then((api) => api?.window.getCurrentWindow().hide());
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  useEffect(() => { const node = thread.current; if (node) node.scrollTop = node.scrollHeight; }, [turns]);

  /* Send a text/attachment turn to the server and stream back. */
  const send = useCallback(
    async (text, attachments = []) => {
      if (!api) {
        setError("This device has no access token yet. Open Courier and sign in first.");
        return;
      }

      // Title heuristic: use text when available, otherwise a file count.
      const displayText = text || attachments.length > 0
        ? (text?.length > 48 ? text.slice(0, 47) + "thinking" : text || `${attachments.length} file${attachments.length > 1 ? "s" : ""}`)
        : null;
      if (!title && displayText) setTitle(displayText);

      setTurns((was) => [
        ...was,
        { key: `u${was.length}`, role: "user", content: text, attachments },
        { key: `a${was.length}`, role: "assistant", content: "", streaming: true },
      ]);
      setBusy(true);
      setError(null);

      try {
        const response = await api.chat(text || null, session.current, attachments);
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
            <Turn key={t.key} role={t.role} content={t.content} streaming={t.streaming} attachments={t.attachments} />
          ))}
          {error ? <div className="qv-error">{error}</div> : null}
        </div>
      ) : null}

      <Composer onSend={send} busy={busy} autoFocus={focusToken} />
    </div>
  );
}

createRoot(document.getElementById("quickview")).render(
  <StrictMode><QuickView /></StrictMode>,
);
