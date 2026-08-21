// The glyphs the shell uses, as one component.
//
// Everything here is an inline stroked path: stroke, width and cap are styled
// once in CSS for every `svg`, so a path is all any of them needs.
//
// `attachment` and `thinking` were file-backed silhouettes masked out of
// public/*.svg. Those files were deleted and are not recoverable, so both are
// drawn inline for now. FILES is kept wired up: drop the originals back into
// public/ and they take precedence again, with no other change needed.
const PATHS = {
  menu: "M4 7h16M4 12h16M4 17h16",
  plus: "M12 5v14M5 12h14",
  close: "M6 6l12 12M18 6L6 18",
  send: "M5 12h14M13 6l6 6-6 6",
  check: "M5 12.5l5 5 9-11",
  // Paperclip.
  attachment:
    "M21 11.5l-8.6 8.6a5 5 0 01-7-7l8.6-8.6a3.3 3.3 0 014.7 4.7l-8.6 8.6a1.7 1.7 0 01-2.3-2.3l7.9-7.9",
  // A head in profile with a thought inside it -- reasoning, not a lightbulb.
  thinking:
    "M15.5 20.5v-2.2a6.5 6.5 0 10-7-10.6M6 13.5H4l2-3.6M9 20.5v-3.2M5 17.5l-2 3M12 11.5h.01M15 11.5h.01M9 11.5h.01",
};

const FILES = {};

export function Icon({ name, badge }) {
  const file = FILES[name];
  const glyph = file ? (
    <span className="icon-mask" style={{ "--mask": `url("${file}")` }} aria-hidden="true" />
  ) : (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d={PATHS[name]} />
    </svg>
  );

  if (!badge) return glyph;

  // A second glyph tucked into the bottom-right corner, inside the icon's own
  // box rather than hanging off it. It sits on a patch of the page background
  // so it stays legible over whatever the artwork underneath is doing.
  return (
    <span className="icon-stack" aria-hidden="true">
      {glyph}
      <svg className="icon-badge" viewBox="0 0 24 24">
        <path d={PATHS[badge]} />
      </svg>
    </span>
  );
}
