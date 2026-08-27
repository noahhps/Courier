// Dev-only: ask the Vite dev server for the token the Python server generated,
// so editing the UI doesn't start with a trip to the terminal.
//
// `import.meta.env.DEV` is replaced by a literal `false` at build time, so the
// body below is unreachable in `npm run build` and rollup drops it. Nothing in
// `dist/` looks for this endpoint, and nothing in production serves it.

export async function fetchDevToken() {
  if (!import.meta.env.DEV) return "";
  try {
    const response = await fetch("/__dev_token", { cache: "no-store" });
    if (!response.ok) return "";
    const { token } = await response.json();
    return typeof token === "string" ? token.trim() : "";
  } catch {
    // No dev server route, or it declined. The gate is the fallback.
    return "";
  }
}
