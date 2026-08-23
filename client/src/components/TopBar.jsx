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
export function TopBar({ title, badge, onNewSession }) {
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

      <button className="icon-btn" aria-label="New conversation" onClick={onNewSession}>
        <Icon name="plus" />
      </button>
    </header>
  );
}
