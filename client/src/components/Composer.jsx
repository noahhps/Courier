import { useEffect, useLayoutEffect, useRef, useState } from "react";

import { Icon } from "./Icon";

const EFFORTS = ["low", "medium", "high"];

/**
 * The composer from artboard 1a: one rounded box, the text on its own line,
 * and a row of chips beneath it with the send button at the right.
 *
 * The reasoning control is a three-way chip group rather than the old slider.
 * The design has no sliders in it, and three named states read faster than a
 * track with an output label under it.
 */
export function Composer({ disabled, focusToken, sessionLabel, onSend }) {
  const [value, setValue] = useState("");
  const [effort, setEffort] = useState("medium");
  const form = useRef(null);
  const input = useRef(null);

  // Grow with the text, up to 40% of the viewport. The message list pads
  // itself by --composer-h, so the last turn is never behind the box -- which
  // means anything that changes the composer's height has to re-measure too.
  useLayoutEffect(() => {
    const node = input.current;
    node.style.height = "auto";
    node.style.height = Math.min(node.scrollHeight, window.innerHeight * 0.4) + "px";
    document.documentElement.style.setProperty(
      "--composer-h",
      form.current.offsetHeight + "px",
    );
  }, [value]);

  // The layout effect above covers everything that changes the composer's
  // contents. This covers everything that changes its box for other reasons --
  // the drawer opening and narrowing it, the window resizing, a font landing --
  // any of which can rewrap the text and leave --composer-h stale, so the last
  // turn ends up behind the input.
  useEffect(() => {
    const node = form.current;
    const observer = new ResizeObserver(() => {
      document.documentElement.style.setProperty("--composer-h", node.offsetHeight + "px");
    });
    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  // Starting a new conversation puts the caret in the box. Only then -- an
  // unprompted focus on a phone throws the keyboard up over the thread.
  useEffect(() => {
    if (focusToken) input.current.focus();
  }, [focusToken]);

  const submit = (event) => {
    event.preventDefault();
    // The button is disabled while a turn streams, but Enter still submits --
    // and clearing the box for a send that gets refused loses what was typed.
    if (disabled) return;
    if (!value.trim()) return;

    const text = value;
    setValue("");
    onSend(text, effort);
  };

  return (
    <form className="composer" ref={form} onSubmit={submit}>
      <div className="composer-box">
        <textarea
          ref={input}
          rows="1"
          placeholder="Ask anything, or drop in a document…"
          autoComplete="off"
          autoCapitalize="sentences"
          value={value}
          onChange={(event) => setValue(event.target.value)}
          onKeyDown={(event) => {
            // Enter sends on a real keyboard; on a phone it should insert a
            // newline -- there is nowhere else to put one.
            const isTouch = window.matchMedia("(pointer: coarse)").matches;
            if (event.key === "Enter" && !event.shiftKey && !isTouch) {
              event.preventDefault();
              form.current.requestSubmit();
            }
          }}
        />

        <div className="composer-row">
          {/* Attachments are still being rebuilt on the server: there is no
              /api/attachments route to post to. Left visible and inert, with
              the reason on the tooltip, rather than removed and forgotten. */}
          <button
            type="button"
            className="chip"
            title="Attachments are being rebuilt — this does nothing yet."
            disabled
          >
            Attach
          </button>

          {sessionLabel ? <span className="chip">{sessionLabel}</span> : null}

          <div className="spacer" />

          <div className="effort" role="group" aria-label="Reasoning effort">
            {EFFORTS.map((level) => (
              <button
                key={level}
                type="button"
                aria-pressed={effort === level}
                disabled={disabled}
                onClick={() => setEffort(level)}
              >
                {level}
              </button>
            ))}
          </div>

          <button type="submit" className="send" aria-label="Send" disabled={disabled}>
            <Icon name="send" />
          </button>
        </div>
      </div>
    </form>
  );
}
