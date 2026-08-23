import { memo, useMemo } from "react";

import { renderMarkdown } from "../lib/markdown";
import { Reasoning } from "./Reasoning";

/**
 * One bubble.
 *
 * Memoised because a streaming answer re-renders on every animation frame and
 * the settled messages above it have not changed a character.
 */
export const Message = memo(function Message({
  role,
  content,
  streaming,
  thinking,
  reasoning,
}) {
  // renderMarkdown escapes the source before emitting a single tag, so no
  // model output reaches the DOM as markup. That is the whole contract; see
  // lib/markdown.js.
  const html = useMemo(
    () => (role === "assistant" && !thinking ? { __html: renderMarkdown(content) } : null),
    [role, content, thinking],
  );

  const className = "msg" + (streaming ? " cursor" : "");

  // Reasoning sits above the answer and outlives the wait for it, so the two
  // are rendered together once there is anything to reason about.
  if (reasoning) {
    return (
      <div className={className} data-role={role}>
        <Reasoning text={reasoning} answering={Boolean(content)} />
        {html ? <div dangerouslySetInnerHTML={html} /> : content}
      </div>
    );
  }

  // The gap between "the model started thinking" and its first reasoning
  // token. Rare and brief, but without it the bubble is empty.
  if (thinking) {
    return (
      <div className={className} data-role={role} data-thinking="1">
        thinking…
      </div>
    );
  }

  // The turn has started and nothing has come back yet. The sheet fills this
  // moment with a blinking WORKING plate rather than an empty bubble -- and it
  // carries no `cursor`, since the plate is the thing that blinks.
  if (streaming && !content) {
    return (
      <div className="msg" data-role={role}>
        <span className="working">Working</span>
      </div>
    );
  }

  // The markdown stays the bubble's own innerHTML rather than gaining a
  // wrapper: the stylesheet trims the margin off `.msg > :first-child`, and a
  // div in between would leave a blank line above every answer.
  if (html) {
    return <div className={className} data-role={role} dangerouslySetInnerHTML={html} />;
  }

  return (
    <div className={className} data-role={role}>
      {content}
    </div>
  );
});
