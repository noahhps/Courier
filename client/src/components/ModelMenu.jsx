import { useEffect, useRef } from "react";

/* The menu behind the circle at the foot of the rail.
 *
 * It chooses a *provider*, not a model, and the distinction is the server's
 * rather than a simplification here: `/api/chat` takes "local", "cloud" or
 * nothing, and nothing means the router decides. There is no endpoint that
 * lists the models Ollama has pulled, so picking between gpt-oss and gemma4
 * from here is not possible yet -- that is the phase 6 model picker, and it
 * needs `/api/models` before this menu can grow a third section.
 *
 * Anchored upward from the foot of the rail, because that is where its circle
 * is and a menu that opens away from its trigger is a menu you lose.
 */

const CHOICES = [
  {
    id: null,
    name: "Auto",
    hint: "Local first, cloud only if local is unreachable",
  },
  { id: "local", name: "Local", hint: "On this machine" },
  { id: "cloud", name: "Cloud", hint: "Anthropic fallback" },
];

export function ModelMenu({ open, status, value, onChange, onClose }) {
  const node = useRef(null);

  // Close on a click anywhere else, and on Escape. Both listeners only exist
  // while the menu is up, so a closed menu costs nothing.
  useEffect(() => {
    if (!open) return undefined;
    const away = (event) => {
      if (node.current && !node.current.contains(event.target)) onClose();
    };
    const key = (event) => {
      if (event.key === "Escape") onClose();
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

  if (!open) return null;

  const detail = (id) => {
    if (id === "local") return status?.local?.model || "not reachable";
    if (id === "cloud") return status?.cloud?.model || "not configured";
    return status?.serving === "cloud" ? "using cloud" : "using local";
  };
  const healthy = (id) => {
    if (id === "local") return status?.local?.healthy;
    if (id === "cloud") return status?.cloud?.healthy;
    return status?.serving !== "none";
  };

  return (
    <div className="popover popover-up" ref={node} role="menu">
      <div className="popover-head mi" data-strong>
        Answer with
      </div>
      {CHOICES.map((choice) => (
        <button
          key={choice.id || "auto"}
          type="button"
          role="menuitemradio"
          aria-checked={value === choice.id}
          className="popover-item"
          onClick={() => {
            onChange(choice.id);
            onClose();
          }}
        >
          <span className="popover-dot" data-ok={healthy(choice.id) ? "" : undefined} />
          <span className="popover-text">
            <span className="popover-name">{choice.name}</span>
            <span className="popover-hint">{detail(choice.id)}</span>
          </span>
          {value === choice.id ? <span className="popover-tick">✓</span> : null}
        </button>
      ))}
      <div className="popover-foot">{CHOICES[0].hint}</div>
    </div>
  );
}
