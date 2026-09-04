import { useEffect, useMemo, useRef, useState } from "react";

/* The menu behind the circle at the foot of the rail.
 *
 * Two levels, because there are two decisions and they are not the same one.
 * The first is *who answers*: the local runner, one of the two cloud backends,
 * or nobody in particular, which is what Auto means. The second is *what it
 * answers with* -- which of the models that backend has. The first is a
 * per-turn field on /api/chat and lives in this browser; the second is server
 * state, because the phone and the laptop are talking to the same assistant.
 *
 * Opening a backend's list does not select that backend. Choosing a model in
 * it does -- you opened this list and picked something in it, so it should be
 * what answers -- with one exception: Auto stays Auto. Someone on Auto who
 * changes which local model is used has not asked to stop falling back.
 *
 * Anchored upward from the foot of the rail, because that is where its circle
 * is and a menu that opens away from its trigger is a menu you lose.
 */

const AUTO = {
  id: null,
  name: "Auto",
  hint: "Local first, then whichever cloud is connected",
};

const LABELS = {
  local: { name: "Local", blurb: "On this machine" },
  cloud: { name: "Cloud", blurb: "Anthropic" },
  openrouter: { name: "OpenRouter", blurb: "Any model, one key" },
};

// Long lists are the normal case on OpenRouter -- several hundred -- so the
// list is cut rather than scrolled forever, and the filter is how you reach
// the rest. Ollama and Anthropic never come close to this.
const SHOWN = 60;

