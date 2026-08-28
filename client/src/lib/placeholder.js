/* Illustration content for the screens whose backend does not exist yet.
 *
 * The Memory screen no longer appears here: `memory_facts`, `/api/memory` and
 * the chunk index all exist now, and everything that page shows comes from the
 * server. What remains is the Skills screen's second tier -- suggestions and
 * usage history, which nothing yet records.
 *
 * Every unbacked value in the UI is imported from here and from nowhere else.
 * That is the point of the module -- when the endpoints land, this file is the
 * checklist of what has to be replaced, and `grep placeholder` finds every
 * screen still telling a story rather than reporting a fact.
 *
 * Nothing here is written to, and nothing persists. The screens that render it
 * say so on the screen itself; see the `.unbacked` banner.
 */

export const PLACEHOLDER = true;

export const PENDING_SKILLS = [
  {
    id: "s1",
    name: "Letter writer",
    origin: "I made this",
    authored: true,
    body:
      "You've asked me for formal letters four times this month, so I put together a small skill that drafts them in your name and address, ready to sign.",
  },
  {
    id: "s2",
    name: "Your email",
    origin: "Off",
    authored: false,
    body:
      "Let me read your inbox so I can answer things like “what did the landlord actually promise?”. Reading only — I can't send or delete.",
  },
];

export const SKILL_USAGE = [
  {
    id: "u1",
    name: "Search my files",
    note: "In most answers",
    tint: null,
    bars: [64, 84, 52, 100, 74, 90],
    hotFrom: 3,
  },
  {
    id: "u2",
    name: "Calendar",
    note: "31 times",
    tint: "green",
    bars: [34, 56, 44, 66, 50, 62],
    coldTo: 2,
  },
  {
    id: "u3",
    name: "Photo cleanup",
    note: "Barely used",
    tint: "quiet",
    bars: [14, 8, 18, 6, 12, 5],
    hideable: true,
  },
];

export const SKILL_UPDATE = {
  name: "Search my files improved itself",
  body:
    "Version 4.1 reads tables inside PDFs. Six of your last ten questions would have got a better answer.",
};

/* The mid-answer permission ask from artboard 1f. Kept here because nothing
 * requests permissions yet -- the tool loop that would raise this is the work
 * described in docs/extending.md section 4. */
export const SAMPLE_ASK = {
  tool: "Read your email",
  lead:
    "I can answer this from the order log, but the promise itself was made over email. Want me to look there?",
  bullets: [
    "Only messages mentioning Northmill, from this year",
    "Reading only — it can't send or delete anything",
    "Nothing gets remembered unless you say so",
  ],
  fallback:
    "Answer without it and I'll rely on the log alone — I'll say so if that leaves a gap.",
};
