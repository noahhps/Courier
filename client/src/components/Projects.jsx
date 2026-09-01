import { useMemo, useState } from "react";

import { useDialog } from "./Dialog";

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
}) {
  const { confirm } = useDialog();
  const [name, setName] = useState("");
  const [renaming, setRenaming] = useState(null);
  const [draftName, setDraftName] = useState("");
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
        <div className="sw" style={{ left: "-90px", top: "-110px", width: "280px", height: "280px", background: "#d5e0f7" }} />
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
                    {renaming === project.id ? (
                      <form
                        className="prj-rename"
                        onSubmit={(event) => {
                          event.preventDefault();
                          const clean = draftName.trim();
                          if (clean) onRenameProject(project.id, clean);
                          setRenaming(null);
                        }}
                      >
                        <input
                          type="text"
                          value={draftName}
                          autoFocus
                          aria-label={`Rename ${project.name}`}
                          onChange={(event) => setDraftName(event.target.value)}
                          onBlur={() => setRenaming(null)}
                        />
                      </form>
                    ) : (
                      <span className="h">{project.name}</span>
                    )}
                    <span className="mi">{mine.length}</span>
                  </div>

                  <ul className="prj-list">
                    {mine.length === 0 ? (
                      <li className="prj-empty mi">Drop a conversation here</li>
                    ) : (
                      mine.map((session) => <Row key={session.id} session={session} />)
                    )}
                  </ul>

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
                      onClick={() => {
                        setDraftName(project.name);
                        setRenaming(project.id);
                      }}
                    >
                      rename
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
