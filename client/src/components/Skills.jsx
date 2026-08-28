import { useMemo, useState } from "react";

import { useSkills } from "../hooks/useSkills";

/* The shelf.
 *
 * One list of what the server actually has, each row switchable, with a filter
 * across the top for when the list outgrows the screen. Everything here is
 * `GET /api/skills` and `PATCH /api/skills/{name}` -- there is no second tier
 * of examples any more.
 */

function Row({ skill, busy, onToggle, onKey }) {
  const [key, setKey] = useState("");
  const [open, setOpen] = useState(false);
  const blocked = skill.requires && !skill.available;

  const save = async (event) => {
    event.preventDefault();
    if (busy) return;
    if (await onKey(skill.name, key.trim())) {
      setKey("");
      setOpen(false);
    }
  };

  return (
    <div className="skill-item" data-off={skill.enabled ? undefined : ""}>
      <div className="skill-item-text">
        <span className="h">{skill.name}</span>
        <p className="skill-body">{skill.description}</p>

        {/* Listed either way, and told plainly why it cannot be switched on.
            A capability you cannot see is one you never go looking for. */}
        {blocked ? (
          <div className="skill-needs">
            <span className="mi">Needs {skill.requires}</span>
            <button type="button" className="mi" onClick={() => setOpen((was) => !was)}>
              {open ? "cancel" : "add key"}
            </button>
          </div>
        ) : null}

        {skill.configurable && skill.available ? (
          <div className="skill-needs">
            <span className="mi">Key saved</span>
            <button
              type="button"
              className="mi"
              disabled={busy}
              onClick={() => onKey(skill.name, "")}
            >
              remove
            </button>
          </div>
        ) : null}

        {open && blocked ? (
          <form className="skill-key" onSubmit={save}>
            <input
              type="password"
              value={key}
              autoFocus
              autoComplete="off"
              spellCheck="false"
              placeholder="Paste the key"
              aria-label={`${skill.requires} for ${skill.name}`}
              onChange={(event) => setKey(event.target.value)}
            />
            <button type="submit" className="btnp" disabled={!key.trim() || busy}>
              {busy ? "Checking…" : "Save"}
            </button>
          </form>
        ) : null}
      </div>
      <button
        type="button"
        className="switch"
        role="switch"
        aria-checked={skill.enabled}
        aria-label={`${skill.enabled ? "Disable" : "Enable"} ${skill.name}`}
        aria-pressed={skill.enabled}
        // Locked until it could actually run. The server refuses this too --
        // the disabled attribute is the explanation, not the enforcement.
        disabled={busy || blocked}
        title={blocked ? `Needs ${skill.requires} first` : undefined}
        onClick={() => onToggle(skill.name, !skill.enabled)}
      >
        <i />
      </button>
    </div>
  );
}

export function Skills({ api }) {
  const { skills, loading, error, refresh, setEnabled, setKey, pending } =
    useSkills(api);
  const [query, setQuery] = useState("");

  // Name and description both, so "time" finds a clock skill that never says
  // the word in its name.
  const shown = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return skills;
    return skills.filter(
      (s) =>
        s.name.toLowerCase().includes(needle) ||
        (s.description || "").toLowerCase().includes(needle),
    );
  }, [skills, query]);

  const on = skills.filter((s) => s.enabled).length;

  return (
    <div className="page">
      <div className="page-head" data-tint="green">
        <div className="sw" style={{ left: "-90px", top: "-110px", width: "280px", height: "280px", background: "#d5e8db" }} />
        <div className="sw" style={{ left: "120px", top: "-70px", width: "130px", height: "130px", background: "#f6efd4" }} />
        <div className="inner">
          <div>
            <h1 className="h">Skills</h1>
            <p>
              Things I can do. Switch one off and I stop being offered it, until
              you switch it back.
            </p>
          </div>
          <div className="actions">
            <button type="button" className="btn" onClick={refresh} disabled={loading}>
              {loading ? "Checking…" : "Check again"}
            </button>
          </div>
        </div>
      </div>

      <div className="page-body" style={{ flexDirection: "column" }}>
        <div className="page-col" style={{ alignSelf: "stretch" }}>
          <div className="lane">
            <span className="mi" data-strong>
              {loading ? "Checking" : `${on} on · ${skills.length} registered`}
            </span>
            <i />
          </div>

          {/* Hidden when there is nothing to filter: a search box over an
              empty list is furniture, and over two rows it is noise. */}
          {skills.length > 3 ? (
            <div className="search">
              <input
                type="search"
                value={query}
                placeholder="Filter skills"
                aria-label="Filter skills"
                onChange={(event) => setQuery(event.target.value)}
              />
            </div>
          ) : null}

          {/* Above the list, not instead of it. A failed toggle is one row's
              problem; replacing the whole region with an error would take away
              the other nine skills and the switch you were trying to use. */}
          {error ? (
            <div className="callout" data-tint="ochre">
              <div style={{ flex: 1 }}>
                <span className="h" style={{ fontSize: "var(--t-lg)" }}>
                  {error.answered ? "That did not work" : "Could not reach the server"}
                </span>
                <p style={{ margin: "7px 0 0", color: "var(--text-dim)", fontSize: "var(--t-sm)", lineHeight: 1.7 }}>
                  {error.message}
                </p>
              </div>
              <button type="button" className="btn" onClick={refresh}>
                Try again
              </button>
            </div>
          ) : null}

          {error && skills.length === 0 ? null : loading && skills.length === 0 ? (
            <p className="p" style={{ color: "var(--text-faint)" }}>
              Asking the server what it can do…
            </p>
          ) : skills.length === 0 ? (
            /* Empty is the honest answer, not a failure: the registry is built
               at boot and nothing calls `register()` yet. Say which, so this
               does not read as a broken request. */
            <p className="p" style={{ color: "var(--text-dim)" }}>
              The server answered, and has no skills registered. They are
              registered in Python at start-up — nothing calls{" "}
              <code>Registry.register()</code> yet, so this list stays empty
              until something does.
            </p>
          ) : shown.length === 0 ? (
            <p className="p" style={{ color: "var(--text-dim)" }}>
              Nothing matches “{query}”.
            </p>
          ) : (
            <div className="skill-list">
              {shown.map((skill) => (
                <Row
                  key={skill.name}
                  skill={skill}
                  busy={pending.includes(skill.name)}
                  onToggle={setEnabled}
                  onKey={setKey}
                />
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
