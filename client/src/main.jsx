import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import App from "./App";
import { DialogProvider } from "./components/Dialog";
import "./styles.css";

/* Mark the document when we are inside the desktop shell.
 *
 * The rail is glass there and flat grey in a browser, and CSS cannot work that
 * out for itself: it turns on whether a native material exists behind the
 * window. Set before the first paint, so the app never renders one and flips.
 *
 * Tested on the IPC bridge the shell injects, not on whether `@tauri-apps/api`
 * imports -- that package is an ordinary dependency bundled into the browser
 * build too, so importing it succeeds everywhere and would answer "yes" on a
 * page with no shell behind it. */
if (
  typeof window.__TAURI_INTERNALS__ !== "undefined" ||
  // The custom scheme the shell serves the bundle from. Belt and braces: the
  // internals object is the documented signal, and this is the one that cannot
  // be absent if the page is running inside the shell at all.
  window.location.protocol.startsWith("tauri")
) {
  document.documentElement.setAttribute("data-shell", "tauri");
}

createRoot(document.getElementById("root")).render(
  <StrictMode>
    {/* Outside App so the gate can raise a dialog too. */}
    <DialogProvider>
      <App />
    </DialogProvider>
  </StrictMode>,
);

if ("serviceWorker" in navigator) {
  // The worker caches the shell only -- never conversation data.
  navigator.serviceWorker.register("/sw.js").catch(() => {});
}
