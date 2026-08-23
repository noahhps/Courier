import { useState } from "react";

import {
  CORPORA,
  FACTS,
  FACT_FILTERS,
  FADING,
  MEMORY_SETTINGS,
} from "../lib/placeholder";

/* Artboard 1b: a soft pastel field for what it knows, an editable list beneath.
 *
 * Nothing on this screen is wired to the server. There is no `memory_facts`
 * table and no `/api/memory` route -- see docs/extending.md section 4 for the
 * shape that would fill it. The banner at the top says so on screen, because a
 * memory page that shows invented facts as though they were real is worse than
 * no memory page at all.
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

function Fact({ fact }) {
  return (
    <div className="sur fact">
      <div className="fact-top">
        <p>
          <Emphasised text={fact.text} />
        </p>
        <span className="mi">Edit</span>
        <span className="mi" data-act>
          Forget
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
        {fact.pinned ? <span className="mi">Keep always</span> : null}
        {fact.check ? (
          <span className="mi" data-act>
            Looks right?
          </span>
        ) : null}
      </div>
    </div>
  );
}

export function Memory() {
  const [filter, setFilter] = useState(FACT_FILTERS[0]);
  const [settings, setSettings] = useState(MEMORY_SETTINGS);

  const toggle = (id) =>
    setSettings((prev) =>
      prev.map((s) => (s.id === id ? { ...s, on: !s.on } : s)),
    );

  return (
    <div className="page">
      <div className="page-head" data-tint="violet">
        <div className="sw" style={{ right: "-70px", top: "-120px", width: "300px", height: "300px", background: "#d8d6f2" }} />
        <div className="sw" style={{ right: "110px", top: "-60px", width: "150px", height: "150px", background: "#dce9e2" }} />
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

      <div className="unbacked">
        <span>
          <b>Not wired up yet.</b> The server has no memory table or endpoint, so
          everything below is the design's illustration rather than your data.
        </span>
      </div>

      <div className="page-body unbacked-body">
        <div className="page-col">
          <div className="lane">
            <span className="mi" data-strong>
              About you · {FACTS.length}
            </span>
            <i />
            <span className="mi">Newest</span>
          </div>

          <div className="filters">
            {FACT_FILTERS.map((name) => (
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

          <div className="page-stack">
            {FACTS.map((fact) => (
              <Fact key={fact.id} fact={fact} />
            ))}

            {/* Something it picked up that it does not intend to keep. */}
            <div className="fact-fading">
              <span>
                {FADING.text} <em>— {FADING.note}</em>
              </span>
              <span className="mi" data-act>
                Keep it
              </span>
            </div>
          </div>

          <div className="callout" data-tint="green">
            <p>
              <b>Past conversations</b> · everything you have ever said here is
              searchable once retrieval lands
            </p>
            <button type="button" className="btn" disabled>
              Browse
            </button>
          </div>
        </div>

        <div className="page-side">
          <div className="lane">
            <span className="mi" data-strong>
              Documents
            </span>
            <i />
            <span className="mi" data-act>
              Add
            </span>
          </div>

          {CORPORA.map((corpus) => (
            <div key={corpus.id} className="sur corpus">
              <div className="corpus-top">
                <i style={{ background: corpus.tint }} />
                <span>{corpus.name}</span>
                <span className="mi">{corpus.count}</span>
              </div>
              <p>{corpus.note}</p>
            </div>
          ))}

          <div className="sur" style={{ padding: "18px" }}>
            <div className="mi" data-strong style={{ marginBottom: "15px" }}>
              Settings
            </div>
            <div className="toggles">
              {settings.map((setting) => (
                <div key={setting.id} className="toggle-row">
                  <span>{setting.label}</span>
                  <button
                    type="button"
                    className="switch"
                    role="switch"
                    aria-checked={setting.on}
                    aria-pressed={setting.on}
                    aria-label={setting.label}
                    onClick={() => toggle(setting.id)}
                  >
                    <i />
                  </button>
                </div>
              ))}
            </div>
          </div>

          <div className="side-actions">
            <button type="button" className="btn" disabled>
              Download all
            </button>
            <button
              type="button"
              className="btn"
              style={{ color: "var(--accent)", borderColor: "rgba(111,90,168,.4)" }}
              disabled
            >
              Forget all
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
