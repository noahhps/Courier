import { memo, useMemo } from "react";

import { renderMarkdown } from "../lib/markdown";
import { MessageAttachments } from "./Attachments";
import { Reasoning } from "./Reasoning";

/**
 * One turn.
 *
 * The user's words are a bubble on the right. An answer is the margin-and-rule
 * layout from artboard 1a: a narrow column of what the system drew on, a
 * hairline, then the prose.
 *
 * The margin is deliberately rendered even when it is empty. It is where
 * recalled facts and read documents will go once there is a memory layer to
 * fill it, and reserving the column now means the answer does not shift
 * sideways the day it arrives. Until then it carries the one piece of
 * provenance the server does report: which model produced the turn.
 *
 * Memoised because a streaming answer re-renders on every animation frame and
 * the settled turns above it have not changed a character.
 */
export const Message = memo(function Message({
  role,
  content,
  streaming,
  thinking,
  reasoning,
  attachments,
  model,
}) {
  // renderMarkdown escapes the source before emitting a single tag, so no
  // model output reaches the DOM as markup. That is the whole contract; see
  // lib/markdown.js.
  const html = useMemo(
    () => (role === "assistant" && content ? { __html: renderMarkdown(content) } : null),
    [role, content],
  );

  if (role === "user") {
    return (
      <div className="turn-user">
        <div className="turn-user-stack">
          <MessageAttachments attachments={attachments} />
          {/* A turn can be nothing but a dropped file, in which case there is
              no bubble to draw -- only what was attached. */}
          {content ? <div className="bubble">{content}</div> : null}
        </div>
      </div>
    );
  }

  if (role === "error") {
    return <div className="turn-error">{content}</div>;
  }

  return (
    <div className="turn-answer">
      <div className="margin">
        {model ? (
          <>
            <span className="mi">Answered by</span>
            <p data-soft>{model}</p>
          </>
        ) : null}
      </div>
      <div className="margin-rule" data-soft={model ? undefined : true} />

      <div className="answer">
        {reasoning ? (
          <Reasoning text={reasoning} answering={Boolean(content)} />
        ) : null}

        {html ? (
          <div className="body" dangerouslySetInnerHTML={html} />
        ) : streaming ? (
          // The gap between the turn starting and its first token. Without
          // something here the answer column is simply blank.
          <span className="working">{thinking ? "Thinking" : "Working"}</span>
        ) : (
          <div className="body">{content}</div>
        )}
      </div>
    </div>
  );
});
