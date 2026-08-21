import { memo, useMemo } from "react";

import { renderMarkdown } from "../lib/markdown";

/**
 * One bubble.
 *
 * Memoised because a streaming answer re-renders on every animation frame and
 * the settled messages above it have not changed a character.
 */
export const Message = memo(function Message({ role, content, streaming }) {
  // renderMarkdown escapes the source before emitting a single tag, so no
  // model output reaches the DOM as markup. That is the whole contract; see
  // lib/markdown.js.
  const html = useMemo(
    () => (role === "assistant" ? { __html: renderMarkdown(content) } : null),
    [role, content],
  );

  const className = "msg" + (streaming ? " cursor" : "");

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
