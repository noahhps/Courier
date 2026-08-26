import { memo, useCallback, useMemo } from "react";

import { useApi } from "../lib/api-context";
import { saveDocument } from "../lib/files";
import { renderMarkdown } from "../lib/markdown";
import { MessageAttachments } from "./Attachments";
import { Reasoning } from "./Reasoning";
import { SkillTrace } from "./SkillTrace";

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
  skills,
  model,
}) {
  const api = useApi();

  // A link the assistant wrote to something this server holds. The endpoint is
  // authenticated and a browser sends no bearer header when it follows a link,
  // so left alone every one of these answers 401. Intercepted here and fetched
  // through the api client instead, which is the same thing Attachments.jsx
  // does to put an image on screen.
  const onBodyClick = useCallback(
    (event) => {
      const link = event.target.closest?.("a");
      if (!link || !api) return;
      let url;
      try {
        url = new URL(link.getAttribute("href"), window.location.origin);
      } catch {
        return; // not a URL we can reason about; let the browser have it
      }
      if (url.origin !== window.location.origin) return;
      if (!url.pathname.startsWith("/api/documents/")) return;

      event.preventDefault();
      const name = decodeURIComponent(url.pathname.split("/").pop() || "");
      saveDocument(api, url.pathname, name).catch(() => {
        // The server said no, or it is gone. Falling back to a normal
        // navigation at least shows the real status rather than nothing.
        window.open(url.href, "_blank", "noopener");
      });
    },
    [api],
  );

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

        <SkillTrace skills={skills} />

        {html ? (
          <div className="body" onClick={onBodyClick} dangerouslySetInnerHTML={html} />
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
