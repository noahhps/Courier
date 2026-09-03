import { useEffect, useMemo, useRef, useState } from "react";

import { ThemePicker } from "./ThemePicker";
import { useDialog } from "./Dialog";
import { swatchOf } from "../lib/theme";

/* A project's two editable properties, in one popup.
 *
 * The colour used to open a swatch row inside the card and the name used to
 * turn the heading into an input -- two different disclosures for two settings
 * on the same folder, each shoving the conversations below it out of the way.
 * Both are here now, over the card rather than inside it, so opening either
 * one costs the page no layout at all.
 *
 * The name commits on Enter and on dismissal, and is abandoned on Escape.
 * Committing on dismissal is the part worth stating: the colour swatches are
 * in this same popup, so clicking one after typing a name has to keep the
 * name -- a blur that discarded it, which is what the inline rename did, would
 * throw the edit away for touching the control next to it.
 */
function ProjectEditor({ project, accent, seed, onRename, onAccent, onClose }) {
  const node = useRef(null);
  const [draft, setDraft] = useState(project.name);
  // Read through a ref by the dismissal handlers, which are bound once and
  // would otherwise close over the name as it was when the popup opened.
  const latest = useRef(draft);
  latest.current = draft;

  useEffect(() => {
    const commit = () => {
      const clean = latest.current.trim();
      if (clean && clean !== project.name) onRename(clean);
      onClose();
    };
    const away = (event) => {
      if (node.current && !node.current.contains(event.target)) commit();
    };
    const key = (event) => {
      if (event.key === "Escape") onClose();
    };
    // pointerdown rather than click: a click that begins inside and ends
    // outside -- dragging across a swatch row -- should not count as leaving.
    document.addEventListener("pointerdown", away);
    document.addEventListener("keydown", key);
    return () => {
      document.removeEventListener("pointerdown", away);
      document.removeEventListener("keydown", key);
    };
  }, [project.name, onRename, onClose]);

  return (
    <div
      ref={node}
      className="popover prj-popover"
      role="dialog"
      aria-label={`Edit ${project.name}`}
    >
      <form
        className="prj-popover-row"
        onSubmit={(event) => {
          event.preventDefault();
          const clean = draft.trim();
          if (clean && clean !== project.name) onRename(clean);
          onClose();
        }}
      >
        <span className="mi">Name</span>
        <input
          type="text"
          value={draft}
          autoFocus
          aria-label={`Rename ${project.name}`}
          onChange={(event) => setDraft(event.target.value)}
        />
      </form>

      <div className="prj-popover-row">
        <span className="mi">Accent</span>
        <ThemePicker
          value={accent || null}
          onChange={onAccent}
          scope="project"
          seed={seed}
          inheritedLabel="Follow the app-wide accent"
        />
        <p className="caveat" style={{ margin: 0 }}>
          Worn by every conversation in here that has not chosen a colour of
          its own.
        </p>
      </div>
    </div>
  );
}

/* Projects, as a screen rather than a fold in the rail.
 *
 * The rail's version is for moving around while you work. This one is for
 * organising: it shows every project beside its conversations at once, which
 * is the view you want when deciding where something belongs — and the rail,
 * at 252px, can never show two folders at the same time.
 *
 * Dropping works the same in both places, so the gesture learned here still
 * works there. The Notion-style dashboard goes inside a project later; this is
 * the folder layer it will sit on.
 */
