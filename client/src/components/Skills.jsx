import {
  PENDING_SKILLS,
  SAMPLE_ASK,
  SKILL_UPDATE,
  SKILL_USAGE,
} from "../lib/placeholder";
import { PermissionAsk } from "./PermissionAsk";

/* Artboard 1c: the shelf that grows.
 *
 * Three tiers, and the distinction between them is the whole point of the
 * screen: what shipped with the app, what you connected, and what the
 * assistant wrote for itself and is asking to keep. The last of those is the
 * one that needs a decision, so it sits at the top.
 *
 * Nothing here is wired. `server/app/tools/` is four files, three of them
 * empty, and there is no tool loop for a skill to run inside -- so this screen
 * is the target the registry and bootstrap work is aimed at, drawn out.
 */

function Spark({ usage }) {
  return (
    <div className="spark" data-tint={usage.tint || undefined}>
      {usage.bars.map((height, index) => (
        <i
          key={index}
          style={{ height: `${height}%` }}
          data-hot={usage.hotFrom != null && index >= usage.hotFrom ? "" : undefined}
          data-cold={usage.coldTo != null && index < usage.coldTo ? "" : undefined}
        />
      ))}
    </div>
  );
}

export function Skills() {
  return (
    <div className="page">
      <div className="page-head" data-tint="green">
        <div className="sw" style={{ left: "-90px", top: "-110px", width: "280px", height: "280px", background: "#d5e8db" }} />
        <div className="sw" style={{ left: "120px", top: "-70px", width: "130px", height: "130px", background: "#f6efd4" }} />
        <div className="inner">
          <div>
            <h1 className="h">Skills</h1>
            <p>
              Things I can do. Some came with the app, some you connected, and
              some I made because you kept asking.
            </p>
          </div>
          <div className="actions">
            <button type="button" className="btn" disabled>
              Browse all
            </button>
            <button type="button" className="btnp" disabled>
              Connect an app
            </button>
          </div>
        </div>
      </div>

      <div className="unbacked">
        <span>
          <b>Not wired up yet.</b> There is no tool registry running and no loop
          to call one, so these are the design's examples — not skills you have.
        </span>
      </div>

      <div className="page-body unbacked-body" style={{ flexDirection: "column" }}>
        <div className="page-col" style={{ alignSelf: "stretch" }}>
          <div className="lane">
            <span className="mi" data-strong>
              Waiting on you · {PENDING_SKILLS.length}
            </span>
            <i />
          </div>

          <div className="skill-row">
            {PENDING_SKILLS.map((skill) => (
              <div
                key={skill.id}
                className={skill.authored ? "skill-pending" : "sur skill-pending"}
                style={skill.authored ? undefined : { background: "var(--surface)" }}
              >
                <div className="skill-head">
                  <i data-tint={skill.authored ? undefined : "grey"} />
                  <span className="h">{skill.name}</span>
                  <span className="mi">{skill.origin}</span>
                </div>
                <p className="skill-body">{skill.body}</p>
                <div className="skill-actions">
                  {skill.authored ? (
                    <>
                      <button type="button" className="btnp" disabled>
                        Keep it
                      </button>
                      <button type="button" className="btn" disabled>
                        See what it does
                      </button>
                      <span className="spacer" />
                      <span className="mi">Discard</span>
                    </>
                  ) : (
                    <>
                      <button type="button" className="btn" disabled>
                        Allow reading
                      </button>
                      <span className="mi">Ask me each time</span>
                    </>
                  )}
                </div>
              </div>
            ))}
          </div>

          <div className="lane" style={{ marginTop: "12px" }}>
            <span className="mi" data-strong>
              You use these most
            </span>
            <i />
            <span className="mi">Last 30 days</span>
          </div>

          <div className="skill-grid">
            {SKILL_USAGE.map((usage) => (
              <div key={usage.id} className="sur skill-card">
                <span className="h">{usage.name}</span>
                <Spark usage={usage} />
                <div className="mi" style={{ marginTop: "13px" }}>
                  {usage.note}
                  {usage.hideable ? (
                    <>
                      {" · "}
                      <span style={{ color: "var(--accent)" }}>hide it?</span>
                    </>
                  ) : null}
                </div>
              </div>
            ))}
          </div>

          {/* A skill that rewrote itself. The offer to go back is the part that
              matters: self-modification without a way out is a trap. */}
          <div className="callout" data-tint="ochre" style={{ marginTop: "12px" }}>
            <div style={{ flex: 1 }}>
              <span className="h" style={{ fontSize: "17px" }}>
                {SKILL_UPDATE.name}
              </span>
              <p style={{ margin: "7px 0 0", color: "var(--text-dim)", fontSize: "12.5px", lineHeight: 1.7 }}>
                {SKILL_UPDATE.body}
              </p>
            </div>
            <div style={{ display: "flex", gap: "9px", flex: "none" }}>
              <button type="button" className="btn" disabled>
                What changed
              </button>
              <button type="button" className="btn" style={{ color: "var(--text-dim)" }} disabled>
                Go back to 4.0
              </button>
            </div>
          </div>

          <div className="lane" style={{ marginTop: "12px" }}>
            <span className="mi" data-strong>
              How it asks
            </span>
            <i />
          </div>
          <PermissionAsk ask={SAMPLE_ASK} />
        </div>
      </div>
    </div>
  );
}
