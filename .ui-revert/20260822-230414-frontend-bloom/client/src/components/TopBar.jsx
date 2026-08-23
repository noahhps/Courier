import { Icon } from "./Icon";

export function TopBar({ title, badge, onOpenDrawer, onNewSession }) {
  return (
    <header className="topbar">
      <button className="icon-btn" aria-label="Sessions" onClick={onOpenDrawer}>
        <Icon name="menu" />
      </button>
      <span className="title">{title}</span>
      {badge ? (
        <span className="badge" data-tone={badge.tone || undefined}>
          {badge.text}
        </span>
      ) : null}
      <button className="icon-btn" aria-label="New conversation" onClick={onNewSession}>
        <Icon name="plus" />
      </button>
    </header>
  );
}