export function Projects({
  projects,
  sessions,
  onOpenSession,
  onNewProject,
  onRenameProject,
  onDeleteProject,
  onNewSessionIn,
  onFileSession,
  accentOf,
  seedOfRecord,
  onProjectAccent,
}) {
  const { confirm } = useDialog();
  const [name, setName] = useState("");
  // Which folder has its editor open. One at a time -- the popup overlays the
  // card, and two of them would be two dialogs fighting for the same corner.
  const [editing, setEditing] = useState(null);
  // Which folder the pointer is currently over, so exactly one lights up.
  const [over, setOver] = useState(null);

  const byProject = useMemo(() => {
    const map = new Map();
    for (const session of sessions) {
      const key = session.project_id || "";
      if (!map.has(key)) map.set(key, []);
      map.get(key).push(session);
    }
    return map;
  }, [sessions]);

  const unfiled = byProject.get("") || [];

  const create = (event) => {
    event.preventDefault();
    const clean = name.trim();
    if (!clean) return;
    onNewProject(clean);
    setName("");
  };

  // `key` is what lights up and `projectId` is what gets written -- they differ
  // for Unfiled, whose project id is null but which still needs a distinct
  // handle, since `null` is also "nothing is being hovered".
  const dropProps = (projectId, key) => ({
    onDragOver: (event) => {
      // Without preventDefault the browser refuses the drop entirely.
      if (event.dataTransfer.types.includes("text/session")) {
        event.preventDefault();
        setOver(key);
      }
    },
    onDragLeave: () => setOver((was) => (was === key ? null : was)),
    onDrop: (event) => {
      event.preventDefault();
      setOver(null);
      const id = event.dataTransfer.getData("text/session");
      if (id) onFileSession(id, projectId);
    },
  });

  const Row = ({ session }) => (
    <li
      key={session.id}
      draggable
      onDragStart={(event) => {
        // A custom type, not text/plain: dropping a conversation into a text
        // field elsewhere should do nothing rather than paste an id.
        event.dataTransfer.setData("text/session", session.id);
        event.dataTransfer.effectAllowed = "move";
      }}
    >
      <button className="prj-session" onClick={() => onOpenSession(session.id)}>
        <span className="prj-session-title">{session.title || "Untitled"}</span>
        <span className="mi">
          {session.message_count === 1 ? "1 message" : `${session.message_count ?? 0} messages`}
        </span>
      </button>
    </li>
  );

  return (
    <div className="page">
      <div className="page-head" data-tint="blue">
        <div className="sw" style={{ left: "-90px", top: "-110px", width: "280px", height: "280px", background: "var(--violet-field)" }} />
        <div className="inner">
          <div>
            <h1 className="h">Projects</h1>
            <p>
              Folders for conversations. Drag a conversation onto a project to
              file it, or drop it on Unfiled to take it out again.
            </p>
          </div>
          <form className="actions" onSubmit={create}>
            <input
              className="prj-new"
              type="text"
              value={name}
              placeholder="New project"
              aria-label="Name for a new project"
              onChange={(event) => setName(event.target.value)}
            />
            <button type="submit" className="btnp" disabled={!name.trim()}>
              Create
            </button>
          </form>
        </div>
      </div>

      <div className="page-body" style={{ flexDirection: "column" }}>
        <div className="page-col" style={{ alignSelf: "stretch" }}>
          {projects.length === 0 ? (
            <p className="p" style={{ color: "var(--text-dim)" }}>
              No projects yet. Make one above, then drag conversations into it.
            </p>
          ) : null}

          <div className="prj-grid">
            {projects.map((project) => {
              const mine = byProject.get(project.id) || [];
              return (
                <section
                  key={project.id}
                  className="prj-card"
                  data-over={over === project.id ? "" : undefined}
                  {...dropProps(project.id, project.id)}
                >
                  <div className="prj-head">
                    <span className="h">
                      <span
                        className="accent-bead"
                        aria-hidden="true"
                        style={{
                          background: swatchOf(
                            accentOf?.(project),
                            seedOfRecord?.(project),
                          ),
                        }}
                      />
                      {project.name}
                    </span>
                    <span className="mi">{mine.length}</span>
                  </div>

                  <ul className="prj-list">
                    {mine.length === 0 ? (
                      <li className="prj-empty mi">Drop a conversation here</li>
                    ) : (
                      mine.map((session) => <Row key={session.id} session={session} />)
                    )}
                  </ul>

                  {/* Over the card, not inside it: the page is for seeing
                      what is in a folder, and neither of a folder's settings
                      should push its conversations down the screen to be
                      changed. */}
                  {editing === project.id ? (
                    <ProjectEditor
                      project={project}
                      accent={accentOf?.(project)}
                      seed={seedOfRecord?.(project)}
                      onRename={(next) => onRenameProject(project.id, next)}
                      onAccent={(accent) => onProjectAccent?.(project.id, accent)}
                      onClose={() => setEditing(null)}
                    />
                  ) : null}

                  <div className="prj-actions">
                    <button
                      type="button"
                      className="mi"
                      onClick={() => onNewSessionIn(project.id)}
                    >
                      new chat
                    </button>
                    <button
                      type="button"
                      className="mi"
                      aria-expanded={editing === project.id}
                      aria-haspopup="dialog"
                      onClick={() =>
                        setEditing((was) => (was === project.id ? null : project.id))
                      }
                    >
                      edit
                    </button>
                    <button
                      type="button"
                      className="mi"
                      onClick={async () => {
                        const yes = await confirm(
                          `Delete the project "${project.name}"? Its conversations are kept and become unfiled.`,
                          { title: "Delete project", confirmLabel: "Delete", destructive: true },
                        );
                        if (yes) onDeleteProject(project.id);
                      }}
                    >
                      delete
                    </button>
                  </div>
                </section>
              );
            })}

            {/* Unfiled is a drop target too, so taking a conversation back out
                is the same gesture as putting it in. */}
            <section
              className="prj-card"
              data-unfiled=""
              data-over={over === "unfiled" ? "" : undefined}
              {...dropProps(null, "unfiled")}
            >
              <div className="prj-head">
                <span className="h">Unfiled</span>
                <span className="mi">{unfiled.length}</span>
              </div>
              <ul className="prj-list">
                {unfiled.length === 0 ? (
                  <li className="prj-empty mi">Everything is filed</li>
                ) : (
                  unfiled.map((session) => <Row key={session.id} session={session} />)
                )}
              </ul>
            </section>
          </div>
        </div>
      </div>
    </div>
  );
}
