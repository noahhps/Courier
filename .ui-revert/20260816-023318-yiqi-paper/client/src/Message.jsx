import { memo, useMemo } from "react";

import { renderMarkdown } from "../lib/markdown";
import { MessageAttachments } from "./Attachments";
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
  attachments,
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

  // The markdown stays the bubble's own innerHTML rather than gaining a
  // wrapper: the stylesheet trims the margin off `.msg > :first-child`, and a
  // div in between would leave a blank line above every answer. Only the user
  // sends files, so an assistant bubble never needs the branch below.
  if (html) {
    return <div className={className} data-role={role} dangerouslySetInnerHTML={html} />;
  }

  return (
    <div className={className} data-role={role}>
      <MessageAttachments attachments={attachments} />
      {content}
    </div>
  );
});
