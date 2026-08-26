import { useEffect, useState } from "react";

import { useApi } from "../lib/api-context";
import { saveDocument } from "../lib/files";

// A skill that produced a file says so by naming the path it can be fetched
// from. Reading it out of the result rather than waiting for the model to
// write a link means the download is always offered: the model paraphrases
// often enough ("you can download it from the link above", with no link) that
// the affordance cannot depend on it remembering.
const DOCUMENT = /\/api\/documents\/(\S+?)(?=[.,)\]]?(?:\s|$))/;

// Save a new document to the browser's download folder without being asked.
// On by default: a file the assistant just wrote is almost always wanted, and
// the alternative is a button people forget to press.
const AUTO_KEY = "unified-llm-autosave";

export function autoSaveEnabled() {
  try {
    return localStorage.getItem(AUTO_KEY) !== "0";
  } catch {
    return true; // private window, blocked storage: default to helpful
  }
}

export function setAutoSave(on) {
  try {
    localStorage.setItem(AUTO_KEY, on ? "1" : "0");
  } catch {
    /* nothing to do; the preference just will not persist */
  }
}

// Paths already sent to the browser this session. A trace re-renders on every
// token of the answer that follows it, and without this each render would
// start another download of the same file.
const alreadySaved = new Set();

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
  const [saving, setSaving] = useState(false);
  const api = useApi();
  const running = skill.result === undefined;
  const args = Object.entries(skill.arguments || {});
  const match = !running && DOCUMENT.exec(skill.result || "");
  // A string, not the match array: the array is a new object every render and
  // would retrigger the effect below forever.
  const encoded = match ? match[1] : null;
  const filename = encoded ? decodeURIComponent(encoded) : null;

  useEffect(() => {
    if (!encoded || !api || !autoSaveEnabled()) return;
    const path = `/api/documents/${encoded}`;
    if (alreadySaved.has(path)) return;
    alreadySaved.add(path);
    // Failure is silent on purpose -- the Save button below is still there,
    // and an automatic action that failed should not interrupt the answer.
    saveDocument(api, path, filename).catch(() => alreadySaved.delete(path));
  }, [encoded, filename, api]);

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

      {encoded ? (
        <button
          type="button"
          className="btn skill-trace-save"
          disabled={saving}
          onClick={async () => {
            setSaving(true);
            try {
              await saveDocument(api, `/api/documents/${encoded}`, filename);
            } finally {
              setSaving(false);
            }
          }}
        >
          {saving ? "Saving…" : `Save ${filename}`}
        </button>
      ) : null}

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
