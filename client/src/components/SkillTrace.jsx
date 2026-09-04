import { useState } from "react";

/**
 * What the model reached for, while it reaches for it.
 *
 * Sits above the answer in the same column, because it happened before the
 * answer and reads as the working that produced it -- the same argument as
 * `Reasoning`, and the same collapse behaviour: open while it is the only
 * thing happening, foldable once there is a reply to read instead.
 *
 * A row with no `result` yet is still running. That state is the whole point
 * of the component: without it a turn that calls a slow skill looks identical
 * to a turn that has hung.
 */
function Row({ skill }) {
  const [open, setOpen] = useState(false);
  const running = skill.result === undefined;
  const args = Object.entries(skill.arguments || {});

  return (
    <div className="skill-trace-row" data-running={running ? "" : undefined}>
      <button
        type="button"
        className="skill-trace-head"
        aria-expanded={open}
        disabled={running}
        onClick={() => setOpen((was) => !was)}
      >
        <span className="skill-trace-dot" aria-hidden="true" />
        <span className="skill-trace-name">{skill.name}</span>
        {args.length ? (
          <span className="skill-trace-args">
            {args.map(([key, value]) => `${key}: ${value}`).join(", ")}
          </span>
        ) : null}
        <span className="spacer" />
        <span className="mi">{running ? "running" : open ? "hide" : "show"}</span>
      </button>

      {open && !running ? (
        <div className="skill-trace-body">{skill.result}</div>
      ) : null}
    </div>
  );
}

export function SkillTrace({ skills }) {
  if (!skills?.length) return null;

  const running = skills.some((s) => s.result === undefined);

  return (
    <div className="skill-trace" data-live={running ? "" : undefined}>
      <div className="skill-trace-label mi">
        {running
          ? `Using ${skills[skills.length - 1].name}…`
          : `Used ${skills.length === 1 ? "1 skill" : `${skills.length} skills`}`}
      </div>
      {skills.map((skill, index) => (
        <Row key={`${skill.name}-${index}`} skill={skill} />
      ))}
    </div>
  );
}
