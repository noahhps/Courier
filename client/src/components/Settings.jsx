/* The page behind the circle at the head of the rail.
 *
 * There is no account here to manage -- one user, one bearer token, one
 * machine -- so this is the honest version of "account": what you are
 * connected to, how it is answering, and how to stop being connected.
 *
 * Everything on this screen is real. It is the one screen added in this
 * redesign that needs no `unbacked` banner.
 */
import { useState } from "react";

import { autoSaveEnabled, setAutoSave } from "./SkillTrace";

export function Settings({ status, provider, onProvider, pinned, onTogglePin, onSignOut }) {
  const [autoSave, setAutoSaveState] = useState(autoSaveEnabled);

  const rows = [
    {
      id: "local",
      name: "Local",
      detail: status?.local?.model || "—",
      note: status?.local?.url || "",
      ok: status?.local?.healthy,
    },
    {
      id: "cloud",
      name: "Cloud fallback",
      detail: status?.cloud?.model || "—",
      note: status?.cloud?.healthy ? "reachable" : "no API key, or unreachable",
      ok: status?.cloud?.healthy,
    },
  ];

  return (
    <div className="page">
      <div className="page-head" data-tint="blue">
        <div className="sw" style={{ right: "-80px", top: "-120px", width: "290px", height: "290px", background: "#d3e0f6" }} />
        <div className="sw" style={{ right: "130px", top: "-60px", width: "140px", height: "140px", background: "#dcd9f5" }} />
        <div className="inner">
          <div>
            <h1 className="h">This device</h1>
            <p>
              One token, one machine. Nothing is stored here except the token
              and how you like the sidebar — everything else lives on the
              server.
            </p>
          </div>
        </div>
      </div>

      <div className="page-body">
        <div className="page-col">
          <div className="lane">
            <span className="mi" data-strong>
              Connection
            </span>
            <i />
            <span className="mi">
              {status?.serving === "none" ? "nothing reachable" : `serving ${status?.serving}`}
            </span>
          </div>

          {rows.map((row) => (
            <div key={row.id} className="sur corpus">
              <div className="corpus-top">
                <i style={{ background: row.ok ? "var(--green)" : "rgba(20,23,29,.18)" }} />
                <span>{row.name}</span>
                <span className="mi">{row.detail}</span>
              </div>
              {row.note ? <p>{row.note}</p> : null}
            </div>
          ))}

          <div className="lane" style={{ marginTop: "6px" }}>
            <span className="mi" data-strong>
              Answer with
            </span>
            <i />
          </div>
          <div className="filters">
            {[
              { id: null, label: "Auto" },
              { id: "local", label: "Local" },
              { id: "cloud", label: "Cloud" },
            ].map((choice) => (
              <button
                key={choice.id || "auto"}
                type="button"
                className="chip"
                data-on={provider === choice.id ? "" : undefined}
                onClick={() => onProvider(choice.id)}
              >
                {choice.label}
              </button>
            ))}
          </div>
          <p className="caveat" style={{ margin: 0 }}>
            Auto uses the local model and falls back to the cloud only when it
            cannot be reached. Choosing between the models Ollama has pulled
            needs a server endpoint that does not exist yet.
          </p>
        </div>

        <div className="page-side">
          <div className="lane">
            <span className="mi" data-strong>
              This app
            </span>
            <i />
          </div>

          <div className="sur" style={{ padding: "18px" }}>
            <div className="toggles">
              <div className="toggle-row">
                <span>
                  Save documents automatically
                  <br />
                  <span className="caveat">
                    Straight to this browser's download folder when the
                    assistant writes one.
                  </span>
                </span>
                <button
                  type="button"
                  className="switch"
                  role="switch"
                  aria-checked={autoSave}
                  aria-pressed={autoSave}
                  aria-label="Save documents automatically"
                  onClick={() => {
                    setAutoSave(!autoSave);
                    setAutoSaveState(!autoSave);
                  }}
                >
                  <i />
                </button>
              </div>
              <div className="toggle-row">
                <span>Keep the sidebar open</span>
                <button
                  type="button"
                  className="switch"
                  role="switch"
                  aria-checked={pinned}
                  aria-pressed={pinned}
                  aria-label="Keep the sidebar open"
                  onClick={onTogglePin}
                >
                  <i />
                </button>
              </div>
            </div>
          </div>

          <div className="callout" data-tint="ochre">
            <p>
              <b>Signing out</b> forgets the token on this device only. Your
              conversations stay on the server.
            </p>
          </div>

          <div className="side-actions">
            <button
              type="button"
              className="btn"
              style={{ color: "var(--accent)", borderColor: "rgba(74,65,176,.4)" }}
              onClick={onSignOut}
            >
              Sign out
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
