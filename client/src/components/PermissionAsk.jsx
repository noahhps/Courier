/* The mid-answer permission card from artboard 1f.
 *
 * Written as a real component rather than a mockup because the tool loop will
 * need exactly this: a tool declares `requires_approval`, the turn stops, and
 * the person answers before anything runs. The three bullets are the contract
 * -- scope, what it cannot do, and what happens to what it sees -- and every
 * approval prompt should have to fill all three in.
 *
 * `onDecide` is optional. Without it the card renders inert, which is how the
 * Skills screen shows it today.
 */
export function PermissionAsk({ ask, onDecide }) {
  const inert = !onDecide;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "18px" }}>
      {ask.lead ? <p className="p" style={{ margin: 0 }}>{ask.lead}</p> : null}

      <div className="sur ask">
        <div className="skill-head">
          <i style={{ background: "var(--blue)" }} />
          <span className="h" style={{ fontSize: "16px", flex: 1 }}>
            {ask.tool}
          </span>
          <span className="mi">New tool</span>
        </div>

        <ul>
          {ask.bullets.map((line) => (
            <li key={line}>
              <i aria-hidden="true" />
              <span>{line}</span>
            </li>
          ))}
        </ul>

        <div className="skill-actions">
          <button
            type="button"
            className="btnp"
            disabled={inert}
            onClick={() => onDecide?.("once")}
          >
            Just this once
          </button>
          <button
            type="button"
            className="btn"
            disabled={inert}
            onClick={() => onDecide?.("always")}
          >
            Always allow
          </button>
          <span className="spacer" />
          <button
            type="button"
            className="mi"
            disabled={inert}
            onClick={() => onDecide?.("never")}
          >
            No
          </button>
        </div>
      </div>

      {ask.fallback ? <p className="caveat">{ask.fallback}</p> : null}
    </div>
  );
}
