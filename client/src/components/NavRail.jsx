import { useCallback, useRef, useState } from "react";

import { Icon } from "./Icon";
import { ModelMenu } from "./ModelMenu";

/* The rail from artboard 1a, which opens out under the pointer.
 *
 * Three destinations set vertically between two circles, and the circles are
 * controls rather than decoration: the one at the head opens this device's
 * settings, the one at the foot chooses which provider answers. Both stay put
 * and stay clickable when the rail is shut, which is most of the time -- they
 * are the two things you reach for without wanting to read a menu first.
 *
 * The foot circle also still reports reachability by its colour, so the thing
 * you click to change the provider is the same thing that tells you the
 * current one has stopped answering.
 *
 * Two modes. Left alone the rail opens on hover and closes again, overlaying
 * the conversation without reflowing it. Pinned, it stays open and takes real
 * width so the sheet sits beside it. Openness is computed here rather than in
 * CSS: hover, keyboard focus, the pin and an open menu all have to produce the
 * same visual state, and expressing that as four selector variants on a dozen
 * rules is how one of them ends up forgotten.
 */

const DESTINATIONS = [
  { id: "chat", label: "Chat" },
  { id: "memory", label: "Memory" },
  { id: "skills", label: "Skills" },
];

function Label({ label }) {
  return (
    <>
      {/* The vertical copy is decorative duplication: it is taken out of the
          accessibility tree so the button keeps one name in both states,
          whichever copy happens to be visible. */}
      <span className="lbl-v" aria-hidden="true">
        {label}
      </span>
      <span className="lbl-h">{label}</span>
    </>
  );
}

export function NavRail({
  view,
  onView,
  status,
  provider,
  onProvider,
  pinned,
  onTogglePin,
  resizable,
  resizing,
  onResizeStart,
  onResizeKey,
  railWidth,
  sessions,
  activeId,
  onOpenSession,
  onNewSession,
  onDelete,
}) {
  const [hovered, setHovered] = useState(false);
  const [focused, setFocused] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const node = useRef(null);

  // The menu counts: it is a child of the rail, so letting the rail collapse
  // underneath an open menu would leave the menu floating beside nothing.
  // So does a drag: the pointer leaves the rail almost immediately when it is
  // widening it, and a rail that shut halfway through its own resize would be
  // impossible to use.
  const open = pinned || hovered || focused || menuOpen || resizing;

  // Focus moving between two children fires blur then focus, which would flap
  // the panel shut and open again. Asking where focus actually landed after
  // the browser has moved it is the cheap fix.
  const handleBlur = useCallback(() => {
    requestAnimationFrame(() => {
      const el = node.current;
      if (el && !el.contains(document.activeElement)) setFocused(false);
    });
  }, []);

  const serving = status?.serving;
  // Grey when local is answering, ochre when it fell through to the cloud,
  // flat when nothing is reachable at all.
  const tone = serving === "cloud" ? "warn" : serving === "none" ? "down" : undefined;
  const label =
    provider === "local"
      ? status?.local?.model || "Local"
      : provider === "cloud"
        ? status?.cloud?.model || "Cloud"
        : serving === "cloud"
          ? "Cloud fallback"
          : serving === "none"
            ? "No model"
            : status?.local?.model || "Local model";

  return (
    <nav
      className="navrail"
      aria-label="Sections"
      ref={node}
      data-open={open ? "" : undefined}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      onFocus={() => setFocused(true)}
      onBlur={handleBlur}
    >
      <div className="navrail-inner">
        <div className="navrail-top">
          <button
            type="button"
            className="navrail-circle navrail-mark"
            aria-current={view === "settings" ? "page" : undefined}
            aria-label="This device — settings"
            title="This device"
            onClick={() => onView("settings")}
          />
          <span className="navrail-wordmark" aria-hidden="true">
            Assistant
          </span>
          <button
            type="button"
            className="navrail-pin"
            aria-pressed={pinned}
            aria-label={pinned ? "Unpin sidebar" : "Keep sidebar open"}
            title={pinned ? "Unpin — open on hover" : "Keep open"}
            // Shut, the pin is inert and out of the tab order, so tabbing into
            // a closed rail lands on Chat rather than on a control nobody can
            // see. It becomes reachable as soon as focus opens the panel.
            tabIndex={open ? 0 : -1}
            onClick={onTogglePin}
          >
            <Icon name={pinned ? "pinned" : "pin"} />
          </button>
        </div>

        <div className="navrail-dest">
          <div className="navrail-group">
            <button
              type="button"
              aria-current={view === "chat" ? "page" : undefined}
              onClick={() => onView("chat")}
            >
              <Label label={DESTINATIONS[0].label} />
            </button>

            {/* Collapsed to nothing until the rail opens. Deliberately not
                `inert` while shut: the rail opens on focus, so making its
                contents unfocusable would mean a keyboard user could never
                open it -- tabbing in is the only way they have. */}
            <div className="navrail-sessions">
              <div className="navrail-sessions-inner">
                <button type="button" className="navrail-new" onClick={onNewSession}>
                  + New conversation
                </button>

                <ul>
                  {sessions.map((session) => (
                    <li key={session.id} data-active={String(session.id === activeId)}>
                      <button
                        className="navrail-session"
                        onClick={() => onOpenSession(session.id)}
                      >
                        {session.title || "Untitled"}
                      </button>
                      <button
                        className="navrail-session-delete"
                        aria-label={`Delete ${session.title || "Untitled"}`}
                        onClick={(event) => {
                          event.stopPropagation();
                          if (confirm("Delete this conversation?")) onDelete(session.id);
                        }}
                      >
                        ×
                      </button>
                    </li>
                  ))}
                  {sessions.length === 0 ? (
                    <li data-empty="true">
                      <span className="navrail-empty">Nothing yet</span>
                    </li>
                  ) : null}
                </ul>
              </div>
            </div>
          </div>

          {DESTINATIONS.slice(1).map((destination) => (
            <button
              key={destination.id}
              type="button"
              aria-current={view === destination.id ? "page" : undefined}
              onClick={() => onView(destination.id)}
            >
              <Label label={destination.label} />
            </button>
          ))}
        </div>

        <div className="spacer" />

        <div className="navrail-foot">
          <button
            type="button"
            className="navrail-circle navrail-dot"
            data-tone={tone}
            aria-haspopup="menu"
            aria-expanded={menuOpen}
            aria-label={`Answering with ${label} — change`}
            title={label}
            onClick={() => setMenuOpen((was) => !was)}
          />
          <span className="navrail-status" aria-hidden="true">
            {label}
          </span>
        </div>
      </div>

      {/* The right edge, as a drag handle. Only while the rail is open: shut,
          its width is the icons' width and there is nothing to choose. */}
      {open && resizable ? (
        <div
          className="navrail-resize"
          role="separator"
          aria-orientation="vertical"
          aria-label="Sidebar width"
          aria-valuenow={railWidth}
          aria-valuemin={200}
          aria-valuemax={460}
          tabIndex={0}
          onPointerDown={onResizeStart}
          onKeyDown={onResizeKey}
          // Double-click restores the drawn width, which is otherwise only
          // reachable by dragging back to a number nobody remembers.
          onDoubleClick={() => onResizeKey({ key: "Reset", preventDefault() {} })}
        >
          <i />
        </div>
      ) : null}

      {/* Outside .navrail-inner on purpose: the inner clips its overflow so the
          panel can animate its width, which would slice a menu in half. */}
      <ModelMenu
        open={menuOpen}
        status={status}
        value={provider}
        onChange={onProvider}
        onClose={() => setMenuOpen(false)}
      />
    </nav>
  );
}
