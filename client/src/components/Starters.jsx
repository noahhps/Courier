/* The empty conversation.
 *
 * Four openers rather than a blank box. They are written as the sentence that
 * actually gets sent, not as a category -- "Summarise this" is a label, and a
 * label still leaves you with a cursor and nothing to type.
 *
 * Each one is picked to exercise something this build genuinely does: the
 * clock skill, a pasted-in file, long-form reading, and a plain question. A
 * starter that promises something the server cannot do is worse than no
 * starter at all.
 */

const STARTERS = [
  {
    title: "Ask what I can do",
    prompt: "What skills do you have available, and when would each one be useful?",
  },
  {
    title: "Check a time zone",
    prompt: "What time is it in Tokyo right now, and how far ahead of me is that?",
  },
  {
    title: "Explain some code",
    prompt:
      "Explain what this code does, step by step, and point out anything that looks wrong:\n\n",
  },
  {
    title: "Think through a decision",
    prompt:
      "Help me think through a decision. I will describe the options and the constraints, and I want the trade-offs laid out rather than a recommendation up front.\n\n",
  },
];

/* Above the composer. The greeting belongs on the side of the box the eye
   reaches first, which is not the side the openers belong on. */
export function StartersHead() {
  return (
    <div className="starters-head">
      <h2 className="h">What are we working on?</h2>
      <p className="p">
        Everything here runs on your own hardware. Nothing leaves the machine
        unless you send it to the cloud provider on purpose.
      </p>
    </div>
  );
}

/* Below it, so the composer itself lands on the centre line rather than being
   pushed under it by whatever sits above. */
export function Starters({ onPick }) {
  return (
    <div className="starters">
      <div className="starters-grid">
        {STARTERS.map((starter) => (
          <button
            key={starter.title}
            type="button"
            className="starter"
            // The full prompt, so a screen reader hears what pressing this
            // will actually put in the box rather than just the label.
            aria-label={`Start with: ${starter.prompt.trim()}`}
            onClick={() => onPick(starter.prompt)}
          >
            <span className="starter-title">{starter.title}</span>
            <span className="starter-body">{starter.prompt.trim()}</span>
          </button>
        ))}
      </div>
    </div>
  );
}
