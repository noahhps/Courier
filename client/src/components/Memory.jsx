import { useMemo, useState } from "react";

import { useMemory, when } from "../hooks/useMemory";

/* Artboard 1b: a soft pastel field for what it knows, an editable list beneath.
 *
 * Every value on this screen now comes from `GET /api/memory`. Two kinds of
 * memory are shown because there are two: curated facts, which ride in every
 * system prompt, and the searchable index over conversations and documents,
 * which the model reaches through the search_history skill.
 *
 * Editing is immediate in both directions -- a change here lands in the next
 * turn's prompt, and the page says so rather than implying a save step.
 */

/** The one piece of markup the facts need: **bold** inside a sentence. */
function Emphasised({ text }) {
  return (
    <>
      {text.split("**").map((part, index) =>
        index % 2 ? <b key={index}>{part}</b> : part,
      )}
    </>
  );
}

function Fact({ fact, busy, onEdit, onForget }) {
  const [draft, setDraft] = useState(null);

  const save = () => {
    const text = (draft || "").trim();
    if (text && text !== fact.text) onEdit(fact.id, { text });
    setDraft(null);
  };

  return (
    <div className="sur fact" data-busy={busy ? "" : undefined}>
      <div className="fact-top">
        {draft === null ? (
          <p>
            <Emphasised text={fact.text} />
          </p>
        ) : (
          <textarea
            className="fact-edit"
            value={draft}
            autoFocus
            rows={2}
            aria-label="Edit this fact"
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                save();
              }
              if (event.key === "Escape") setDraft(null);
            }}
          />
        )}
        {draft === null ? (
          <span className="mi" role="button" tabIndex={0}
                onClick={() => setDraft(fact.text)}
                onKeyDown={(e) => e.key === "Enter" && setDraft(fact.text)}>
            Edit
          </span>
        ) : (
          <span className="mi" role="button" tabIndex={0} onClick={save}
                onKeyDown={(e) => e.key === "Enter" && save()}>
            Save
          </span>
        )}
        <span className="mi" data-act role="button" tabIndex={0}
              onClick={() => (draft === null ? onForget(fact.id) : setDraft(null))}
              onKeyDown={(e) => e.key === "Enter" && onForget(fact.id)}>
          {draft === null ? "Forget" : "Cancel"}
        </span>
      </div>
      <div className="fact-meta">
        {/* Told to it, or worked out. An inferred fact shows how sure it is. */}
        <span className="origin" data-from={fact.from}>
          {fact.from === "inferred" ? (
            <span style={{ width: `${Math.round(fact.confidence * 100)}%` }} />
          ) : null}
        </span>
        <span className="mi" data-strong>
          {fact.from === "told" ? "You told me" : "I worked it out"}
        </span>
        <span className="sep" />
        <span className="mi">
          {fact.when}
          {fact.used ? ` · ${fact.used}` : ""}
        </span>
        <span className="spacer" />
        <span className="mi" role="button" tabIndex={0}
              data-strong={fact.pinned ? "" : undefined}
              onClick={() => onEdit(fact.id, { pinned: !fact.pinned })}
              onKeyDown={(e) => e.key === "Enter" && onEdit(fact.id, { pinned: !fact.pinned })}>
          {fact.pinned ? "Keep always" : "Keep?"}
        </span>
        {/* A guess held back, or one it was unsure of. Confirming promotes it
            into the prompt; Forget is the other answer and is already above. */}
        {fact.check ? (
          <span className="mi" data-act role="button" tabIndex={0}
                onClick={() => onEdit(fact.id, { status: "active" })}
                onKeyDown={(e) => e.key === "Enter" && onEdit(fact.id, { status: "active" })}>
            {fact.status === "pending" ? "Looks right?" : "Confirm"}
          </span>
        ) : null}
      </div>
    </div>
  );
}

