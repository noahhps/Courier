/* Accents: one hue in, a whole stylesheet's worth of tints out.
 *
 * The shipped palette is a ladder -- a sheet at OKLCH lightness 0.995, a shell
 * at 0.957, four line weights between 0.77 and 0.91, two text greys, and a
 * cobalt accent at 0.49 -- and every rung of it is the *same hue* at a
 * different lightness and a fraction of the accent's chroma. That is what
 * makes it a system rather than sixteen chosen colours, and it is also what
 * makes it swappable: hold the ladder, change the hue, and the app is dressed
 * in something else without a single rule in styles.css knowing about it.
 *
 * The ratios below were read back off the hand-picked hexes in styles.css, so
 * `cobalt` at full strength reproduces the palette this app shipped with to
 * within a rounding step. "Off" and "cobalt" agreeing is not a coincidence
 * worth losing -- it is the proof that the generator did not invent a look.
 *
 * What the generator does *not* do is the theatrical part. Apple Music's
 * colour is mostly one enormous soft field behind everything, and that is a
 * separate layer here -- the `--aura-*` tokens at the bottom, painted by
 * `.app::before` in styles.css. Surfaces stay nearly white and legible; the
 * drama sits behind them.
 */

import { oklch, readable } from "./color";

// The ten accents on the swatch row. Hue in OKLCH degrees, chroma in OKLCH
// units -- roughly 0.03 is a tinted grey, 0.22 is about as saturated as sRGB
// goes at these lightnesses.
//
// `cobalt` is measured from #1f4fd8, the accent this app has always used, so
// picking it is genuinely a return to the original rather than an
// approximation of it.
export const PRESETS = [
  { id: "cobalt", name: "Cobalt", hue: 264.5, chroma: 0.216 },
  { id: "midnight", name: "Midnight", hue: 258, chroma: 0.105 },
  { id: "iris", name: "Iris", hue: 300, chroma: 0.15 },
  { id: "mauve", name: "Mauve", hue: 350, chroma: 0.08 },
  { id: "rose", name: "Rose", hue: 15, chroma: 0.135 },
  { id: "ember", name: "Ember", hue: 45, chroma: 0.16 },
  { id: "brass", name: "Brass", hue: 92, chroma: 0.13 },
  { id: "fern", name: "Fern", hue: 152, chroma: 0.115 },
  { id: "teal", name: "Teal", hue: 195, chroma: 0.11 },
  { id: "slate", name: "Slate", hue: 250, chroma: 0.032 },
];

const BY_ID = new Map(PRESETS.map((preset) => [preset.id, preset]));

// How much colour, from a hairline to the full wash. The shipped palette sits
// at about 0.31 on this scale; the default is deliberately above it, because
// an accent nobody can see is not one they chose.
export const DEFAULT_STRENGTH = 0.55;

/** The app-wide accent when nothing has ever been chosen. */
export const DEFAULT_ACCENT = { mode: "auto", strength: DEFAULT_STRENGTH };

/** Resolve an accent to its hue and chroma, or null for "wear no colour". */
export function seedOf(accent, fallback = null) {
  if (!accent || accent.mode === "off") return null;
  if (accent.mode === "custom") {
    return { hue: accent.hue ?? 264.5, chroma: accent.chroma ?? 0.14 };
  }
  if (accent.mode === "preset") {
    const preset = BY_ID.get(accent.preset);
    return preset ? { hue: preset.hue, chroma: preset.chroma } : null;
  }
  // auto: the caller works the hue out from the conversation and hands it in.
  return fallback;
}

/* The ladder. Each rung is [lightness, chroma as a fraction of the accent's].
 *
 * Read off styles.css rather than designed here, which is the point: this
 * table is a description of a palette that already worked, not a new opinion
 * about one. The lightnesses are held at every hue -- that is what OKLab buys
 * -- and only the chroma fractions are scaled by how strong an accent is
 * asked for. */
const LADDER = {
  ground: [0.995, 0.012],
  surface: [1.0, 0.0],
  shell: [0.9573, 0.034],
  menu: [0.9451, 0.048],
  "menu-well": [0.9451, 0.048],
  rail: [0.9728, 0.026],
  "grid-line": [0.9663, 0.034],
  line: [0.8866, 0.084],
  "line-soft": [0.9058, 0.069],
  "line-firm": [0.8614, 0.107],
  "line-strong": [0.7698, 0.141],
  ink: [0.1915, 0.122],
  "accent-soft": [0.6197, 0.746],
  field: [0.9312, 0.105],
  wash: [0.9431, 0.085],
};

/**
 * Every custom property an accent sets, as a plain object ready to be written
 * onto an element's style.
 *
 * Returns null for "off", which the caller reads as "clear what you set and
 * let styles.css stand" -- an empty object would mean the same thing, but a
 * null makes the two cases impossible to confuse at the call site.
 */