export function ModelMenu({
  open,
  status,
  providers = [],
  value,
  onChange,
  onChooseModel,
  onClose,
  onManage,
}) {
  const node = useRef(null);
  // Which backend's models are on screen, or null for the provider list.
  const [browsing, setBrowsing] = useState(null);
  const [filter, setFilter] = useState("");

  // Close on a click anywhere else, and on Escape. Both listeners only exist
  // while the menu is up, so a closed menu costs nothing.
  useEffect(() => {
    if (!open) return undefined;
    const away = (event) => {
      if (node.current && !node.current.contains(event.target)) onClose();
    };
    const key = (event) => {
      if (event.key !== "Escape") return;
      // Escape backs out of a model list first and closes the menu second,
      // which is the order the two levels were entered in.
      setBrowsing((current) => {
        if (current) return null;
        onClose();
        return current;
      });
    };
    // Capture: a click on the trigger itself must reach the trigger's own
    // handler, so this runs before React's delegated listener sees it and
    // toggles it straight back open.
    document.addEventListener("mousedown", away);
    document.addEventListener("keydown", key);
    return () => {
      document.removeEventListener("mousedown", away);
      document.removeEventListener("keydown", key);
    };
  }, [open, onClose]);

  // A closed menu forgets where it was. Reopening at the third page of the
  // OpenRouter catalogue is not where anybody left off.
  useEffect(() => {
    if (!open) {
      setBrowsing(null);
      setFilter("");
    }
  }, [open]);

  // Everything that matches, and the first `SHOWN` of it. Both counts are
  // needed: one to draw the list, the other to say honestly how much of it is
  // not being drawn.
  const matches = useMemo(() => {
    const found = providers.find((p) => p.id === browsing);
    if (!found) return [];
    const needle = filter.trim().toLowerCase();
    if (!needle) return found.models || [];
    return (found.models || []).filter(
      (m) =>
        m.id.toLowerCase().includes(needle) ||
        (m.name || "").toLowerCase().includes(needle),
    );
  }, [browsing, filter, providers]);
  const shown = matches.slice(0, SHOWN);

  if (!open) return null;

  const backend = (id) => providers.find((p) => p.id === id) || null;

  const detail = (id) => {
    if (id === null) {
      return status?.serving && status.serving !== "none"
        ? `using ${status.serving}`
        : "nothing reachable";
    }
    const found = backend(id);
    if (!found) return "";
    if (id === "openrouter" && !found.configured) return "not connected";
    if (!found.healthy) return found.model ? `${found.model} · unreachable` : "unreachable";
    return found.model || LABELS[id]?.blurb || "";
  };

  const healthy = (id) => {
    if (id === null) return status?.serving && status.serving !== "none";
    return backend(id)?.healthy;
  };

  const browse = (id) => {
    setFilter("");
    setBrowsing(id);
  };

  const pick = (id, model) => {
    onChooseModel(id, model);
    // Auto keeps routing; an explicit choice of another backend does not
    // survive picking a model somewhere else. See the note at the top.
    if (value !== null && value !== id) onChange(id);
    onClose();
  };

  if (browsing) {
    const found = backend(browsing);
    const total = found?.models?.length || 0;
    return (
      <div className="popover popover-up popover-models" ref={node} role="menu">
        <div className="popover-head popover-back">
          <button type="button" className="mi" onClick={() => setBrowsing(null)}>
            ‹ back
          </button>
          <span className="mi" data-strong>
            {LABELS[browsing]?.name || browsing}
          </span>
        </div>

        {total > 12 ? (
          <div className="popover-filter">
            <input
              type="search"
              value={filter}
              autoFocus
              placeholder="Filter models"
              aria-label={`Filter ${LABELS[browsing]?.name || browsing} models`}
              onChange={(event) => setFilter(event.target.value)}
            />
          </div>
        ) : null}

        <div className="popover-scroll">
          {found?.error ? (
            <p className="popover-empty">{found.error}</p>
          ) : total === 0 ? (
            <p className="popover-empty">
              {browsing === "local"
                ? "Nothing pulled yet — `ollama pull gpt-oss`."
                : browsing === "openrouter"
                  ? "Connect OpenRouter to see its models."
                  : "No models to choose from."}
            </p>
          ) : shown.length === 0 ? (
            <p className="popover-empty">Nothing matches “{filter.trim()}”.</p>
          ) : (
            shown.map((model) => (
              <button
                key={model.id}
                type="button"
                role="menuitemradio"
                aria-checked={found.model === model.id}
                className="popover-item"
                onClick={() => pick(browsing, model.id)}
              >
                <span className="popover-text">
                  <span className="popover-name">{model.name || model.id}</span>
                  <span className="popover-hint">{summarise(model)}</span>
                </span>
                {found.model === model.id ? <span className="popover-tick">✓</span> : null}
              </button>
            ))
          )}
        </div>

        {matches.length > shown.length ? (
          <div className="popover-foot">
            {shown.length} of {matches.length}
            {filter.trim() ? " matches" : ` models`} — keep typing to narrow it.
          </div>
        ) : null}
      </div>
    );
  }

  return (
    <div className="popover popover-up" ref={node} role="menu">
      <div className="popover-head mi" data-strong>
        Answer with
      </div>

      <button
        type="button"
        role="menuitemradio"
        aria-checked={value === null}
        className="popover-item"
        onClick={() => {
          onChange(null);
          onClose();
        }}
      >
        <span className="popover-dot" data-ok={healthy(null) ? "" : undefined} />
        <span className="popover-text">
          <span className="popover-name">{AUTO.name}</span>
          <span className="popover-hint">{detail(null)}</span>
        </span>
        {value === null ? <span className="popover-tick">✓</span> : null}
      </button>

      {providers.map((entry) => (
        <div key={entry.id} className="popover-pair">
          <button
            type="button"
            role="menuitemradio"
            aria-checked={value === entry.id}
            className="popover-item"
            onClick={() => {
              onChange(entry.id);
              onClose();
            }}
          >
            <span className="popover-dot" data-ok={healthy(entry.id) ? "" : undefined} />
            <span className="popover-text">
              <span className="popover-name">{LABELS[entry.id]?.name || entry.name}</span>
              <span className="popover-hint">{detail(entry.id)}</span>
            </span>
            {value === entry.id ? <span className="popover-tick">✓</span> : null}
          </button>
          {/* Separate from the row it sits beside, because it does something
              else: the row chooses who answers, this opens what they have. */}
          <button
            type="button"
            className="popover-more"
            aria-label={`Choose a ${LABELS[entry.id]?.name || entry.name} model`}
            title="Choose a model"
            onClick={() => browse(entry.id)}
          >
            ›
          </button>
        </div>
      ))}

      {backend("openrouter") && !backend("openrouter").configured ? (
        <div className="popover-foot">
          <button type="button" className="mi" onClick={onManage}>
            Connect OpenRouter →
          </button>
        </div>
      ) : (
        <div className="popover-foot">{AUTO.hint}</div>
      )}
    </div>
  );
}

/** One line under a model name: what it costs, how much it holds, what it can do. */
function summarise(model) {
  const parts = [];
  if (model.free) parts.push("free");
  else if (model.prompt_price) parts.push(perMillion(model.prompt_price));
  if (model.context) parts.push(`${Math.round(model.context / 1000)}k ctx`);
  if (model.vision) parts.push("vision");
  if (model.reasoning) parts.push("reasoning");
  // Ollama has no prices and no context to report, so its rows fall back to
  // the size and quantisation the tags endpoint gives instead.
  return parts.join(" · ") || model.description || model.id;
}

/**
 * OpenRouter states prices in dollars per token, as strings.
 *
 * Strings because they are decimals with more precision than a float keeps --
 * `0.0000006` — so the conversion happens once, here, at the point where it
 * becomes a number a person reads rather than a number anything computes with.
 */
function perMillion(price) {
  const perToken = Number(price);
  if (!Number.isFinite(perToken) || perToken <= 0) return "";
  const dollars = perToken * 1_000_000;
  return `$${dollars < 1 ? dollars.toFixed(2) : dollars.toFixed(dollars < 10 ? 1 : 0)}/M`;
}
