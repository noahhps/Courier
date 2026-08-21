import { useEffect, useLayoutEffect, useRef, useState } from "react";

/**
 * The model's working, live.
 *
 * Open while it is the only thing happening, folded away the moment the answer
 * starts -- reasoning runs to thousands of tokens and would otherwise push the
 * reply off the screen. Folding is done once, on that transition, so anyone who
 * opens it back up to read along is left alone.
 *
 * Deliberately not markdown: this is half-formed text arriving a token at a
 * time, and running an incomplete stream through the renderer produces a
 * flickering mess of half-open code fences.
 */
export function Reasoning({ text, answering }) {
  const [open, setOpen] = useState(true);
  const body = useRef(null);

  useEffect(() => {
    if (answering) setOpen(false);
  }, [answering]);

  // Follow the tail as it grows, but only within the block: the thread has its
  // own scrolling and this must not fight it.
  useLayoutEffect(() => {
    if (open && body.current) body.current.scrollTop = body.current.scrollHeight;
  }, [text, open]);

  return (
    <div className="reasoning" data-live={!answering || undefined}>
      <button
        type="button"
        className="reasoning-toggle"
        aria-expanded={open}
        onClick={() => setOpen((was) => !was)}
      >
        {answering ? "Thought before answering" : "Thinking…"}
      </button>
      {open ? (
        <div className="reasoning-body" ref={body}>
          {text}
        </div>
      ) : null}
    </div>
  );
}
