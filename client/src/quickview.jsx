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

import {
  StrictMode,
  useCallback,
  useEffect,
  useMemo,
  useReducer,
  useRef,
  useState,
} from "react";
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
function Composer({ onSend, busy, autoFocus, started }) {
  const [value, setValue] = useState("");
  const [staged, setStaged] = useState([]);          // processed attachments
  const [dropping, setDropping] = useState(false);   // dragging-files-over flag
  const [dragCount, setDragCount] = useState(0);      // how many files are over the window
  const input = useRef(null);
  const picker = useRef(null);
  const dragDepth = useRef(0);
  const form = useRef(null);
  // Raised while `publishPeek` drives the thread's scrollTop itself, so the
  // scroll listener below can tell that scroll apart from the reader's. Without
  // it the two halves drive each other and the box shakes -- the same loop this
  // effect's counterpart in the main client had.
  const selfScroll = useRef(false);

  useEffect(() => { if (autoFocus) input.current?.focus(); }, [autoFocus]);

  /* How much of the composer is on screen, and the scroll that makes the
   * conversation ride up with it rather than disappear behind it.
   *
   * Withdrawn, only the sliver still showing is reserved; raised, the whole
   * box. Growing the reserve does not by itself move the last turn -- it adds
   * emptiness below it while the box rises over the top -- so when the reader
   * is already at the end the thread is scrolled to match. Only then: someone
   * reading back through an answer should not be dragged forward because the
   * cursor drifted near the box. */
  const publishPeek = useCallback((near) => {
    const node = form.current;
    if (!node) return;

    // `--qv-lip` is read back out of the stylesheet rather than repeated here,
    // so how much of the box survives withdrawal stays one decision made in one
    // place -- and this arithmetic stays the exact inverse of the `transform`
    // in the `.qv[data-started] .qv-composer` rule. If they disagree the thread
    // reserves the wrong amount and the last turn sits under the box.
    const lip = parseFloat(getComputedStyle(node).getPropertyValue("--qv-lip")) || 0;
    const hidden = Math.max(0, node.offsetHeight - lip) * (1 - near);
    const peek = Math.max(0, Math.round(node.offsetHeight - hidden));

    const panel = node.parentElement;
    if (!panel) return;
    if (peek === parseFloat(panel.style.getPropertyValue("--qv-peek"))) return;

    // Measured before the write, because setting the property changes the very
    // numbers this asks about.
    const thread = panel.querySelector(".qv-thread");
    const atEnd =
      thread && thread.scrollHeight - thread.scrollTop - thread.clientHeight < 120;

    panel.style.setProperty("--qv-peek", peek + "px");

    // Cleared on the next frame rather than the next line: scroll events are
    // dispatched in the rendering steps, which run before animation frame
    // callbacks, so the event this covers has been and gone by the time the
    // flag comes down.
    if (thread && atEnd) {
      selfScroll.current = true;
      thread.scrollTop = thread.scrollHeight;
      requestAnimationFrame(() => {
        selfScroll.current = false;
      });
    }
  }, []);

  const currentNear = useCallback(() => {
    const near = parseFloat(form.current?.style.getPropertyValue("--near"));
    return Number.isFinite(near) ? near : 1;
  }, []);

  /* Autosize the textarea, capped at 160 px. */
  const autosize = useCallback(() => {
    const node = input.current;
    if (!node) return;
    node.style.height = "auto";
    node.style.height = Math.min(node.scrollHeight, 160) + "px";
    publishPeek(currentNear());
  }, [publishPeek, currentNear]);

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

  /* The composer withdraws until you reach for it.
   *
   * Ported from the main client rather than reinvented -- same REACH, same
   * ONSET curve, same rules about focus and reading -- because the panel and
   * the app should not answer the cursor differently. See the long note over
   * the effect in components/Composer.jsx for why each number is what it is.
   *
   * Deliberately outside React state: this updates on every pointer move, and
   * a setState here would re-render the composer and rewrap its textarea sixty
   * times a second. */
  useEffect(() => {
    const node = form.current;
    if (!node) return undefined;

    const REACH = 130;
    const ONSET = 2;

    // A cursor is the whole mechanism, so a device without one keeps the box
    // present permanently. Reduced motion opts out for the same reason.
    const fine = window.matchMedia("(hover: hover) and (pointer: fine)");
    const calm = window.matchMedia("(prefers-reduced-motion: reduce)");
    const enabled = () => fine.matches && !calm.matches;

    let frame = 0;
    let latest = null;
    let reading = false;

    const set = (near) => {
      node.style.setProperty("--near", near.toFixed(3));
      publishPeek(near);
    };

    const measure = () => {
      frame = 0;
      if (!enabled()) return set(1);
      // Empty, the panel is the composer and there is nothing to get out of the
      // way of -- the same exemption `.screen[data-empty]` makes in the client.
      if (!node.parentElement?.hasAttribute("data-started")) return set(1);
      // Focus outranks everything: while you are typing the box stays put,
      // including through the autoscroll a streaming reply causes.
      if (node.contains(document.activeElement)) return set(1);
      if (reading) return set(0);
      if (!latest) return set(0);

      // Distance to the nearest edge, which is 0 anywhere inside it. The centre
      // would make a wide composer feel far away at its own left edge.
      const rect = node.getBoundingClientRect();
      const dx = Math.max(rect.left - latest.x, 0, latest.x - rect.right);
      const dy = Math.max(rect.top - latest.y, 0, latest.y - rect.bottom);
      const closeness = Math.max(0, Math.min(1, 1 - Math.hypot(dx, dy) / REACH));
      set(Math.pow(closeness, ONSET));
    };

    const schedule = () => {
      if (frame) return;
      frame = requestAnimationFrame(measure);
    };

    const onMove = (event) => {
      latest = { x: event.clientX, y: event.clientY };
      // Moving the pointer is how you ask for it back; the scroll only
      // suppresses it until you next show an interest.
      reading = false;
      schedule();
    };

    // Scroll does not bubble, so this is a capture-phase listener on the
    // document rather than one bound to the thread.
    const onScroll = () => {
      if (selfScroll.current) return; // our own scroll, not the reader's
      if (reading) return;
      reading = true;
      schedule();
    };

    // The pointer leaving the window reads as "gone", not as "last seen at the
    // edge" -- which matters more here than in the app, since the panel is a
    // small window with a lot of not-the-panel around it.
    const onLeave = () => {
      latest = null;
      schedule();
    };

    window.addEventListener("pointermove", onMove, { passive: true });
    document.addEventListener("scroll", onScroll, { capture: true, passive: true });
    document.addEventListener("pointerleave", onLeave);
    node.addEventListener("focusin", schedule);
    node.addEventListener("focusout", schedule);
    fine.addEventListener("change", schedule);
    calm.addEventListener("change", schedule);

    measure();
    return () => {
      if (frame) cancelAnimationFrame(frame);
      window.removeEventListener("pointermove", onMove);
      document.removeEventListener("scroll", onScroll, { capture: true });
      document.removeEventListener("pointerleave", onLeave);
      node.removeEventListener("focusin", schedule);
      node.removeEventListener("focusout", schedule);
      fine.removeEventListener("change", schedule);
      calm.removeEventListener("change", schedule);
      node.style.removeProperty("--near");
    };
    // `started` is a dependency because `measure` reads it: the panel gaining a
    // thread is exactly when the box becomes something that can withdraw, and
    // nothing else would tell this effect that happened.
  }, [publishPeek, started]);

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
      ref={form}
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
/* How many tiles a fan shows before it gives up and counts. Four is where a
 * 62px tile overlapped by 20 stops reading as separate photographs. */
const FAN = 4;

/* One group of files: a fanned stack, and its name underneath.
 *
 * `items` are `{ name, src, mime }`. `src` is optional -- a file with no
 * picture available draws its name in a tile-shaped card instead of a blank,
 * so a mixed group still lines up and nothing silently disappears. */
function FileGroup({ label, items }) {
  if (!items.length) return null;
  const shown = items.slice(0, FAN);
  const rest = items.length - shown.length;

  return (
    <div className="qv-file-group">
      <div className="qv-file-stack">
        {shown.map((item, i) =>
          item.src ? (
            <img
              key={item.name + i}
              className="qv-file-tile"
              src={item.src}
              alt={item.name}
              title={item.name}
            />
          ) : (
            <span key={item.name + i} className="qv-file-doc" title={item.name}>
              {item.name}
            </span>
          ),
        )}
        {rest > 0 ? <span className="qv-file-more">+{rest}</span> : null}
      </div>
      <span className="qv-file-caption" title={label}>{label}</span>
    </div>
  );
}

/* Files attached to a turn, grouped for display.
 *
 * Pictures fan into one stack captioned by their count, because that is the
 * outcome you are looking at -- "six photos" -- rather than six separate
 * facts. Anything else keeps its own name, since a document is identified by
 * what it is called and a thumbnail would say nothing about it. */
function TurnFiles({ attachments }) {
  const groups = useMemo(() => {
    const items = attachments || [];
    if (!items.length) return [];

    const images = items
      .filter((a) => a.mime?.startsWith("image/"))
      .map((a) => ({ name: a.name, src: `data:${a.mime};base64,${a.data}`, mime: a.mime }));
    const others = items.filter((a) => !a.mime?.startsWith("image/"));

    const out = [];
    if (images.length) {
      out.push({
        key: "images",
        label: images.length === 1 ? images[0].name : `${images.length} images`,
        items: images,
      });
    }
    for (const file of others) {
      out.push({ key: file.name, label: file.name, items: [{ name: file.name }] });
    }
    return out;
  }, [attachments]);

  if (!groups.length) return null;

  return (
    <div className="qv-files">
      {groups.map((g) => (
        <FileGroup key={g.key} label={g.label} items={g.items} />
      ))}
    </div>
  );
}

/* -- The reveal -----------------------------------------------------------
 *
 * Text fades in as it arrives, so a reply looks like it is being written
 * rather than pasted in.
 *
 * The unit of the fade is the chunk the server sent, not the character, and
 * each chunk keeps its own element for the length of its animation. That is
 * the whole trick: rendering the growing string as one node and animating
 * *that* restarts the fade on every delta, so with tokens landing every 50ms
 * and a 400ms fade nothing ever finishes fading and the whole reply sits
 * permanently half-visible.
 *
 * Chunks are retired into a plain string once there are enough of them that
 * the oldest have certainly finished, so a long answer is a handful of
 * animating spans and one settled text node rather than six hundred spans.
 */

/* How many chunks stay animatable. At a chunk every ~50ms, twelve covers about
   600ms -- comfortably longer than the fade, so nothing is ever retired
   mid-animation. */
const FADE_CHUNKS = 12;

function FadingText({ text }) {
  // Derived from props and cached across renders rather than held in state:
  // this is recomputed from `text` alone, and putting it in state would mean a
  // second render for every delta.
  const settled = useRef("");
  const chunks = useRef([]);
  const seen = useRef(0);

  if (text.length > seen.current) {
    // Only what is new gets an element of its own.
    chunks.current = [...chunks.current, { key: seen.current, text: text.slice(seen.current) }];
    seen.current = text.length;
    if (chunks.current.length > FADE_CHUNKS) {
      const retired = chunks.current.slice(0, chunks.current.length - FADE_CHUNKS);
      settled.current += retired.map((c) => c.text).join("");
      chunks.current = chunks.current.slice(retired.length);
    }
  } else if (text.length < seen.current || !text.startsWith(settled.current)) {
    // A different message in the same component -- a retry, or a reset. Start
    // over rather than diffing, and show it whole: it is not new text arriving,
    // so there is nothing to fade.
    settled.current = text;
    chunks.current = [];
    seen.current = text.length;
  }

  return (
    <>
      {settled.current}
      {chunks.current.map((c) => (
        <span key={c.key} className="qv-fade">{c.text}</span>
      ))}
    </>
  );
}

function Turn({ role, content, streaming, attachments }) {
  return (
    <div className="qv-turn" data-role={role}>
      {/* Only the assistant decodes. What you typed yourself does not need to
          be tuned in, and re-rendering it as noise would read as a bug. */}
      {role === "assistant" && content ? (
        <FadingText text={content} />
      ) : (
        content || (streaming ? <span className="qv-waiting">Thinking</span> : "")
      )}
      <TurnFiles attachments={attachments} />
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
        // Belt and braces on the turn's streaming flag. `done` clears it above
        // and that is the normal path, but a stream that simply ends -- a
        // dropped connection, a server restart mid-reply -- finishes the loop
        // with no event and no exception, and the turn would stay marked as
        // streaming forever. That used to cost nothing, since the flag only
        // drove a placeholder that content had already replaced; now it holds
        // the decode window open, so the last few characters would sit on
        // screen as noise that never resolves. The stream being over is the
        // fact this flag is about, and this is the one place that always knows.
        setTurns((was) =>
          was.map((t, i) => (i === was.length - 1 ? { ...t, streaming: false } : t)),
        );
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

      <Composer onSend={send} busy={busy} autoFocus={focusToken} started={started} />
    </div>
  );
}

createRoot(document.getElementById("quickview")).render(
  <StrictMode><QuickView /></StrictMode>,
);
