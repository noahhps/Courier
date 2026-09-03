/* Colour arithmetic, in OKLCH.
 *
 * Every accent in this app is one hue put through the same set of derivations:
 * a dozen surface tints, four line weights, two text greys and an accent that
 * has to stay legible on all of it. Doing that in HSL does not work -- HSL's
 * lightness is a channel average, so `hsl(60 80% 50%)` and `hsl(260 80% 50%)`
 * are nominally the same lightness and one of them is yellow. A palette
 * generated that way is readable at some hues and not at others, and the hue
 * the reader picks is exactly the variable we do not control.
 *
 * OKLab's L is perceptual, so one lightness ladder holds at every hue: the
 * ochre accent and the indigo accent land at the same contrast against the
 * same sheet. That is the whole reason for the sixty lines of matrix below.
 *
 * Nothing here is a dependency. The transforms are Björn Ottosson's published
 * constants, and the contrast function is WCAG 2.1's, which is four lines.
 */

// -- OKLCH -> sRGB ----------------------------------------------------------

const clamp01 = (n) => (n < 0 ? 0 : n > 1 ? 1 : n);

// Linear light to the sRGB transfer curve.
function encode(channel) {
  return channel <= 0.0031308
    ? 12.92 * channel
    : 1.055 * Math.pow(channel, 1 / 2.4) - 0.055;
}

function decode(channel) {
  return channel <= 0.04045
    ? channel / 12.92
    : Math.pow((channel + 0.055) / 1.055, 2.4);
}

/** OKLCH to linear sRGB, unclamped -- out-of-gamut values fall outside 0..1,
 *  which is what `inGamut` below is looking for. */
function toLinear(lightness, chroma, hue) {
  const radians = (hue * Math.PI) / 180;
  const a = chroma * Math.cos(radians);
  const b = chroma * Math.sin(radians);

  const l = (lightness + 0.3963377774 * a + 0.2158037573 * b) ** 3;
  const m = (lightness - 0.1055613458 * a - 0.0638541728 * b) ** 3;
  const s = (lightness - 0.0894841775 * a - 1.291485548 * b) ** 3;

  return [
    4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
    -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
    -0.0041960863 * l - 0.7034186147 * m + 1.707614701 * s,
  ];
}

const inGamut = (rgb) => rgb.every((c) => c >= -0.0001 && c <= 1.0001);

/**
 * An OKLCH colour as a `#rrggbb` string, brought inside sRGB if it started
 * outside it.
 *
 * Gamut mapping is a bisection on chroma with the hue and lightness held: a
 * colour that cannot be shown is answered with the most saturated one at that
 * lightness that can, rather than with the clipped channels a naive clamp
 * gives -- clipping shifts the hue, and a palette whose ochre turns brown at
 * one end of the slider and not the other is worse than a slightly duller
 * ochre.
 */
export function oklch(lightness, chroma, hue) {
  const L = clamp01(lightness);
  let low = 0;
  let high = Math.max(0, chroma);

  if (!inGamut(toLinear(L, high, hue))) {
    // Twelve halvings resolve chroma to about 0.00005, which is far below a
    // step anything downstream can show.
    for (let i = 0; i < 12; i += 1) {
      const mid = (low + high) / 2;
      if (inGamut(toLinear(L, mid, hue))) low = mid;
      else high = mid;
    }
    high = low;
  }

  const hex = toLinear(L, high, hue)
    .map((channel) => {
      const value = Math.round(clamp01(encode(channel)) * 255);
      return value.toString(16).padStart(2, "0");
    })
    .join("");
  return `#${hex}`;
}

/** The same colour with an alpha channel, for the ambient fields. */
export function oklcha(lightness, chroma, hue, alpha) {
  const solid = oklch(lightness, chroma, hue);
  const step = Math.round(clamp01(alpha) * 255)
    .toString(16)
    .padStart(2, "0");
  return solid + step;
}

// -- contrast ---------------------------------------------------------------

function luminance(hex) {
  const value = parseInt(hex.slice(1, 7), 16);
  const [r, g, b] = [(value >> 16) & 255, (value >> 8) & 255, value & 255];
  return (
    0.2126 * decode(r / 255) + 0.7152 * decode(g / 255) + 0.0722 * decode(b / 255)
  );
}

/** WCAG 2.1 contrast ratio between two `#rrggbb` strings, 1 to 21. */
export function contrast(a, b) {
  const one = luminance(a);
  const two = luminance(b);
  return (Math.max(one, two) + 0.05) / (Math.min(one, two) + 0.05);
}

/**
 * The lightest colour at this hue and chroma that still clears `ratio`
 * against `against`, searched downwards from `start`.
 *
 * This is the function that makes the whole feature safe to hand to a reader.
 * The stylesheet's own comments record two rounds of darkening text by hand
 * until it cleared 4.5:1 on the sheet -- against one fixed palette. With the
 * palette now coming from a slider there is no hand to do that, so the search
 * happens on every derivation instead.
 *
 * Falls through to near-black if even that will not clear the ratio, which
 * only happens when `against` is itself mid-grey.
 */
export function readable(hue, chroma, against, ratio = 4.5, start = 0.62) {
  for (let L = start; L > 0.1; L -= 0.02) {
    const candidate = oklch(L, chroma, hue);
    if (contrast(candidate, against) >= ratio) return candidate;
  }
  return oklch(0.12, chroma * 0.5, hue);
}

/**
 * A stable hue for a string. Used when a conversation is too new, or too
 * unlike anything in the lexicon, for its subject to suggest a colour.
 *
 * FNV-1a, then a pick from the palette's own hues rather than anywhere on the
 * circle: a hash mapped straight to 0..360 lands in the yellow-greens about as
 * often as anywhere else, and those are the hues that go muddy as surfaces.
 */
export function hashPick(text, choices) {
  let hash = 0x811c9dc5;
  for (let i = 0; i < text.length; i += 1) {
    hash ^= text.charCodeAt(i);
    hash = Math.imul(hash, 0x01000193) >>> 0;
  }
  return choices[hash % choices.length];
}
