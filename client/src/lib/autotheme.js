/* What colour is this conversation?
 *
 * Apple Music takes its wash from the artwork, which is a picture it is handed
 * for free. A conversation has no artwork -- what it has is a subject, so the
 * subject is what this reads.
 *
 * Deliberately arithmetic rather than a model call. Asking the model what
 * colour a chat should be would cost a round trip on every turn, would need a
 * running model to render the UI at all, and would come back with a different
 * answer each time for the same conversation. This is a keyword pass over text
 * the client already has in memory: no network, no await, and the same
 * conversation is the same colour on every device that opens it.
 *
 * The lexicon is small on purpose. It does not need to classify a
 * conversation, only to notice which of a dozen fields it is nearest, and the
 * fallback for "none of them" -- a hash of the session id -- is a perfectly
 * good answer that is at least stable and distinct.
 */

import { hashPick } from "./color";
import { PRESETS } from "./theme";

/* Each field is a hue, a chroma, and the words that point at it.
 *
 * Stems, matched as prefixes: "deploy" catches deploys, deployed and
 * deployment, and writing all three would be three chances to miss one. Short
 * stems are the risk -- "art" would swallow "article" -- so anything under
 * five letters is only ever matched whole, which is what `exact` marks. */
const FIELDS = [
  {
    id: "engineering",
    hue: 264.5,
    chroma: 0.2,
    stems: "code coding function bug debug api server deploy python javascript typescript react component compile database query git commit refactor stack docker npm endpoint runtime regex terminal repo",
    exact: "app css html sql json build test ci rust go java",
  },
  {
    id: "writing",
    hue: 40,
    chroma: 0.1,
    stems: "essay draft paragraph chapter novel poem poetry prose manuscript editor rewrite narrative sentence wording headline blog newsletter script",
    exact: "book edit tone title",
  },
  {
    id: "money",
    hue: 152,
    chroma: 0.12,
    stems: "invoice budget salary revenue expense pricing invest portfolio mortgage payroll accounting refund subscription earnings",
    exact: "tax cost price cash bank loan fees",
  },
  {
    id: "health",
    hue: 178,
    chroma: 0.1,
    stems: "doctor exercise workout symptom nutrition protein injury therapy medication dentist recovery training clinic surgery",
    exact: "sleep diet gym pain sick knee back",
  },
  {
    id: "music",
    hue: 330,
    chroma: 0.115,
    stems: "album guitar chord lyric melody playlist concert singer instrument painting drawing gallery sketch design illustration",
    exact: "song band art film movie",
  },
  {
    id: "travel",
    hue: 218,
    chroma: 0.13,
    stems: "flight hotel itinerary passport airport booking airline luggage boarding station tourist",
    exact: "trip visa city train ferry",
  },
  {
    id: "food",
    hue: 55,
    chroma: 0.155,
    stems: "recipe cooking baking dinner ingredient sauce kitchen roasted marinade breakfast restaurant sourdough pasta risotto seasoning",
    exact: "oven meal dough herbs pan bread flour salad soup roast",
  },
  {
    id: "science",
    hue: 288,
    chroma: 0.145,
    stems: "physics quantum astronomy galaxy molecule theorem experiment chemistry genome equation particle telescope hypothesis",
    exact: "orbit atom lab",
  },
  {
    id: "nature",
    hue: 140,
    chroma: 0.125,
    stems: "garden planting forest weather mountain wildlife hiking harvest compost seedling climate",
    exact: "tree soil bird lake trail rain snow",
  },
  {
    id: "admin",
    hue: 250,
    chroma: 0.05,
    stems: "contract clause policy compliance licence license regulation insurance agreement paperwork landlord tenancy",
    exact: "legal terms form deed",
  },
  {
    id: "security",
    hue: 28,
    chroma: 0.155,
    stems: "security vulnerability breach exploit password credential firewall phishing encryption malware attacker",
    exact: "threat token leak audit",
  },
  {
    id: "learning",
    hue: 95,
    chroma: 0.13,
    stems: "study studying lecture homework tutorial revision curriculum semester textbook flashcard vocabulary grammar",
    exact: "exam class learn course quiz",
  },
];

