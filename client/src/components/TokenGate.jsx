import { useState } from "react";

/** Nothing else renders until the bearer token is accepted. */
export function TokenGate({ error, connecting, onSubmit }) {
  const [value, setValue] = useState("");

  return (
    <div className="gate">
      <form
        className="gate-card"
        onSubmit={(event) => {
          event.preventDefault();
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
        <p>Paste the token printed by the server on startup.</p>
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
