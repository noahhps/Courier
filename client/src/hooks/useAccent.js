import { useCallback, useEffect, useState } from "react";

const KEY = "unified-llm-accent";

/**
 * The accent options from the sheet, plus the one the app currently wears.
 *
 * `ground` is not decoration: four of these five are dark colours, drawn for a
 * paper sheet. Applied as an accent on the near-black ground they would be
 * invisible -- ink on ink. So each choice carries the surface it was designed
 * against, and picking one swaps the whole palette rather than a single value.
 */
export const ACCENTS = [
  { id: "acid", label: "Acid", swatch: "#d6f249", ground: "#0b0c0a" },
  { id: "blue", label: "Blue", swatch: "#0000cc", ground: "#faf9f6" },
  { id: "navy", label: "Navy", swatch: "#061b4a", ground: "#faf9f6" },
  { id: "ink", label: "Ink", swatch: "#14161a", ground: "#faf9f6" },
  { id: "green", label: "Green", swatch: "#1f4d3d", ground: "#faf9f6" },
];

const known = (id) => ACCENTS.some((a) => a.id === id);

export function useAccent() {
  const [accent, setAccent] = useState(() => {
    const saved = localStorage.getItem(KEY);
    return known(saved) ? saved : ACCENTS[0].id;
  });

  useEffect(() => {
    document.documentElement.dataset.accent = accent;
    localStorage.setItem(KEY, accent);

    // The phone paints its chrome from this, so it has to move with the
    // ground or the status bar ends up the wrong colour against the app.
    const meta = document.querySelector('meta[name="theme-color"]');
    const chosen = ACCENTS.find((a) => a.id === accent);
    if (meta && chosen) meta.setAttribute("content", chosen.ground);
  }, [accent]);

  return [accent, useCallback((id) => known(id) && setAccent(id), [])];
}
