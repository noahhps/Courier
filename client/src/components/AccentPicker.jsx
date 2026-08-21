import { ACCENTS } from "../hooks/useAccent";
import { Icon } from "./Icon";

/** The swatch row from the sheet's style panel, as a control. */
export function AccentPicker({ value, onChange }) {
  return (
    <div className="accent">
      <span className="accent-label">accent</span>
      <div className="accent-row" role="group" aria-label="Accent colour">
        {ACCENTS.map((option) => (
          <button
            key={option.id}
            type="button"
            className="accent-swatch"
            style={{ "--swatch": option.swatch }}
            title={option.label}
            aria-label={option.label}
            aria-pressed={value === option.id}
            onClick={() => onChange(option.id)}
          >
            {value === option.id ? <Icon name="check" /> : null}
          </button>
        ))}
      </div>
    </div>
  );
}
