import { useId } from "react";

import { oklch } from "../lib/color";
import { DEFAULT_STRENGTH, PRESETS, seedOf } from "../lib/theme";

/* The swatch row, in all three places an accent can be set.
 *
 * One component rather than three, because the three scopes differ in exactly
 * one way: whether "nothing chosen here" is an option. The app has to have an
 * accent -- it is the bottom of the stack -- while a chat and a project may
 * decline and let the scope above them decide. That is `inherit`.
 *
 * Every swatch draws itself in the colour it sets, which is the only honest
 * way to label a colour. The auto swatch draws whatever the conversation
 * currently resolves to, so it is a preview rather than a promise.
 */
export function ThemePicker({
  value,
  onChange,
  scope = "app",
  seed = null,
  inheritedLabel = "",
  disabled = false,
}) {
  const sliderId = useId();
  const mode = value?.mode || (scope === "app" ? "auto" : "inherit");
  const strength = value?.strength ?? DEFAULT_STRENGTH;

  // Keep the strength across a change of mode: someone who has turned the wash
  // down to a hairline means it about the app, not about cobalt in particular.
  const pick = (next) => onChange(next ? { ...next, strength } : null);

  const autoSeed = seed || { hue: 264.5, chroma: 0.14 };

  return (
    <div className="accents" data-disabled={disabled ? "" : undefined}>
      <div className="accent-row" role="group" aria-label="Accent">
        {scope !== "app" ? (
          <button
            type="button"
            className="accent"
            data-kind="inherit"
            data-on={mode === "inherit" ? "" : undefined}
            disabled={disabled}
            aria-pressed={mode === "inherit"}
            title={inheritedLabel || "Follow the scope above"}
            onClick={() => pick(null)}
          >
            <i />
            <span className="mi">auto-inherit</span>
          </button>
        ) : null}

        <button
          type="button"
          className="accent"
          data-kind="auto"
          data-on={mode === "auto" ? "" : undefined}
          disabled={disabled}
          aria-pressed={mode === "auto"}
          title="Taken from what the conversation is about"
          onClick={() => pick({ mode: "auto" })}
          style={{
            "--a": oklch(0.66, autoSeed.chroma, autoSeed.hue),
            "--b": oklch(0.8, autoSeed.chroma * 0.8, autoSeed.hue + 40),
          }}
        >
          <i />
          <span className="mi">from the chat</span>
        </button>

        {PRESETS.map((preset) => (
          <button
            key={preset.id}
            type="button"
            className="accent"
            data-on={mode === "preset" && value?.preset === preset.id ? "" : undefined}
            disabled={disabled}
            aria-pressed={mode === "preset" && value?.preset === preset.id}
            title={preset.name}
            onClick={() => pick({ mode: "preset", preset: preset.id })}
            style={{ "--a": oklch(0.62, preset.chroma, preset.hue) }}
          >
            <i />
            <span className="mi">{preset.name}</span>
          </button>
        ))}

        <button
          type="button"
          className="accent"
          data-kind="off"
          data-on={mode === "off" ? "" : undefined}
          disabled={disabled}
          aria-pressed={mode === "off"}
          title="No accent -- the palette this app ships with"
          onClick={() => pick({ mode: "off" })}
        >
          <i />
          <span className="mi">none</span>
        </button>
      </div>

      {/* Both sliders are hidden when there is no colour to adjust: a strength
          control under an accent that is off, or inherited from elsewhere,
          adjusts nothing and says otherwise. */}
      {mode !== "off" && mode !== "inherit" ? (
        <div className="accent-dials">
          <label className="accent-dial" htmlFor={`${sliderId}-strength`}>
            <span className="mi">Intensity</span>
            <input
              id={`${sliderId}-strength`}
              type="range"
              min="0"
              max="1"
              step="0.05"
              value={strength}
              disabled={disabled}
              onChange={(event) =>
                onChange({
                  ...(value || { mode: "auto" }),
                  strength: Number(event.target.value),
                })
              }
            />
          </label>

          {mode === "custom" ? (
            <label className="accent-dial" htmlFor={`${sliderId}-hue`}>
              <span className="mi">Hue</span>
              <input
                id={`${sliderId}-hue`}
                type="range"
                min="0"
                max="360"
                step="1"
                value={value?.hue ?? 264}
                disabled={disabled}
                className="accent-hue"
                onChange={(event) =>
                  onChange({
                    mode: "custom",
                    hue: Number(event.target.value),
                    chroma: value?.chroma ?? 0.14,
                    strength,
                  })
                }
              />
            </label>
          ) : (
            <button
              type="button"
              className="mi"
              data-act
              disabled={disabled}
              onClick={() => {
                const from = seedOf(value, seed) || autoSeed;
                pick({ mode: "custom", hue: from.hue, chroma: from.chroma });
              }}
            >
              pick a hue
            </button>
          )}
        </div>
      ) : null}
    </div>
  );
}
