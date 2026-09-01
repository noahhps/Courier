/* Where the API lives, which is not the same question in every host.
 *
 * The same `dist/` is served two ways: FastAPI mounts it at the root, and the
 * Tauri app bundles the identical files behind a custom protocol. So this has
 * to be decided at *runtime*, not by a build flag -- one build, two hosts, and
 * a `VITE_` variable baked in at compile time would be wrong for whichever
 * host it was not built for.
 *
 * In a browser the answer is the empty string. Relative `/api` already
 * resolves to the server that served the page, which is the behaviour that has
 * always worked and the one the service worker's pass-through rule is written
 * against.
 *
 * Under Tauri there is no such server. The page comes from `tauri://localhost`
 * and the API is somewhere else entirely -- usually a sidecar on this machine,
 * sometimes the GPU box across the network -- so it needs a full origin, and
 * the reader has to be able to change it without a rebuild.
 */

const ORIGIN_KEY = "unified-llm-server-origin";

// The sidecar's default. Loopback rather than localhost: the Python server
// binds 127.0.0.1 by default, and on a machine where localhost resolves to ::1
// first the two are not the same address.
export const DEFAULT_ORIGIN = "http://127.0.0.1:8080";

/** Whether this bundle is running inside the Tauri shell rather than a browser. */
export function isDesktop() {
  // v2's marker. Checked defensively because this module is imported by the
  // browser build too, where `window` exists but the marker never will.
  return typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;
}

/**
 * Whether the page itself arrived over http(s).
 *
 * True in a browser, and also true under `tauri dev`, where the window loads
 * from Vite on :5173 rather than from the bundled protocol. That case looks
 * like desktop by every other measure and must still be treated as
 * same-origin: Vite proxies `/api` to the real server, so a relative path
 * works, while an absolute one would skip the proxy and arrive at the API as
 * a cross-origin request from an origin the server does not allow.
 */
function servedOverHttp() {
  return (
    typeof window !== "undefined" && /^https?:$/.test(window.location.protocol)
  );
}

/**
 * Whether this host has to be *told* where the server is.
 *
 * Only the bundled app does. A browser is talking to the server that sent it
 * the page, and `tauri dev` has a proxy standing in for one -- in both cases
 * the question has a single possible answer and asking it would be noise.
 */
export function needsExplicitOrigin() {
  return isDesktop() && !servedOverHttp();
}

/**
 * The origin to prefix `/api` with. Empty string in a browser.
 *
 * Trailing slashes are stripped so callers can concatenate without thinking
 * about it -- `http://host:8080/` + `/api` would otherwise produce a double
 * slash that FastAPI answers with a redirect the bearer header does not
 * survive.
 */
export function serverOrigin() {
  if (!needsExplicitOrigin()) return "";
  let saved = null;
  try {
    saved = localStorage.getItem(ORIGIN_KEY);
  } catch {
    // Private mode, or storage disabled. The default is still correct.
  }
  return (saved || DEFAULT_ORIGIN).replace(/\/+$/, "");
}

/** Remember a different API host. Empty clears it back to the default. */
export function setServerOrigin(origin) {
  const value = (origin || "").trim().replace(/\/+$/, "");
  try {
    if (value) localStorage.setItem(ORIGIN_KEY, value);
    else localStorage.removeItem(ORIGIN_KEY);
  } catch {
    // Nothing durable to do about it; the app still works for this run.
  }
}
