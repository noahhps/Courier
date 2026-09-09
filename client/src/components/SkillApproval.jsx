/**
 * A skill asking to run, and the three widths you can say yes at.
 *
 * The turn is stopped on the server while this is on screen. That is the whole
 * reason it is drawn as a card in the thread rather than as a toast or a modal:
 * it is not a notification about something that happened, it is the thing the
 * conversation is currently waiting on, and it belongs at the point in the
 * transcript where the waiting is.
 *
 * The arguments are shown, not summarised. "list_directory wants to run" says
 * almost nothing; "list_directory on ~/Documents" is the whole decision, and a
 * prompt that hides the part you would judge is a prompt that trains you to
 * press the green button. They are drawn as plain text through React, so a path
 * or a query is escaped by the renderer rather than by us.
 *
 * Three answers, ordered by how much they give away, narrowest first -- the
 * cheapest choice should be the easiest one to land on. Refusing is not styled
 * as the dangerous option, because it is not: a denied call comes back to the
 * model as a sentence and the answer carries on without it.
 */
export function SkillApproval({ skill, onDecide }) {
  const { id, answered, expired } = skill.approval;
  const args = skill.arguments && Object.keys(skill.arguments).length ? skill.arguments : null;

  return (
    <div className="approval" data-answered={answered ? "" : undefined}>
      <div className="approval-head">
        <span className="approval-label mi">Wants to run</span>
        <span className="approval-name">{skill.name}</span>
      </div>

      {args ? (
        <dl className="approval-args">
          {Object.entries(args).map(([key, value]) => (
            <div className="approval-arg" key={key}>
              <dt>{key}</dt>
              {/* String(value) rather than a bare {value}: an argument can be a
                  number, a boolean or an object, and React renders the last of
                  those as nothing at all -- an empty line where the thing you
                  are being asked to approve should be. */}
              <dd>{typeof value === "string" ? value : JSON.stringify(value)}</dd>
            </div>
          ))}
        </dl>
      ) : (
        <p className="approval-args" data-empty="">No arguments</p>
      )}

      {expired ? (
        <p className="approval-note">
          That prompt is no longer waiting — the turn was stopped, or it stood
          long enough to be refused for you.
        </p>
      ) : answered ? (
        <p className="approval-note">
          {answered === "deny" ? "Declined." : "Allowed — running it now…"}
        </p>
      ) : (
        <div className="approval-actions">
          <button type="button" className="approval-btn" onClick={() => onDecide(id, "allow_once")}>
            Allow once
          </button>
          <button
            type="button"
            className="approval-btn"
            onClick={() => onDecide(id, "allow_session")}
            title="Stop asking for this skill in this conversation"
          >
            Allow in this chat
          </button>
          <button
            type="button"
            className="approval-btn"
            onClick={() => onDecide(id, "allow_always")}
            title="Stop asking for this skill everywhere, until you change it in Skills"
          >
            Always allow
          </button>
          <button
            type="button"
            className="approval-btn"
            data-deny=""
            onClick={() => onDecide(id, "deny")}
          >
            Deny
          </button>
        </div>
      )}
    </div>
  );
}
