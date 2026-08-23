import { useEffect, useLayoutEffect, useRef, useState } from "react";

import { Icon } from "./Icon";

const THINKING_LEVELS = ["low", "medium", "high"];

export function Composer({ disabled, focusToken, onSend }) {
  const [value, setValue] = useState("");
  const [thinkingLevel, setThinkingLevel] = useState("medium");
  const form = useRef(null);
  const input = useRef(null);

  // Grow with the text, up to 40% of the viewport. The message list pads
  // itself by --composer-h, so the last bubble is never behind the box --
  // which means anything else that changes the composer's height has to
  // re-measure too.
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
  // message ends up behind the input.
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
    onSend(text, thinkingLevel);
  };

  return (
    <form className="composer" ref={form} onSubmit={submit}>
      <div className="thinking-control">
        <label className="thinking-label" htmlFor="thinking-level">
          <Icon name="thinking" />
          <span>Reasoning</span>
        </label>
        <input
          id="thinking-level"
          type="range"
          min="0"
          max="2"
          step="1"
          value={THINKING_LEVELS.indexOf(thinkingLevel)}
          aria-valuetext={thinkingLevel}
          aria-label="Reasoning effort"
          disabled={disabled}
          onChange={(event) =>
            setThinkingLevel(THINKING_LEVELS[Number(event.target.value)])
          }
        />
        <output htmlFor="thinking-level">{thinkingLevel}</output>
      </div>

      <div className="composer-row">
        {/* Attachments are still being rebuilt; this control is deliberately
            inert until its server path returns. */}
        <button
          type="button"
          className="icon-btn"
          aria-label="Attach files"
          title="Attachments are being rebuilt — this does nothing yet."
          disabled={disabled}
        >
          <Icon name="attachment" badge="plus" />
        </button>

        <textarea
          ref={input}
          rows="1"
          placeholder="Message"
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

        <button type="submit" className="send" aria-label="Send" disabled={disabled}>
          <Icon name="send" />
        </button>
      </div>
    </form>
  );
}
