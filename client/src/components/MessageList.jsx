import { useLayoutEffect, useRef } from "react";

import { Message } from "./Message";

// Anything within this much of the bottom counts as "reading the end", so a
// new token follows the reader down. Further up and they are looking at
// something; the thread must not yank them away from it.
const STICK_PX = 120;

export function MessageList({ messages, model, scrollToken }) {
  const ref = useRef(null);

  // Opening a session or sending a message: go to the bottom, wherever the
  // reader was.
  useLayoutEffect(() => {
    const node = ref.current;
    node.scrollTop = node.scrollHeight;
  }, [scrollToken]);

  // A token landed. Follow it only if the reader was already at the end --
  // measured after the paint, so one delta's worth of growth stays inside the
  // threshold.
  useLayoutEffect(() => {
    const node = ref.current;
    const { scrollTop, scrollHeight, clientHeight } = node;
    if (scrollHeight - scrollTop - clientHeight < STICK_PX) {
      node.scrollTop = scrollHeight;
    }
  }, [messages]);

  return (
    <main className="messages" ref={ref}>
      {messages.length === 0 ? (
        <div className="empty">Ask anything, or start where you left off.</div>
      ) : (
        messages.map((m) => (
          <Message
            key={m.key}
            role={m.role}
            content={m.content}
            streaming={m.streaming}
            thinking={m.thinking}
            reasoning={m.reasoning}
            attachments={m.attachments}
            // Only the turn that is actually from the assistant carries the
            // provenance line; a user bubble and an error have no model.
            model={m.role === "assistant" ? model : null}
          />
        ))
      )}
    </main>
  );
}