export function Memory({ api }) {
  const memory = useMemory(api);
  const [filter, setFilter] = useState("All");
  const [adding, setAdding] = useState(null);
  const [confirmWipe, setConfirmWipe] = useState(false);

  const { facts, settings, corpora, index } = memory;

  // Filters are data now, not a constant: the categories are whatever has
  // actually been remembered, with the two computed views on either end.
  const filters = useMemo(() => {
    const categories = [...new Set(facts.map((f) => f.category).filter(Boolean))].sort();
    const tail = facts.some((f) => f.fading) ? ["Fading"] : [];
    return ["All", ...categories, ...tail];
  }, [facts]);

  const shown = facts.filter((fact) => {
    if (filter === "All") return !fact.fading;
    if (filter === "Fading") return fact.fading;
    return fact.category === filter && !fact.fading;
  });
  const fading = facts.filter((fact) => fact.fading);

  const download = async () => {
    const blob = await api.exportMemory();
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = "memory.json";
    link.click();
    URL.revokeObjectURL(url);
  };

  const submitNew = () => {
    const text = (adding || "").trim();
    if (text) memory.addFact(text);
    setAdding(null);
  };

  return (
    <div className="page">
      <div className="page-head" data-tint="violet">
        <div className="sw" style={{ right: "-70px", top: "-120px", width: "300px", height: "300px", background: "var(--violet-field)" }} />
        <div className="sw" style={{ right: "110px", top: "-60px", width: "150px", height: "150px", background: "var(--blue-wash)" }} />
        <div className="inner">
          <div>
            <h1 className="h">What I remember</h1>
            <p>
              Two kinds. Things I've picked up about you, and documents you've
              given me to look things up in. Change or delete anything — I stop
              using it straight away.
            </p>
          </div>
        </div>
      </div>

      {memory.error ? (
        <div className="unbacked">
          <span>{memory.error}</span>
        </div>
      ) : null}

      <div className="page-body">
        <div className="page-col">
          <div className="lane">
            <span className="mi" data-strong>
              About you · {memory.loading ? "…" : facts.length}
            </span>
            <i />
            <span className="mi" data-act role="button" tabIndex={0}
                  onClick={() => setAdding("")}
                  onKeyDown={(e) => e.key === "Enter" && setAdding("")}>
              Add
            </span>
          </div>

          {adding !== null ? (
            <div className="sur fact">
              <div className="fact-top">
                <textarea
                  className="fact-edit"
                  value={adding}
                  autoFocus
                  rows={2}
                  placeholder="One sentence — something still worth knowing in a month."
                  aria-label="A new fact to remember"
                  onChange={(event) => setAdding(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" && !event.shiftKey) {
                      event.preventDefault();
                      submitNew();
                    }
                    if (event.key === "Escape") setAdding(null);
                  }}
                />
                <span className="mi" role="button" tabIndex={0} onClick={submitNew}
                      onKeyDown={(e) => e.key === "Enter" && submitNew()}>
                  Save
                </span>
                <span className="mi" data-act role="button" tabIndex={0}
                      onClick={() => setAdding(null)}
                      onKeyDown={(e) => e.key === "Enter" && setAdding(null)}>
                  Cancel
                </span>
              </div>
            </div>
          ) : null}

          {filters.length > 1 ? (
            <div className="filters">
              {filters.map((name) => (
                <button
                  key={name}
                  type="button"
                  className="chip"
                  data-on={filter === name ? "" : undefined}
                  onClick={() => setFilter(name)}
                >
                  {name}
                </button>
              ))}
            </div>
          ) : null}

          <div className="page-stack">
            {memory.loading ? (
              <p className="mi">Checking what's remembered…</p>
            ) : null}

            {!memory.loading && facts.length === 0 ? (
              <p className="mi">
                Nothing remembered yet. Tell me something worth keeping — “remember
                that I rent a flat in Leeds” — or add it here.
              </p>
            ) : null}

            {shown.map((fact) => (
              <Fact
                key={fact.id}
                fact={fact}
                busy={memory.busy.includes(fact.id)}
                onEdit={memory.editFact}
                onForget={memory.forgetFact}
              />
            ))}

            {/* Picked up but not intended to be kept: unpinned, rarely used,
                and not looked at in a month. Shown so it can be rescued. */}
            {filter === "All" && fading.length
              ? fading.map((fact) => (
                  <div key={fact.id} className="fact-fading">
                    <span>
                      {fact.text} <em>— I'll let this fade unless it's used</em>
                    </span>
                    <span className="mi" data-act role="button" tabIndex={0}
                          onClick={() => memory.editFact(fact.id, { pinned: true })}
                          onKeyDown={(e) =>
                            e.key === "Enter" && memory.editFact(fact.id, { pinned: true })
                          }>
                      Keep it
                    </span>
                  </div>
                ))
              : null}
          </div>

          <div className="callout" data-tint="green">
            <p>
              <b>Past conversations</b> ·{" "}
              {index && index.total
                ? `${index.total} passage${index.total === 1 ? "" : "s"} searchable` +
                  (index.embedded < index.total
                    ? `, ${index.total - index.embedded} still to index`
                    : "")
                : "nothing indexed yet"}
            </p>
            <button type="button" className="btn" onClick={memory.reindex}>
              {index && index.embedded < index.total ? "Finish indexing" : "Re-index"}
            </button>
          </div>
        </div>

        <div className="page-side">
          <div className="lane">
            <span className="mi" data-strong>
              Documents
            </span>
            <i />
            <span className="mi">{corpora.reduce((n, c) => n + c.count, 0)}</span>
          </div>

          {corpora.length === 0 ? (
            <p className="mi">
              Nothing attached yet. Files you send in a conversation are indexed
              and searchable from then on.
            </p>
          ) : null}

          {corpora.map((corpus) => (
            <div key={corpus.kind} className="sur corpus">
              <div className="corpus-top">
                <i style={{ background: TINTS[corpus.kind] || "#c9d3cc" }} />
                <span>{LABELS[corpus.kind] || corpus.kind}</span>
                <span className="mi">{corpus.count}</span>
              </div>
              <p>
                {corpus.names.join(", ")}
                {corpus.count > corpus.names.length
                  ? `, and ${corpus.count - corpus.names.length} more`
                  : ""}
                {corpus.newest ? ` · newest ${when(corpus.newest)}` : ""}
              </p>
            </div>
          ))}

          <div className="sur" style={{ padding: "18px" }}>
            <div className="mi" data-strong style={{ marginBottom: "15px" }}>
              Settings
            </div>
            <div className="toggles">
              {SWITCHES.map((setting) => (
                <div key={setting.id} className="toggle-row">
                  <span title={setting.note}>{setting.label}</span>
                  <button
                    type="button"
                    className="switch"
                    role="switch"
                    aria-checked={Boolean(settings[setting.id])}
                    aria-pressed={Boolean(settings[setting.id])}
                    aria-label={setting.label}
                    disabled={memory.loading}
                    onClick={() => memory.setSetting(setting.id, !settings[setting.id])}
                  >
                    <i />
                  </button>
                </div>
              ))}
            </div>
          </div>

          <div className="side-actions">
            <button type="button" className="btn" onClick={download}>
              Download all
            </button>
            <button
              type="button"
              className="btn"
              style={{ color: "var(--accent)", borderColor: "rgba(111,90,168,.4)" }}
              disabled={facts.length === 0}
              onClick={() => {
                if (!confirmWipe) {
                  setConfirmWipe(true);
                  return;
                }
                memory.forgetAll();
                setConfirmWipe(false);
              }}
              onBlur={() => setConfirmWipe(false)}
            >
              {confirmWipe ? "Really forget all?" : "Forget all"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

// Attachment kinds as the page names them. The server sends `image`, `text`
// and `document` because that is what decides how a file is handled; these are
// the words a person would use for the same three things.
const LABELS = { image: "Pictures", text: "Text and code", document: "Documents" };
const TINTS = { image: "#8f8ede", text: "#c9d3cc", document: "#a8c3b4" };

const SWITCHES = [
  {
    id: "between_chats",
    label: "Remember between chats",
    note: "Off: nothing is stored or recalled across conversations.",
  },
  {
    id: "confirm",
    label: "Ask before saving anything",
    note: "On: anything I work out waits here for you to confirm.",
  },
  {
    id: "share",
    label: "Share across projects",
    note: "Stored, but inert — there are no projects yet.",
  },
];
