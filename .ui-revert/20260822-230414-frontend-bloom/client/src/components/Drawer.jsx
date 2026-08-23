import { AccentPicker } from "./AccentPicker";
import { Icon } from "./Icon";

export function Drawer({
  open,
  sessions,
  activeId,
  accent,
  onAccent,
  onOpenSession,
  onDelete,
  onClose,
}) {
  // Always mounted. Unmounting on close would take the panel out of the DOM
  // before it could animate out; hidden state is carried by the attribute on
  // .app, and `visibility` keeps the closed panel out of the focus order.
  return (
    <>
      <aside className="drawer" aria-hidden={open ? undefined : "true"}>
        <div className="drawer-head">
          <span>Conversations</span>
          <button className="icon-btn" aria-label="Close" onClick={onClose}>
            <Icon name="close" />
          </button>
        </div>
        <ul className="session-list">
          {sessions.map((session) => (
            <li key={session.id} data-active={String(session.id === activeId)}>
              <button
                className="session-open"
                onClick={() => {
                  onClose();
                  onOpenSession(session.id);
                }}
              >
                {session.title || "Untitled"}
              </button>
              <button
                className="session-delete"
                aria-label="Delete conversation"
                onClick={(event) => {
                  event.stopPropagation();
                  if (confirm("Delete this conversation?")) onDelete(session.id);
                }}
              >
                ×
              </button>
            </li>
          ))}
        </ul>
        <AccentPicker value={accent} onChange={onAccent} />
      </aside>
      <div className="scrim" onClick={onClose} />
    </>
  );
}
