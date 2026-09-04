import { useState } from "react";

/* Where the backends are managed: what each is pointed at, and how to connect
 * the one that needs connecting.
 *
 * The rail's menu is the fast path -- open it, pick a model, close it. This is
 * the slow one, and it is where the things that cannot be a menu item live: a
 * key to paste, a sign-in to start, an account to look at, a connection to
 * take back. Both change the same server state, so a model picked here shows
 * in the menu and the other way round.
 */

const LABELS = {
  local: { name: "Local", blurb: "Ollama, on this machine" },
  cloud: { name: "Cloud", blurb: "Anthropic, when a key is in the environment" },
  openrouter: { name: "OpenRouter", blurb: "One key, several hundred models" },
};

export function Providers({ models, provider, onProvider, serving }) {
  return (
    <>
      <div className="lane">
        <span className="mi" data-strong>
          Connection
        </span>
        <i />
        <span className="mi">
          {serving === "none" ? "nothing reachable" : `serving ${serving || "…"}`}
        </span>
      </div>

      {models.error ? (
        <div className="callout" data-tint="ochre">
          <p>
            {models.error.answered
              ? models.error.message
              : "Couldn't reach the server to list the models."}
          </p>
        </div>
      ) : null}

      {/* `loading` starts true so the page says "checking" rather than
          flashing an empty column and then contradicting it. */}
      {models.loading && models.providers.length === 0 ? (
        <p className="caveat" style={{ margin: 0 }}>
          Checking what is reachable…
        </p>
      ) : null}

      {models.providers.map((entry) => (
        <div key={entry.id} className="sur corpus provider-card">
          <div className="corpus-top">
            <i
              style={{
                background: entry.healthy ? "var(--green)" : "rgba(20,23,29,.18)",
              }}
            />
            <span>{LABELS[entry.id]?.name || entry.name}</span>
            <span className="mi">
              {/* Three states, not two: a backend with no key at all is not
                  the same as one whose key stopped working, and the label is
                  short because it is set in the small caps of a card head. */}
              {entry.healthy ? "reachable" : entry.configured ? "unreachable" : "no key"}
            </span>
          </div>

          <ModelRow entry={entry} onChoose={models.choose} />

          {entry.id === "openrouter" ? (
            <OpenRouterConnection models={models} entry={entry} />
          ) : (
            <p>{entry.error || LABELS[entry.id]?.blurb}</p>
          )}
        </div>
      ))}

      <div className="lane" style={{ marginTop: "6px" }}>
        <span className="mi" data-strong>
          Answer with
        </span>
        <i />
      </div>
      <div className="filters">
        {[{ id: null, label: "Auto" }, ...models.providers.map((p) => ({
          id: p.id,
          label: LABELS[p.id]?.name || p.name,
        }))].map((choice) => (
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
        Auto uses the local model and falls back to a cloud backend only when it
        cannot be reached. Whichever one answers, it answers with the model
        chosen above — and that choice is kept on the server, so it is the same
        on every device.
      </p>
    </>
  );
}

/**
 * The model in use, and everything else this backend has.
 *
 * A native select rather than the rail's filtered list: this page has room,
 * the lists are short enough to scroll on every backend but OpenRouter, and a
 * select is the one control a phone already knows how to make usable.
 */
function ModelRow({ entry, onChoose }) {
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState("");
  const models = entry.models || [];

  // A model set from the environment, or picked before it was retired, can be
  // one the catalogue no longer lists. It is still what this backend will use,
  // so it is added to the options rather than silently replaced by the first
  // one in the list -- which is what a select with no matching value does.
  const options = models.some((m) => m.id === entry.model)
    ? models
    : [{ id: entry.model, name: entry.model }, ...models];

  const change = async (model) => {
    if (!model || model === entry.model) return;
    setBusy(true);
    setProblem("");
    try {
      await onChoose(entry.id, model);
    } catch (failure) {
      setProblem(failure.message || String(failure));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="provider-model">
      <label className="mi" htmlFor={`model-${entry.id}`}>
        Model
      </label>
      {models.length === 0 && !entry.model ? (
        <span className="mi">—</span>
      ) : (
        <select
          id={`model-${entry.id}`}
          value={entry.model || ""}
          disabled={busy || models.length === 0}
          onChange={(event) => change(event.target.value)}
        >
          {options.map((model) => (
            <option key={model.id} value={model.id}>
              {model.name || model.id}
            </option>
          ))}
        </select>
      )}
      {problem ? <span className="mi provider-problem">{problem}</span> : null}
    </div>
  );
}

/**
 * Connecting OpenRouter: sign in, or paste a key.
 *
 * Both end at the same place -- a key held on the server, in `data/` -- so
 * this offers whichever one the reader would rather do rather than picking for
 * them. The sign-in is first because it is one tap and the other is four steps
 * in a web console.
 */
function OpenRouterConnection({ models, entry }) {
  const [pasting, setPasting] = useState(false);
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState("");
  const signIn = models.signIn;

  const save = async (event) => {
    event.preventDefault();
    setBusy(true);
    setProblem("");
    try {
      await models.setKey(key.trim());
      setKey("");
      setPasting(false);
    } catch (failure) {
      setProblem(failure.message || String(failure));
    } finally {
      setBusy(false);
    }
  };

  const disconnect = async () => {
    setBusy(true);
    setProblem("");
    try {
      await models.setKey("");
    } catch (failure) {
      setProblem(failure.message || String(failure));
    } finally {
      setBusy(false);
    }
  };

  if (entry.configured) {
    return (
      <>
        <p>
          {entry.account?.label ? `Connected as ${entry.account.label}. ` : "Connected. "}
          {typeof entry.account?.usage === "number"
            ? `$${entry.account.usage.toFixed(2)} spent on this key` +
              (entry.account.limit ? ` of $${Number(entry.account.limit).toFixed(2)}.` : ".")
            : "Usage and limits live on openrouter.ai."}
        </p>
        <div className="skill-needs">
          <span className="mi">
            {entry.healthy ? "Key accepted" : "Key stored, but not accepted"}
          </span>
          <button type="button" className="mi" disabled={busy} onClick={disconnect}>
            disconnect
          </button>
        </div>
        {problem ? <p className="provider-problem">{problem}</p> : null}
      </>
    );
  }

  return (
    <>
      <p>
        Sign in and OpenRouter mints a key for this app. Nothing else is stored:
        the key lands in <code>data/openrouter_key</code> on the server, and
        disconnecting deletes it.
      </p>

      {signIn?.status === "pending" ? (
        <p>
          Waiting for the sign-in to finish in the other tab…{" "}
          {/* The link matters more than it looks: a browser that blocked the
              popup leaves this as the only way through the flow. */}
          {signIn.url ? (
            <a href={signIn.url} target="_blank" rel="noreferrer">
              open it again
            </a>
          ) : null}
          {" · "}
          <button type="button" className="mi" onClick={models.dismissSignIn}>
            stop waiting
          </button>
        </p>
      ) : null}
      {signIn?.status === "failed" ? (
        <p className="provider-problem">{signIn.error || "The sign-in didn't finish."}</p>
      ) : null}

      <div className="side-actions" style={{ marginTop: "10px" }}>
        <button
          type="button"
          className="btn"
          disabled={signIn?.status === "pending" || signIn?.status === "starting"}
          onClick={models.startSignIn}
        >
          {signIn?.status === "pending" ? "Waiting…" : "Sign in with OpenRouter"}
        </button>
        <button type="button" className="btn" onClick={() => setPasting((was) => !was)}>
          {pasting ? "Cancel" : "Paste a key"}
        </button>
      </div>

      {pasting ? (
        <form className="skill-key" onSubmit={save}>
          <input
            type="password"
            value={key}
            autoFocus
            autoComplete="off"
            spellCheck="false"
            placeholder="sk-or-v1-…"
            aria-label="OpenRouter API key"
            onChange={(event) => setKey(event.target.value)}
          />
          <button type="submit" className="btnp" disabled={!key.trim() || busy}>
            {busy ? "Checking…" : "Save"}
          </button>
        </form>
      ) : null}

      {problem ? <p className="provider-problem">{problem}</p> : null}
    </>
  );
}
