import { useEffect, useRef, useState } from "react";

import { Icon } from "./Icon";
import { ThemePicker } from "./ThemePicker";
import { swatchOf } from "../lib/theme";

/**
 * The 64px header from 1a: the conversation's name, what the system is doing,
 * and the one control that belongs to the thread rather than to the app.
 *
 * There is no menu button. Picking a conversation is the rail's job now -- the
 * list unfolds under Chat when the rail opens.
 *
 * `badge` is the provider/model line the turn reports back. It is rendered as
 * the design's dot-plus-label state rather than as a pill, so "cloud" reads as
 * a condition of the conversation rather than as a piece of chrome.
 */
export function TopBar({
  title,
  badge,
  projects,
  projectId,
  onProject,
  canFile,
  onNewSession,
  accent,
  onAccent,
  seed,
  contextSeed,
  resolvedAccent,
  accentSource,
}) {
  const [accentsOpen, setAccentsOpen] = useState(false);
  const accentNode = useRef(null);

  // Same dismissal as the provider menu at the foot of the rail: away, or
  // Escape, and neither listener exists while the panel is shut.
  useEffect(() => {
    if (!accentsOpen) return undefined;
    const away = (event) => {
      if (accentNode.current && !accentNode.current.contains(event.target)) {
        setAccentsOpen(false);
      }
    };
    const key = (event) => {
      if (event.key === "Escape") setAccentsOpen(false);
    };
    document.addEventListener("mousedown", away);
    document.addEventListener("keydown", key);
    return () => {
      document.removeEventListener("mousedown", away);
      document.removeEventListener("keydown", key);
    };
  }, [accentsOpen]);

  return (
    <header className="topbar">
      <span className="title">{title}</span>

      {badge ? (
        <span className="state" data-tone={badge.tone || undefined}>
          <i aria-hidden="true" />
          <span className="mi">{badge.text}</span>
        </span>
      ) : null}

      <div className="spacer" />

      {/* Filing lives here rather than on the rail row: the rail is 252px of
          conversation titles, and a select crammed into one would be a target
          nobody hits. This is about the conversation you are reading, which is
          what the rest of this bar is about too.

          Hidden until there is a conversation to file -- a new, unsent chat
          has no id yet, and offering to file it would fail on submit. */}
      {canFile ? (
        <label className="topbar-project">
          <span className="mi">Project</span>
          <select
            value={projectId || ""}
            aria-label="File this conversation"
            onChange={(event) => onProject(event.target.value || null)}
          >
            <option value="">None</option>
            {projects.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
        </label>
      ) : null}

      {/* The accent, for this conversation only. Beside filing because both
          are properties of the thread rather than of the app, and hidden for
          the same reason: an unsent chat has no id to write one against. */}
      {canFile ? (
        <div className="topbar-accent" ref={accentNode}>
          <button
            type="button"
            className="icon-btn accent-trigger"
            aria-label="Accent for this conversation"
            aria-expanded={accentsOpen}
            onClick={() => setAccentsOpen((was) => !was)}
          >
            {/* The resolved accent, not this chat's own: a conversation
                wearing its project's green is wearing green, and a bead that
                went grey because the decision was made elsewhere would be
                reporting on the plumbing rather than on the colour. */}
            <span
              className="accent-bead"
              style={{ background: swatchOf(resolvedAccent, seed) }}
            />
          </button>

          {accentsOpen ? (
            <div className="popover popover-accents" role="dialog" aria-label="Accent">
              <div className="popover-head mi" data-strong>
                Accent · this chat
              </div>
              <ThemePicker
                value={accent}
                onChange={onAccent}
                scope="chat"
                seed={contextSeed}
                inheritedLabel={
                  projectId ? "Follow the project" : "Follow the app-wide accent"
                }
              />
              <div className="popover-foot">
                {accent
                  ? "Set here, so this conversation keeps it."
                  : accentSource === "project"
                    ? "Coming from the project this chat is filed under."
                    : "Coming from the app-wide accent."}
              </div>
            </div>
          ) : null}
        </div>
      ) : null}

      <button className="icon-btn" aria-label="New conversation" onClick={onNewSession}>
        <Icon name="plus" />
      </button>
    </header>
  );
}
