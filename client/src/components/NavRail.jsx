import { useCallback, useRef, useState } from "react";

import { useDialog } from "./Dialog";
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

const DELETE_WARNING = (name) =>
  `Delete the project "${name}"? Its conversations are kept and become unfiled.`;

const LIST_KEY = "unified-llm-rail-list-open";

const DESTINATIONS = [
  { id: "chat", label: "Chat", icon: "chat_bubble" },
  { id: "projects", label: "Projects", icon: "folder" },
  { id: "calendar", label: "Calendar", icon: "calendar" },
  { id: "memory", label: "Memory", icon: "memory" },
  { id: "skills", label: "Skills", icon: "skills" },
  { id: "tools", label: "Tools", icon: "tools" },
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

/* One conversation, wherever it is filed. Pulled out because it is now
   rendered twice -- inside a project, and under Conversations for the ones
   that are not in any -- and two copies of a delete confirmation is exactly
   how the two drift apart. */
function SessionRows({ sessions, activeId, onOpenSession, onDelete, empty }) {
  const { confirm } = useDialog();
  if (sessions.length === 0) {
    return (
      <li data-empty="true">
        <span className="navrail-empty">{empty}</span>
      </li>
    );
  }
  return sessions.map((session) => (
    <li
      key={session.id}
      data-active={String(session.id === activeId)}
      draggable
      onDragStart={(event) => {
        // A custom type rather than text/plain, so dragging a conversation
        // over a text field somewhere does not offer to paste an id.
        event.dataTransfer.setData("text/session", session.id);
        event.dataTransfer.effectAllowed = "move";
      }}
    >
      <button className="navrail-session" onClick={() => onOpenSession(session.id)}>
        {session.title || "Untitled"}
      </button>
      <button
        className="navrail-session-delete"
        aria-label={`Delete ${session.title || "Untitled"}`}
        onClick={async (event) => {
          // Stopped synchronously, before the await: the row underneath opens
          // the conversation, and letting the click through while the dialog
          // is deciding would open the very thing being deleted.
          event.stopPropagation();
          const yes = await confirm("Delete this conversation?", {
            title: "Delete conversation",
            confirmLabel: "Delete",
            destructive: true,
          });
          if (yes) onDelete(session.id);
        }}
      >
        ×
      </button>
    </li>
  ));
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
  projects,
  onNewProject,
  onRenameProject,
  onDeleteProject,
  onNewSessionIn,
  onFileSession,
  activeId,
  onOpenSession,
  onNewSession,
  onDelete,
}) {
  const { ask, confirm } = useDialog();
  // Whether the conversation list is unfolded under Chat. A layout preference,
  // so it persists like the pin does -- someone who keeps it shut wants it shut
  // tomorrow as well.
  const [listOpen, setListOpen] = useState(
    () => localStorage.getItem(LIST_KEY) !== "0",
  );
  // Which projects are unfolded. A Set in state rather than a flag per
  // project, so adding a project needs no new state.
  const [openProjects, setOpenProjects] = useState(() => new Set());
  // The folder a dragged conversation is currently over, and the folder whose
  // right-click menu is open. Both are one-at-a-time, so both are a single id.
  const [dropOver, setDropOver] = useState(null);
  const [menu, setMenu] = useState(null);
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
              <Icon name={DESTINATIONS[0].icon} />
              <Label label={DESTINATIONS[0].label} />
            </button>

            {/* Collapsed to nothing until the rail opens. Deliberately not
                `inert` while shut: the rail opens on focus, so making its
                contents unfocusable would mean a keyboard user could never
                open it -- tabbing in is the only way they have. */}
            <div className="navrail-sessions">
              <div className="navrail-sessions-inner">
                {/* The section header doubles as the fold. `aria-controls`
                    rather than nesting the list inside the button: a button
                    wrapping a list of buttons is not a thing a screen reader
                    can describe. */}
                <button
                  type="button"
                  className="navrail-section"
                  aria-expanded={listOpen}
                  aria-controls="navrail-session-list"
                  onClick={() => {
                    const next = !listOpen;
                    setListOpen(next);
                    localStorage.setItem(LIST_KEY, next ? "1" : "0");
                  }}
                >
                  <Icon name={listOpen ? "chat_bubble" : "chat_bubble_outline"} />
                  <span>Conversations</span>
                  <span className="navrail-chevron" aria-hidden="true">
                    {listOpen ? "⌄" : "›"}
                  </span>
                </button>

                <button type="button" className="navrail-new" onClick={onNewSession}>
                  + New conversation
                </button>

                {/* Projects first, each one a fold of its own, then the
                    conversations that are in no project. A chat filed into a
                    project appears only there -- listing it twice would make
                    the counts lie. */}
                <div hidden={!listOpen}>
                  {projects.map((project) => {
                    const mine = sessions.filter((s) => s.project_id === project.id);
                    const unfolded = openProjects.has(project.id);
                    return (
                      <div
                        key={project.id}
                        className="navrail-project"
                        data-over={dropOver === project.id ? "" : undefined}
                        onDragOver={(event) => {
                          if (event.dataTransfer.types.includes("text/session")) {
                            // Required, or the browser refuses the drop.
                            event.preventDefault();
                            setDropOver(project.id);
                          }
                        }}
                        onDragLeave={() =>
                          setDropOver((was) => (was === project.id ? null : was))
                        }
                        onDrop={(event) => {
                          event.preventDefault();
                          setDropOver(null);
                          const id = event.dataTransfer.getData("text/session");
                          if (id) {
                            onFileSession(id, project.id);
                            // Opened, so the conversation is visible where it
                            // just landed rather than seeming to disappear.
                            setOpenProjects((was) => new Set(was).add(project.id));
                          }
                        }}
                        onContextMenu={(event) => {
                          event.preventDefault();
                          setMenu(menu === project.id ? null : project.id);
                        }}
                      >
                        <button
                          type="button"
                          className="navrail-section"
                          aria-expanded={unfolded}
                          onClick={() =>
                            setOpenProjects((was) => {
                              const next = new Set(was);
                              next.has(project.id)
                                ? next.delete(project.id)
                                : next.add(project.id);
                              return next;
                            })
                          }
                        >
                          {/* One glyph whatever the fold is doing. The two
                              folder files differ only by the tab line inside
                              them, which is gone by 14px -- swapping them
                              would claim a state the icon cannot show. The
                              chevron at the far end reports it instead. */}
                          <Icon name="folder" />
                          <span>{project.name}</span>
                          <span className="navrail-count mi">{mine.length}</span>
                          <span className="navrail-chevron" aria-hidden="true">
                            {unfolded ? "⌄" : "›"}
                          </span>
                        </button>
                        {/* Right-click. Kept to the three things you would
                            otherwise have to leave the rail for. */}
                        {menu === project.id ? (
                          <div className="navrail-menu" role="menu">
                            <button
                              type="button"
                              role="menuitem"
                              onClick={() => {
                                setMenu(null);
                                setOpenProjects((was) => new Set(was).add(project.id));
                                onNewSessionIn(project.id);
                              }}
                            >
                              New chat here
                            </button>
                            <button
                              type="button"
                              role="menuitem"
                              onClick={async () => {
                                setMenu(null);
                                const next = await ask("Rename project", {
                                  value: project.name,
                                  confirmLabel: "Rename",
                                });
                                if (next) onRenameProject(project.id, next);
                              }}
                            >
                              Rename
                            </button>
                            <button
                              type="button"
                              role="menuitem"
                              onClick={async () => {
                                setMenu(null);
                                const yes = await confirm(DELETE_WARNING(project.name), {
                                  title: "Delete project",
                                  confirmLabel: "Delete",
                                  destructive: true,
                                });
                                if (yes) onDeleteProject(project.id);
                              }}
                            >
                              Delete project
                            </button>
                          </div>
                        ) : null}

                        <ul hidden={!unfolded}>
                          <SessionRows
                            sessions={mine}
                            activeId={activeId}
                            onOpenSession={onOpenSession}
                            onDelete={onDelete}
                            empty="Nothing filed here"
                          />
                        </ul>
                        <button
                          type="button"
                          className="navrail-project-new"
                          hidden={!unfolded}
                          onClick={() => onNewSessionIn(project.id)}
                        >
                          + New chat here
                        </button>
                      </div>
                    );
                  })}

                  <ul
                    id="navrail-session-list"
                    data-over={dropOver === "unfiled" ? "" : undefined}
                    onDragOver={(event) => {
                      if (event.dataTransfer.types.includes("text/session")) {
                        event.preventDefault();
                        setDropOver("unfiled");
                      }
                    }}
                    onDragLeave={() =>
                      setDropOver((was) => (was === "unfiled" ? null : was))
                    }
                    onDrop={(event) => {
                      event.preventDefault();
                      setDropOver(null);
                      const id = event.dataTransfer.getData("text/session");
                      // null unfiles it -- dragging out is the same gesture.
                      if (id) onFileSession(id, null);
                    }}
                  >
                    <SessionRows
                      sessions={sessions.filter((s) => !s.project_id)}
                      activeId={activeId}
                      onOpenSession={onOpenSession}
                      onDelete={onDelete}
                      empty="Nothing yet"
                    />
                  </ul>

                  <button
                    type="button"
                    className="navrail-new"
                    onClick={async () => {
                      const name = await ask("Name for the new project", {
                        placeholder: "Project name",
                        confirmLabel: "Create",
                      });
                      if (name) onNewProject(name);
                    }}
                  >
                    + New project
                  </button>
                </div>
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
              <Icon name={destination.icon} />
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
