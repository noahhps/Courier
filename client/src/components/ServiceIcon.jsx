import { useEffect, useState } from "react";
import { Icon } from "./Icon";
import { iconForServer } from "../lib/mcpIcons";

/* The logo an MCP server wears, with two fallbacks behind it.
 *
 * A real logo first -- these rows are read as brands ("the GitHub one"), and a
 * wall of identical glyphs is the slowest possible way to find one of fourteen
 * services. Then a monogram, then the functional glyph the rest of the shell
 * uses.
 *
 * Unlike every other icon here, a fetched logo is drawn as an <img> rather than
 * as a mask over currentColor. Masking exists so a Material silhouette inherits
 * the row's colour; doing it to a brand mark would throw the brand away and
 * leave a violet blob. So this one keeps its own colours, deliberately, and is
 * the only thing in the shell that does.
 */

/* Deterministic, so a server keeps the same colour across reloads and devices,
 * and two servers next to each other rarely collide. Hue only: saturation and
 * lightness come from the theme's own tokens via colour-mix, so a monogram sits
 * in the same register as everything around it in either theme. */
function hueFor(name) {
  let hash = 0;
  for (let i = 0; i < name.length; i += 1) {
    hash = (hash * 31 + name.charCodeAt(i)) | 0;
  }
  return Math.abs(hash) % 360;
}

/* First letter of each of the first two words: "google_workspace" -> "GW",
 * "figma" -> "F". Underscores and hyphens count as spaces, because that is how
 * these names are actually written. */
function monogram(name) {
  const words = String(name || "?")
    .split(/[\s_\-./]+/)
    .filter(Boolean);
  if (!words.length) return "?";
  const letters = words.slice(0, 2).map((word) => word[0].toUpperCase());
  return letters.join("").slice(0, 2);
}

export function ServiceIcon({ api, name, serverId, presetId, hasIcon = true, off = false }) {
  const [url, setUrl] = useState(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    if (!api || !hasIcon || (!serverId && !presetId)) return undefined;

    let revoked = false;
    let objectUrl = null;

    (async () => {
      try {
        const blob = serverId
          ? await api.mcpServerIcon(serverId)
          : await api.mcpPresetIcon(presetId);
        if (revoked) return;
        objectUrl = URL.createObjectURL(blob);
        setUrl(objectUrl);
      } catch {
        // A 404 is the ordinary answer for a site with no favicon, and for a
        // stdio server with no website at all. Fall through to the monogram.
        if (!revoked) setFailed(true);
      }
    })();

    return () => {
      revoked = true;
      // Object URLs are held by the document until revoked; a list that
      // re-renders on every sync would leak one per row per render.
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [api, serverId, presetId, hasIcon]);

  if (url && !failed) {
    return (
      <span className="mcp-avatar" data-logo="" data-off={off ? "" : undefined}>
        <img src={url} alt="" aria-hidden="true" />
      </span>
    );
  }

  // A name the glyph table recognises still gets its glyph -- gmail, calendar
  // and figma read better as their own marks than as "GM", "GC", "FI".
  const glyph = iconForServer(name);
  if (glyph !== "device_hub") {
    return (
      <span className="mcp-avatar" data-off={off ? "" : undefined} aria-hidden="true">
        <Icon name={glyph} />
      </span>
    );
  }

  return (
    <span
      className="mcp-avatar"
      data-mono=""
      data-off={off ? "" : undefined}
      style={off ? undefined : { "--hue": hueFor(String(name || "")) }}
      aria-hidden="true"
    >
      {monogram(name)}
    </span>
  );
}
