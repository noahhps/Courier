import { resolve } from "node:path";

import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The API lives in the same origin in production -- FastAPI serves `dist/` and
// `/api` from one process. In development Vite serves the UI instead, so /api
// is proxied to the real server rather than mocked: streaming, auth, and SSE
// framing are exactly the things worth exercising while editing the UI.
const API_ORIGIN = process.env.API_ORIGIN || "http://127.0.0.1:8080";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": { target: API_ORIGIN, changeOrigin: true },
      "/healthz": { target: API_ORIGIN, changeOrigin: true },
      // Where OpenRouter sends the browser back to after a sign-in. It is not
      // under /api because the redirect carries no bearer token, and it has to
      // be proxied here too: in development the address the browser knows this
      // app by is Vite's, so that is the address the callback is registered
      // with, and it has to reach the server that minted the flow.
      "/openrouter": { target: API_ORIGIN, changeOrigin: true },
    },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
    rollupOptions: {
      // Two entries, not one app with a route. QuickView is a 640px panel that
      // opens over another application and is dismissed in seconds; making it
      // load the rail, the router, Memory and the Skills page first -- none of
      // which it can show -- would be paying the whole app's startup cost on
      // every keypress.
      input: {
        main: resolve(__dirname, "index.html"),
        quickview: resolve(__dirname, "quickview.html"),
      },
    },
  },
});