// Prepared once. A Map from stem to field beats scanning twelve strings per
// token, and the conversation is re-read on every turn.
const PREFIXES = [];
const EXACT = new Map();
for (const field of FIELDS) {
  for (const stem of field.stems.split(" ")) PREFIXES.push([stem, field]);
  for (const word of field.exact.split(" ")) EXACT.set(word, field);
}
// Longest first, so "component" is credited once rather than also matching a
// shorter stem that happens to prefix it.
PREFIXES.sort((a, b) => b[0].length - a[0].length);

// The hues a conversation with no discernible subject can be given. The preset
// row rather than the whole circle: a hash mapped straight onto 0..360 lands
// in the yellow-greens as often as anywhere, and those go muddy as surfaces.
const FALLBACK = PRESETS.filter((p) => p.id !== "slate").map((p) => ({
  hue: p.hue,
  chroma: p.chroma,
}));

// How much of the conversation is read. Enough for the subject to be clear,
// bounded so a chat that has been going for three days does not turn every
// keystroke into a scan of two hundred thousand characters.
const MAX_CHARS = 24_000;
const MAX_MESSAGES = 24;

function tokens(text) {
  return text.toLowerCase().match(/[a-z]{3,}/g) || [];
}

function score(text, weight, tally) {
  for (const token of tokens(text)) {
    const exact = EXACT.get(token);
    if (exact) {
      tally.set(exact, (tally.get(exact) || 0) + weight);
      continue;
    }
    for (const [stem, field] of PREFIXES) {
      if (token.startsWith(stem)) {
        tally.set(field, (tally.get(field) || 0) + weight);
        break;
      }
    }
  }
}

/**
 * The hue and chroma a conversation suggests.
 *
 * `previous` is the seed this conversation is already wearing, and it is here
 * for one reason: a colour that changes on every turn is a flicker, not a
 * theme. A new subject has to beat the standing one by a clear margin before
 * the app is redecorated, so a chat about a deploy that mentions lunch once
 * stays blue.
 */
export function seedFromContext({ title = "", messages = [], id = "" }, previous = null) {
  const tally = new Map();

  // The title is the conversation's own summary of itself -- the server writes
  // it from the first exchange -- so it is worth several body mentions.
  if (title && title !== "New conversation" && title !== "Untitled") {
    score(title, 4, tally);
  }

  let budget = MAX_CHARS;
  // From the end backwards: what a conversation is about now matters more than
  // what it opened with, and this is also the half most likely to survive the
  // character budget.
  const recent = messages.slice(-MAX_MESSAGES);
  for (let i = recent.length - 1; i >= 0 && budget > 0; i -= 1) {
    const message = recent[i];
    if (!message?.content || message.role === "error") continue;
    const text = message.content.slice(0, budget);
    budget -= text.length;
    // What the reader asked for says more about the subject than the answer,
    // which is longer and full of the assistant's own connective prose.
    score(text, message.role === "user" ? 2 : 1, tally);
  }

  const ranked = [...tally.entries()].sort((a, b) => b[1] - a[1]);
  const [best, second] = ranked;

  /* Nothing said anything. Stable, distinct, and never grey.
   *
   * The floor is one title word, which is deliberately low: a conversation
   * titled "Sourdough starter" is about sourdough, and the title carries a
   * weight of four precisely because the server wrote it from the first
   * exchange rather than from a passing mention. A body word on its own does
   * not reach it, which is the other half of the same decision. */
  if (!best || best[1] < 4) {
    return { ...hashPick(id || title || "new", FALLBACK), field: null, confident: false };
  }

  if (previous?.field && previous.field !== best[0].id) {
    // A quarter clear of the incumbent, or the incumbent keeps it.
    const held = tally.get(FIELDS.find((f) => f.id === previous.field)) || 0;
    if (best[1] < held * 1.25) {
      return { ...previous, confident: true };
    }
  }

  // A conversation is rarely about exactly one thing. When a second field is
  // close behind, the hue lands between them -- which is what stops twelve
  // fields from meaning only twelve possible colours.
  let { hue, chroma } = best[0];
  if (second && second[1] >= best[1] * 0.45) {
    const share = (second[1] / (best[1] + second[1])) * 0.5;
    hue = mixHue(best[0].hue, second[0].hue, share);
    chroma = best[0].chroma * (1 - share) + second[0].chroma * share;
  }

  return { hue, chroma, field: best[0].id, confident: true };
}

/** Interpolate around the circle the short way -- 350 and 10 meet at 0. */
function mixHue(from, to, amount) {
  let delta = ((to - from + 540) % 360) - 180;
  return (from + delta * amount + 360) % 360;
}
