// The glyphs the shell uses, as one component. Two kinds, for two reasons.
//
// The shell's own four are inline paths: stroke, width, and cap are styled
// once in CSS for every `svg`, so a path is all any of them needs.
//
// The rest are files in public/ -- drawn silhouettes, far too much path data
// to paste in here. They are painted by masking `currentColor` through the
// file, so they inherit the theme and the button's disabled state exactly like
// the inline ones, and the .svg on disk stays the single source of truth: redraw
// the file and the UI follows without touching this component.
const PATHS = {
  menu: "M4 7h16M4 12h16M4 17h16",
  plus: "M12 5v14M5 12h14",
  close: "M6 6l12 12M18 6L6 18",
  send: "M5 12h14M13 6l6 6-6 6",
};

const FILES = {
  attachment: "/attachment.svg",
  thinking: "/thinking.svg",
};

export function Icon({ name, badge }) {
  // The sheet draws its mic as a plain filled capsule rather than a mic
  // outline, which is why it sits outside PATHS: everything there is stroked,
  // and this one is a shape.
  if (name === "mic") {
    return (
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <rect x="7.5" y="4.5" width="9" height="15" rx="4.5" fill="currentColor" stroke="none" />
      </svg>
    );
  }

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
  // box rather than hanging off it. It rides on a disc of the page background
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
