import { useState } from "react";

import {
  DEFAULT_ORIGIN,
  needsExplicitOrigin,
  serverOrigin,
  setServerOrigin,
} from "../lib/serverOrigin";

/** Nothing else renders until the bearer token is accepted. */
export function TokenGate({ error, connecting, onSubmit }) {
  const [value, setValue] = useState("");
  // Only the bundled desktop app has to ask -- see needsExplicitOrigin. Under
  // `tauri dev` the Vite proxy answers the question, so the gate stays the
  // one-field form it is in a browser.
  const desktop = needsExplicitOrigin();
  const [origin, setOrigin] = useState(() => serverOrigin() || DEFAULT_ORIGIN);

  return (
    <div className="gate">
      <form
        className="gate-card"
        onSubmit={(event) => {
          event.preventDefault();
          // Saved before the token is handed up, because the caller's very
          // next act is a request that has to go to the right host.
          if (desktop) setServerOrigin(origin);
          onSubmit(value.trim());
        }}
      >
        {/* The same soft fields the screen headers use, so the gate is dressed
            in the app rather than being a bare form in front of it. */}
        <div
          className="sw"
          style={{ right: "-70px", top: "-90px", width: "220px", height: "220px", background: "var(--violet-field)" }}
        />
        <div
          className="sw"
          style={{ right: "90px", top: "-50px", width: "110px", height: "110px", background: "var(--green-field)" }}
        />

        <h1>Assistant</h1>
        <p>
          {desktop
            ? "Where the server is, and the token it printed on startup."
            : "Paste the token printed by the server on startup."}
        </p>
        {desktop ? (
          <input
            type="text"
            inputMode="url"
            autoComplete="off"
            autoCapitalize="off"
            placeholder={DEFAULT_ORIGIN}
            spellCheck="false"
            aria-label="Server address"
            value={origin}
            onChange={(event) => setOrigin(event.target.value)}
          />
        ) : null}
        <input
          type="password"
          autoComplete="current-password"
          placeholder="token"
          spellCheck="false"
          value={value}
          onChange={(event) => setValue(event.target.value)}
        />
        <button type="submit" disabled={connecting}>
          {connecting ? "Connecting…" : "Connect"}
        </button>
        {error ? <p className="gate-error">{error}</p> : null}
      </form>
    </div>
  );
}
