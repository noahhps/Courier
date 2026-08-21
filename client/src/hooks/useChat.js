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
export function useChat(api, { onSessionsChanged }) {
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
  const pending = useRef({ key: null, text: "", reasoning: "" });

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

  const schedule = useCallback(() => {
    if (frame.current) return;
    frame.current = requestAnimationFrame(() => {
      frame.current = 0;
      flush();
    });
  }, [flush]);

  const settle = useCallback(() => {
    if (frame.current) {
      cancelAnimationFrame(frame.current);
      frame.current = 0;
    }
    flush();
    pending.current = { key: null, text: "", reasoning: "" };
  }, [flush]);

  useEffect(() => () => cancelAnimationFrame(frame.current), []);

  // -- navigation -----------------------------------------------------------

  const openSession = useCallback(
    async (id) => {
      const data = await api.getSession(id);
      setSessionId(id);
      setTitle(data.session.title || "Untitled");
      setMessages(
        data.messages
          // A turn can be a file with nothing typed, so an empty body is only
          // uninteresting when nothing came with it.
          .filter((m) => m.role !== "system" && (m.content || m.attachments?.length))
          .map((m) => message(m.role, m.content, { attachments: m.attachments })),
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
    async (text, files = [], think = null) => {
      if (streaming || (!text.trim() && !files.length)) return;
      setStreaming(true);

      const answer = message("assistant", "", { streaming: true });
      const asked = message("user", text);
      setMessages((prev) => [...prev, asked, answer]);
      jumpToEnd();

      let active = sessionId;
      let content = "";
      let reasoning = "";
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

        const response = await api.chat(text, sessionId, attachments, think);

        for await (const { event, data } of readEvents(response)) {
          if (event === "session") {
            active = data.session_id;
            setSessionId(active);
          } else if (event === "meta") {
            setBadge({
              text: data.source === "fallback" ? "cloud · " + data.model : data.model,
              tone: data.source === "fallback" ? "warn" : null,
            });
          } else if (event === "thinking") {
            // The model's working, not its answer. Shown live, never stored:
            // reopening the conversation gives back the reply alone.
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
        // A rejected token has already dropped the app back to the gate;
        // stacking "lost the connection" on top of that says nothing.
        if (!(error instanceof UnauthorizedError)) {
          // A refusal from the server -- a file too large, or of a kind no
          // model can read -- already says what went wrong and why. Only a
          // genuinely dropped connection needs to be described as one.
          const explained = error instanceof ApiError || error.name === "FileReadError";
          fail(explained ? error.message : "Lost the connection: " + error.message);
        }
      } finally {
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
    [api, flush, jumpToEnd, onSessionsChanged, schedule, sessionId, settle, streaming],
  );

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
  };
}
