import { useCallback, useRef, useState } from "react";

import { useDialog } from "./Dialog";
import { Icon } from "./Icon";
import { ModelMenu } from "./ModelMenu";
import { seedFromContext } from "../lib/autotheme";
import { swatchOf } from "../lib/theme";

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

const LIST_KEY = "unified-llm-rail-list-open";

const DESTINATIONS = [
  { id: "chat", label: "Chat", icon: "chat_bubble" },
  { id: "projects", label: "Projects", icon: "folder" },
  { id: "memory", label: "Memory", icon: "memory" },
  { id: "skills", label: "Skills", icon: "skills" },
  // Named for the page it opens. It was "tools", but `view === "tools"` has
  // never had a branch of its own -- it fell through to the Settings page,
  // which is also where the mark at the head of the rail goes. The label was
  // describing a screen that does not exist; the id now matches the one it
  // actually lands on.
  { id: "settings", label: "Settings", icon: "settings" },
];

/* The destination's name.
 *
 * One copy now. There used to be a second, set vertically, which was what the
 * rail showed while shut -- the glyphs were held at zero width and the labels
 * were the whole of the closed state. Shut, it is a column of icons instead,
 * so the sideways copy has nothing left to do.
 *
 * This one stays in the DOM at every width, hidden with opacity rather than
 * `display: none`, which is what keeps the button's accessible name the same
 * whether the rail is open or shut. A screen reader reads "Calendar" either
 * way; only the eye sees the difference. */
function Label({ label }) {
  return <span className="lbl-h">{label}</span>;
}

/* The colour a conversation wears in the list.
 *
 * Its own accent if it has one, otherwise its project's -- the same order
 * `useTheme` resolves in when it dresses the conversation itself, so a chat
 * shows the same colour in the rail as it does once opened.
 *
 * Null when neither has an accent, and the row draws no bead at all. A bead on
 * every conversation would be the app's own colour repeated down the whole
 * list, which says nothing about any of them. */
function accentOf(session, projects) {
  // Filed conversations only. The bead is how this list says which project a
  // row belongs to, now that the folders it used to sit inside are gone -- so
  // on a conversation that belongs to none it is a dot with nothing to report,
  // and a column of them says less than a column without.
  if (!session.project_id) return null;

  const project = projects?.find((p) => p.id === session.project_id);
  if (!project) return null;

  // Its own accent still wins where it has one, so the colour in the list is
  // the colour the conversation actually opens in -- the same order
  // `useTheme` resolves in.
  if (session.theme) return swatchOf(session.theme, seedFromContext(session));
  if (!project.theme) return null;
  // A project has a name rather than a title and no messages, so an auto
  // accent seeds from what little it has.
  return swatchOf(
    project.theme,
    seedFromContext({ title: project.name, id: project.id }),
  );
}

/* One conversation, wherever it is filed. */
function SessionRows({ sessions, projects, activeId, onOpenSession, onDelete, empty }) {
  const { confirm } = useDialog();
  if (sessions.length === 0) {
    return (
      <li data-empty="true">
        <span className="navrail-empty">{empty}</span>
      </li>
    );
  }
  return sessions.map((session) => {
    const accent = accentOf(session, projects);
    return (
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
        {/* The bead is how a conversation shows which project it belongs to,
            now that the folders it used to sit inside are gone from here.
            It reads its project's colour unless it has one of its own. */}
        {accent ? (
          <span className="accent-bead" aria-hidden="true" style={{ background: accent }} />
        ) : null}
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
    );
  });
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
  onFileSession,
  activeId,
  onOpenSession,
  onNewSession,
  onDelete,
}) {
  // Whether the conversation list is unfolded under Chat. A layout preference,
  // so it persists like the pin does -- someone who keeps it shut wants it shut
  // tomorrow as well.
  const [listOpen, setListOpen] = useState(
    () => localStorage.getItem(LIST_KEY) !== "0",
  );
  // Whether a dragged conversation is currently over the list. One at a time,
  // so one id rather than a set.
  const [dropOver, setDropOver] = useState(null);
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
      {/* The reveal. Unpinned, the rail takes no width at all, so there is
          nothing left to hover -- this strip along the very edge of the window
          is what the pointer arrives at instead.

          It sits inside `.navrail` rather than beside it so the existing
          enter/leave handlers do the work: entering any descendant enters the
          rail, which is the same route the opened panel already takes when the
          pointer moves onto it from the sheet.

          Hidden when pinned (there is nothing to reveal) and on narrow screens,
          where the rail keeps a visible strip and opens on tap -- a hover
          target is no use to a finger. */}
      <div className="navrail-edge" aria-hidden="true" />

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
            {/* Chat is both the destination and the fold above its own list,
                so one press does both: go there, and show what is there.
                `aria-expanded` describes the list it controls; `aria-current`
                describes where you are. */}
            <button
              type="button"
              className="navrail-section"
              aria-current={view === "chat" ? "page" : undefined}
              aria-expanded={listOpen}
              aria-controls="navrail-session-list"
              onClick={() => {
                const next = !listOpen;
                setListOpen(next);
                localStorage.setItem(LIST_KEY, next ? "1" : "0");
                onView("chat");
              }}
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

                <button type="button" className="navrail-new" onClick={onNewSession}>
                  + New conversation
                </button>

                {/* Projects first, each one a fold of its own, then the
                    conversations that are in no project. A chat filed into a
                    project appears only there -- listing it twice would make
                    the counts lie. */}
                <div hidden={!listOpen}>
                  {/* Every conversation, in one flat list.
                   *
                   * The projects used to be here too, each an unfoldable
                   * section with its own chats nested inside and its own
                   * context menu -- which made this a second, worse copy of
                   * the Projects page inside a 252px column. Projects live in
                   * one place now; this is the list of conversations, and a
                   * chat's project shows as its colour rather than as a
                   * folder it has to be dug out of.
                   *
                   * Every session, not just the unfiled ones. With the folds
                   * gone a filed chat would otherwise have nowhere to appear
                   * in the rail at all. */}
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
                      // Dropping on the list takes a chat out of its project.
                      // Filing it into one is done on the Projects page, which
                      // is where the projects are.
                      if (id) onFileSession(id, null);
                    }}
                  >
                    <SessionRows
                      sessions={sessions}
                      projects={projects}
                      activeId={activeId}
                      onOpenSession={onOpenSession}
                      onDelete={onDelete}
                      empty="Nothing yet"
                    />
                  </ul>
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
