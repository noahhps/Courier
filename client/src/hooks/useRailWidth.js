import { useCallback, useEffect, useRef, useState } from "react";

// A layout preference rather than data, so it lives beside the pin in
// localStorage rather than on the server: it belongs to this screen, and a
// phone and a desktop want different answers.
const KEY = "unified-llm-rail-width";

// Narrower than this and a conversation title has no room to be recognisable,
// which is the whole reason the open rail is wider than the icons need. Wider
// than the max and the rail stops being a rail.
const MIN = 200;
const MAX = 460;
// Whatever the window, this much has to be left for the conversation.
const KEEP_FOR_SHEET = 320;
// The width the rail is drawn at, and what a double-click on the handle
// returns to.
const DEFAULT = 252;

function clamp(px) {
  const ceiling = Math.min(MAX, Math.max(MIN, window.innerWidth - KEEP_FOR_SHEET));
  return Math.round(Math.min(ceiling, Math.max(MIN, px)));
}

/**
 * The width of the opened rail, dragged by its right edge.
 *
 * Returns `resizing` as well as the number because two things have to change
 * during a drag: the rail's width transition has to come off, or the panel
 * lags a couple of frames behind the pointer, and the rail has to be held open
 * even though the pointer has left it.
 */
export function useRailWidth() {
  const [width, setWidth] = useState(() => {
    const saved = Number(localStorage.getItem(KEY));
    return Number.isFinite(saved) && saved > 0 ? clamp(saved) : DEFAULT;
  });
  const [resizing, setResizing] = useState(false);
  // Below 900px the stylesheet sets its own, narrower --rail-open, and the rail
  // opens on tap rather than hover. An inline custom property on .app outranks
  // that :root rule, so publishing one on a phone would silently kill the
  // designed phone sizing -- and a 9px drag handle is no use on a touchscreen
  // anyway. So the whole feature is desktop-only, rather than fighting it.
  const [wide, setWide] = useState(
    () => window.matchMedia("(min-width: 901px)").matches,
  );

  useEffect(() => {
    const mq = window.matchMedia("(min-width: 901px)");
    const sync = () => setWide(mq.matches);
    mq.addEventListener("change", sync);
    return () => mq.removeEventListener("change", sync);
  }, []);
  // Read inside a pointermove handler that was created once, so it cannot see
  // the `width` from the render it closed over.
  const live = useRef(width);
  live.current = width;

  const commit = useCallback((px) => {
    const next = clamp(px);
    live.current = next;
    setWidth(next);
    return next;
  }, []);

  const start = useCallback(
    (event) => {
      // Stops the drag selecting the labels either side of the handle.
      event.preventDefault();
      const originX = event.clientX;
      const originW = live.current;
      setResizing(true);

      const move = (moved) => commit(originW + (moved.clientX - originX));
      const stop = () => {
        window.removeEventListener("pointermove", move);
        setResizing(false);
        localStorage.setItem(KEY, String(live.current));
      };

      // On window rather than on the handle: the pointer routinely outruns a
      // 7px target, and losing the drag because it did would make the handle
      // feel broken rather than precise.
      window.addEventListener("pointermove", move);
      window.addEventListener("pointerup", stop, { once: true });
      window.addEventListener("pointercancel", stop, { once: true });
    },
    [commit],
  );

  // The separator is focusable, so it answers to the keyboard too -- a drag
  // handle that only takes a mouse is unusable for anyone who does not use one.
  const nudge = useCallback(
    (event) => {
      const step = event.shiftKey ? 48 : 16;
      let next = null;
      if (event.key === "ArrowLeft") next = live.current - step;
      else if (event.key === "ArrowRight") next = live.current + step;
      else if (event.key === "Home") next = MIN;
      else if (event.key === "End") next = MAX;
      // Sent by the handle's double-click, not by a keyboard.
      else if (event.key === "Reset") next = DEFAULT;
      if (next === null) return;
      event.preventDefault();
      localStorage.setItem(KEY, String(commit(next)));
    },
    [commit],
  );

  // A window narrow enough to break the clamp has to pull the rail in with it,
  // or the conversation is squeezed to nothing on a resize.
  useEffect(() => {
    const onResize = () => setWidth((w) => clamp(w));
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  return { width, resizing, start, nudge, enabled: wide, min: MIN, max: MAX };
}
