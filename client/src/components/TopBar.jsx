import { Icon } from "./Icon";

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
}) {
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

      <button className="icon-btn" aria-label="New conversation" onClick={onNewSession}>
        <Icon name="plus" />
      </button>
    </header>
  );
}
