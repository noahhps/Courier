import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, UnauthorizedError, readEvents } from "../lib/api";
import { readFiles } from "../lib/files";

// Local-only identity for list keys. Server message ids exist but arrive after
// a bubble is already on screen, so the UI needs its own handle from the start.
let nextKey = 0;
const message = (role, content = "", extra = {}) => ({
  key: ++nextKey,
  role,
  content,
  ...extra,
});

/**
 * The conversation: which session is open, what is in it, and the turn in
 * flight. Everything durable lives on the server -- this is a view of it.
 */
export function useChat(api, { onSessionsChanged, provider = null }) {
  const [messages, setMessages] = useState([]);
  const [sessionId, setSessionId] = useState(null);
  // Mirror of the above, readable from a callback that outlived its render --
  // "is this still the open conversation?" cannot be answered by a closure.
  const openRef = useRef(null);
  openRef.current = sessionId;
  const [title, setTitle] = useState("New conversation");
  const [streaming, setStreaming] = useState(false);
  const [badge, setBadge] = useState(null);
  // Bumped when the thread should jump to the bottom regardless of where the
  // reader had scrolled: opening a session, sending a message.
  const [scrollToken, setScrollToken] = useState(0);
  const jumpToEnd = useCallback(() => setScrollToken((n) => n + 1), []);

  // Deltas arrive faster than the screen refreshes. Coalescing them into one
  // state update per frame keeps a long answer from re-rendering the markdown
  // sixty-plus times a second for no visible gain.
  //
  // Reasoning rides the same buffer rather than a second one: it arrives token
  // by token like the answer does, faster and usually in far greater volume,
  // so it is the stream that needs the batching most.
  const frame = useRef(0);
  const timer = useRef(0);
  const pending = useRef({ key: null, text: "", reasoning: "" });
  // The turn in flight, so it can be called off from outside `send`.
  const inFlight = useRef(null);

  const flush = useCallback(() => {
    const { key, text, reasoning } = pending.current;
    if (key === null) return;
    setMessages((prev) =>
      prev.map((m) =>
        m.key === key
          ? { ...m, content: text, reasoning, thinking: !text && m.thinking }
          : m,
      ),
    );
  }, []);

  const clearPendingFlush = useCallback(() => {
    if (frame.current) {
      cancelAnimationFrame(frame.current);
      frame.current = 0;
    }
    if (timer.current) {
      clearTimeout(timer.current);
      timer.current = 0;
    }
  }, []);

  const schedule = useCallback(() => {
    if (frame.current || timer.current) return;
    const run = () => {clearPendingFlush(); flush();};
    frame.current = requestAnimationFrame(run);

    timer.current = setTimeout(run, 250);
  }, [clearPendingFlush, flush]);

  const settle = useCallback(() => {
    clearPendingFlush();
    flush();
    pending.current = { key: null, text: "", reasoning: "" };
  }, [clearPendingFlush, flush]);

  // The cleanup is `clearPendingFlush` itself -- an extra arrow here would
  // return the function on unmount instead of calling it, and the pending
  // frame and timer would outlive the component.
  useEffect(() => clearPendingFlush, [clearPendingFlush]);

  // -- navigation -----------------------------------------------------------

  const openSession = useCallback(
    async (id) => {
      const data = await api.getSession(id);
      setSessionId(id);
      setTitle(data.session.title || "Untitled");
      setMessages(
        data.messages
          // A turn can be nothing but a dropped image, so a message with no
          // text but with files still belongs on screen.
          .filter((m) => m.role !== "system" && (m.content || m.attachments?.length))
          // The working comes back with the turn now, so a reopened
          // conversation still shows what was thought and what was called.
          // `skills` is already a list from the server; `|| undefined` so an
          // empty one leaves the trace unrendered rather than drawing an empty
          // frame around nothing.
          .map((m) =>
            message(m.role, m.content, {
              attachments: m.attachments,
              reasoning: m.reasoning || undefined,
              skills: m.skills?.length ? m.skills : undefined,
            }),
          ),
      );
      jumpToEnd();
      onSessionsChanged();
    },
    [api, jumpToEnd, onSessionsChanged],
  );

  const startNew = useCallback(() => {
    setSessionId(null);
    setTitle("New conversation");
    setMessages([]);
    setBadge(null);
  }, []);

  // -- the turn -------------------------------------------------------------

  const send = useCallback(
    async (text, files = [], thinkingLevel = null) => {
      if (streaming || (!text.trim() && !files.length)) return;
      setStreaming(true);

      const controller = new AbortController();
      inFlight.current = controller;

      const answer = message("assistant", "", { streaming: true });
      const asked = message("user", text);
      setMessages((prev) => [...prev, asked, answer]);
      jumpToEnd();

      let active = sessionId;
      let content = "";
      let reasoning = "";
      // What the model reached for this turn, in order. Kept on the answer so
      // it survives a re-render and stays with the reply it belongs to.
      let skills = [];
      let announced = false;
      pending.current = { key: answer.key, text: "", reasoning: "" };

      // Whatever arrived before the failure is kept; a bubble that never got a
      // single token is not worth leaving behind above the error.
      const fail = (text) =>
        setMessages((prev) => [
          ...(content ? prev : prev.filter((m) => m.key !== answer.key)),
          message("error", text),
        ]);

      try {
        // Read before the request so the bubble can show the picture straight
        // away: the same base64 that goes up is what the preview renders from,
        // which costs nothing extra and avoids an object URL to keep track of.
        const attachments = files.length ? await readFiles(files) : [];
        if (attachments.length) {
          setMessages((prev) =>
            prev.map((m) => (m.key === asked.key ? { ...m, attachments } : m)),
          );
          jumpToEnd();
        }

        const response = await api.chat(
          text,
          sessionId,
          attachments,
          thinkingLevel,
          provider,
          controller.signal,
        );

        for await (const { event, data } of readEvents(response)) {
          if (event === "session") {
            active = data.session_id;
            setSessionId(active);
          } else if (event === "meta") {
            // Named rather than labelled "cloud": there are two cloud backends
            // now, and which one answered is what the badge is for. The model
            // alone is enough when it was the local one -- that is the case
            // with nothing to warn about.
            setBadge({
              text:
                data.source === "fallback"
                  ? data.provider + " · " + data.model
                  : data.model,
              tone: data.source === "fallback" ? "warn" : null,
            });
          } else if (event === "thinking") {
            if (!announced) {
              announced = true;
              // Once, so there is something on screen in the gap before the
              // first reasoning token lands.
              setMessages((prev) =>
                prev.map((m) => (m.key === answer.key ? { ...m, thinking: true } : m)),
              );
            }
            reasoning += data.text || "";
            pending.current = { key: answer.key, text: content, reasoning };
            schedule();
          } else if (event === "tool_call") {
            // Appended optimistically: the result arrives as a second frame,
            // and until it does the row shows as still running.
            skills = [...skills, { name: data.name, arguments: data.arguments }];
            setMessages((prev) =>
              prev.map((m) => (m.key === answer.key ? { ...m, skills } : m)),
            );
            jumpToEnd();
          } else if (event === "tool_result") {
            // Fills in the last unanswered row for that skill rather than the
            // last row overall: two skills can be called in one round.
            let filled = false;
            skills = [...skills]
              .reverse()
              .map((s) =>
                !filled && s.name === data.name && s.result === undefined
                  ? ((filled = true), { ...s, result: data.text })
                  : s,
              )
              .reverse();
            setMessages((prev) =>
              prev.map((m) => (m.key === answer.key ? { ...m, skills } : m)),
            );
          } else if (event === "delta") {
            content += data.text;
            pending.current = { key: answer.key, text: content, reasoning };
            schedule();
          } else if (event === "error") {
            flush();
            fail(data.message);
          }
        }
        settle();
      } catch (error) {
        settle();
        // Stopping is not failing. The reader asked for the turn to end, the
        // server has already kept whatever was written, and what is on screen
        // is what will be there on reload -- so the bubble simply stops
        // growing. An error under it would be describing an outcome the reader
        // chose, which is how a deliberate act gets reported as a fault.
        const stopped = error.name === "AbortError";
        // A rejected token has already dropped the app back to the gate;
        // stacking "lost the connection" on top of that says nothing.
        if (!stopped && !(error instanceof UnauthorizedError)) {
          // A refusal from the server already says what went wrong and why.
          // Only a genuinely dropped connection needs to be described as one.
          const explained = error instanceof ApiError || error.name === "FileReadError";
          fail(explained ? error.message : "Lost the connection: " + error.message);
        }
      } finally {
        inFlight.current = null;
        setMessages((prev) =>
          prev.map((m) => (m.key === answer.key ? { ...m, streaming: false } : m)),
        );
        setStreaming(false);

        // The server titles the session after the first exchange, off the
        // response path -- so the name is only there once the turn is over.
        onSessionsChanged();
        if (active) {
          api
            .getSession(active)
            .then((data) => {
              // Only if that session is still the one on screen: a turn
              // finishing after the reader moved on must not retitle the
              // header of the conversation they moved to.
              if (openRef.current === active && data.session.title) {
                setTitle(data.session.title);
              }
            })
            .catch(() => {});
        }
      }
    },
    [
      api,
      flush,
      jumpToEnd,
      onSessionsChanged,
      provider,
      schedule,
      sessionId,
      settle,
      streaming,
    ],
  );

  /**
   * End the turn in flight, keeping what has arrived.
   *
   * Aborting the fetch is the whole mechanism -- there is no "stop" message to
   * send. The server notices the disconnect between frames and its `finally`
   * writes the partial answer, so what is on screen at the moment you press it
   * is what the conversation keeps. Reopening the session shows the same
   * half-finished reply rather than an empty bubble.
   *
   * Safe to call when nothing is running: the ref is cleared in `send`'s
   * `finally`, so this is a no-op rather than an error.
   */
  const stop = useCallback(() => {
    inFlight.current?.abort();
  }, []);

  // A turn outliving the component that started it has nobody to render it and
  // no way to be stopped, so unmounting ends it. Without this, navigating away
  // mid-answer leaves the model generating into a closed page.
  useEffect(() => () => inFlight.current?.abort(), []);

  return {
    messages,
    sessionId,
    title,
    streaming,
    badge,
    scrollToken,
    setBadge,
    openSession,
    startNew,
    send,
    stop,
  };
}
