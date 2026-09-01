import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import App from "./App";
import { DialogProvider } from "./components/Dialog";
import "./styles.css";

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
