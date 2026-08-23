/* Illustration content for the screens whose backend does not exist yet.
 *
 * The Memory and Skills screens come from the design canvas, and the server
 * has no endpoint behind either of them: `memory_facts` has not been written,
 * the `chunks` table is empty, and the tool registry is still four stub files.
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

export const FACTS = [
  {
    id: "f1",
    text: "Rents a two-bed flat in **Leeds**; tenancy started March 2026.",
    from: "told",
    confidence: 1,
    when: "4 Jul",
    used: "12 answers",
    pinned: true,
  },
  {
    id: "f2",
    text: "Boiler reported broken **8 July**, still unrepaired.",
    from: "inferred",
    confidence: 0.62,
    when: "2 min ago",
    check: true,
  },
  {
    id: "f3",
    text: "Wants the short answer first, detail only if asked.",
    from: "told",
    confidence: 1,
    when: "19 Jun",
  },
];

export const FADING = {
  text: "Looking for a plumber this week",
  note: "I'll let this fade once the job's done",
};

export const FACT_FILTERS = ["All", "Home", "How I answer", "People", "Fading"];

export const CORPORA = [
  {
    id: "c1",
    name: "Moving house",
    count: 7,
    tint: "#8f8ede",
    note: "Tenancy agreement, 2 emails, photos of the boiler",
  },
  {
    id: "c2",
    name: "Everything else",
    count: 41,
    tint: "#c9d3cc",
    note: "Only you can see these · never used for training",
  },
];

export const MEMORY_SETTINGS = [
  { id: "between", label: "Remember between chats", on: true },
  { id: "confirm", label: "Ask before saving anything", on: true },
  { id: "share", label: "Share across projects", on: false },
];

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
