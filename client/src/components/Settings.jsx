/* The page behind the circle at the head of the rail.
 *
 * There is no account here to manage -- one user, one bearer token, one
 * machine -- so this is the honest version of "account": what you are
 * connected to, how it is answering, and how to stop being connected.
 *
 * Everything on this screen is real. It is the one screen added in this
 * redesign that needs no `unbacked` banner.
 */
import { ManageChats } from "./ManageChats";
import { useState } from "react";

import { ThemePicker } from "./ThemePicker";
import { DEFAULT_ACCENT } from "../lib/theme";

export function Settings({
  status,
  provider,
  onProvider,
  pinned,
  onTogglePin,
  onSignOut,
  api,
  sessions,
  onSessionsChanged,
  theme,
}) {

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
        <div className="sw" style={{ right: "-80px", top: "-120px", width: "290px", height: "290px", background: "var(--violet-field)" }} />
        <div className="sw" style={{ right: "130px", top: "-60px", width: "140px", height: "140px", background: "var(--blue-wash)" }} />
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
              This app
            </span>
            <i />
          </div>

          <div className="sur" style={{ padding: "18px" }}>
            <div className="toggles">
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

          {/* The accent, app-wide. This is the bottom of the stack of three:
              a conversation with no accent of its own falls through to its
              project, and a project with none falls through to here -- so
              this is the only one of the three that cannot decline to
              choose. */}
          <div className="lane">
            <span className="mi" data-strong>
              Accent
            </span>
            <i />
            <span className="mi">{theme?.source === "app" ? "in use" : "overridden here"}</span>
          </div>

          <div className="sur" style={{ padding: "18px" }}>
            <ThemePicker
              value={theme?.appAccent || DEFAULT_ACCENT}
              onChange={(accent) => theme?.setApp(accent || DEFAULT_ACCENT)}
              scope="app"
              seed={theme?.contextSeed}
            />
            <p className="caveat" style={{ margin: "14px 0 0" }}>
              <b>From the chat</b> reads what a conversation is about and
              colours the app to match — locally, from words already on this
              device, with nothing sent anywhere. A conversation or a project
              can override this; a chat that has not chosen wears whatever is
              set here.
            </p>
          </div>

          {/* Bulk management sits above sign-out: both are the destructive
              end of the page, and the one that actually destroys something
              should not be below the one that does not. */}
          <ManageChats
            api={api}
            sessions={sessions}
            onDeleted={onSessionsChanged}
          />

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
              style={{ color: "var(--accent)", borderColor: "var(--line-firm)" }}
              onClick={onSignOut}
            >
              Sign out
            </button>
          </div>
        </div>

        <div className="page-side">
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
      </div>
    </div>
  );
}
