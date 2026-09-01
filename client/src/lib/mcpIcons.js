/* Which glyph stands for which MCP server.
 *
 * Matched on the server's own name rather than on the preset id it came from,
 * because a server can be added by hand as well as installed from a preset --
 * someone whose server is called "work-gmail" should still get the envelope.
 *
 * Order matters and the list is longest-first: `google_calendar` has to be
 * tested before `calendar` would swallow it, and before `google_workspace`
 * claims anything merely Google-shaped.
 */
const RULES = [
  ["google_calendar", "calendar"],
  ["google_workspace", "apps"],
  ["workspace", "apps"],
  ["calendar", "calendar"],
  ["gmail", "mail"],
  ["mail", "mail"],
  ["figma", "design"],
  ["drive", "apps"],
];

/* An unrecognised server is still a server, so this is a real answer rather
 * than a blank: the graph glyph says "something federated is behind this".  */
export const MCP_FALLBACK_ICON = "device_hub";

export function iconForServer(name) {
  const needle = String(name || "").toLowerCase();
  for (const [pattern, icon] of RULES) {
    if (needle.includes(pattern)) return icon;
  }
  return MCP_FALLBACK_ICON;
}