export function palette(accent, fallbackSeed = null) {
  const seed = seedOf(accent, fallbackSeed);
  if (!seed) return null;

  const { hue } = seed;
  const strength = clampStrength(accent?.strength);
  // The surface tints scale with strength; the accent's own chroma does not.
  // Turning the wash down should leave the links the colour they were, not
  // fade the one thing on the sheet that has to stay visible.
  const spread = 0.5 + 1.6 * strength;
  const chroma = seed.chroma;
  const tone = (rung) => {
    const [lightness, fraction] = LADDER[rung];
    return oklch(lightness, chroma * fraction * spread, hue);
  };

  const ground = tone("ground");
  // Three colours that carry text, and therefore the three that get checked
  // rather than chosen. 5.5:1 for the accent because it is also a link and a
  // 12px label; 4.5 for the greys, which is what the stylesheet's own comments
  // settled on after two rounds of darkening them by hand.
  const accentColour = readable(hue, chroma, ground, 5.5, 0.56);
  const accentHover = readable(hue, chroma, ground, 8, 0.42);
  const dim = readable(hue, chroma * 0.156 * spread, ground, 6, 0.56);
  const faint = readable(hue, chroma * 0.148 * spread, ground, 4.6, 0.58);

  return {
    "--shell": tone("shell"),
    "--menu": tone("menu"),
    "--menu-well": tone("menu-well"),
    "--ground": ground,
    "--rail": tone("rail"),
    "--surface": tone("surface"),
    "--ink": tone("ink"),
    "--grid-line": tone("grid-line"),

    // The four-tint system keeps its names -- every component in the app reads
    // one of them -- but violet and blue have resolved to the same colour
    // since the cobalt redesign, and both now follow the accent. Green and
    // ochre are left alone on purpose: they mean pass and warning, and a
    // warning that turns blue because the reader likes blue is a bug.
    "--violet": accentColour,
    "--violet-soft": tone("accent-soft"),
    "--violet-field": tone("field"),
    "--violet-wash": tone("wash"),
    "--blue": accentColour,
    "--blue-field": tone("field"),
    "--blue-wash": tone("wash"),
    "--send": accentColour,

    "--accent": accentColour,
    "--accent-hover": accentHover,

    "--line": tone("line"),
    "--line-soft": tone("line-soft"),
    "--line-firm": tone("line-firm"),
    "--line-strong": tone("line-strong"),

    "--text-dim": dim,
    "--text-faint": faint,

    /* The ambient field, which is the part that actually looks like the
     * reference. Three soft discs of colour behind everything: one large and
     * mid-toned low on the sheet, one deeper and offset in hue, one pale and
     * turned the other way. Their hues are pulled apart by about 25 degrees
     * each so the wash has somewhere to go -- a single-hue blur reads as a
     * cast over the screen rather than as light coming from somewhere. */
    "--aura-1": oklch(0.845, chroma * 0.95, hue),
    "--aura-2": oklch(0.755, chroma * 1.15, hue + 26),
    "--aura-3": oklch(0.905, chroma * 0.72, hue - 32),
    // Kept under two thirds even at full strength. The fields sit behind the
    // thread, and past that they stop being light on a sheet and start being
    // a background the text has to fight.
    "--aura-opacity": (0.12 + 0.5 * strength).toFixed(3),
    // A saturated form of the accent for the one place that wants the hue
    // undiluted -- the swatch a chat wears in the rail.
    "--accent-pure": oklch(0.62, chroma, hue),
  };
}

function clampStrength(value) {
  if (typeof value !== "number" || Number.isNaN(value)) return DEFAULT_STRENGTH;
  return Math.min(1, Math.max(0, value));
}

/** The one colour that stands for an accent: a rail dot, a swatch, a preview. */
export function swatchOf(accent, fallbackSeed = null) {
  const seed = seedOf(accent, fallbackSeed);
  if (!seed) return "var(--line-strong)";
  return oklch(0.62, seed.chroma, seed.hue);
}

/**
 * Write a palette onto an element, and take the previous one off.
 *
 * The removal half matters more than it looks: switching from an accent that
 * sets sixteen properties to one that sets none has to leave the element
 * clean, or the sheet keeps whichever tokens the new palette happens not to
 * mention. Every key this module can emit is in `ALL_TOKENS` for exactly that.
 */
export const ALL_TOKENS = Object.keys(palette({ mode: "preset", preset: "cobalt" }));

export function applyPalette(element, tokens) {
  if (!element) return;
  for (const name of ALL_TOKENS) element.style.removeProperty(name);
  if (!tokens) return;
  for (const [name, value] of Object.entries(tokens)) {
    element.style.setProperty(name, value);
  }
}
