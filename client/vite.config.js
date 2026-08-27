import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The API lives in the same origin in production -- FastAPI serves `dist/` and
// `/api` from one process. In development Vite serves the UI instead, so /api
// is proxied to the real server rather than mocked: streaming, auth, and SSE
// framing are exactly the things worth exercising while editing the UI.
const API_ORIGIN = process.env.API_ORIGIN || "http://127.0.0.1:8080";

const HERE = path.dirname(fileURLToPath(import.meta.url));
// Where config.py writes it: beside the database, which defaults to data/chat.db.
const TOKEN_FILE = process.env.TOKEN_FILE || path.resolve(HERE, "..", "data", "token");

const LOOPBACK = new Set(["127.0.0.1", "::1", "::ffff:127.0.0.1"]);

/**
 * Hand the dev UI the token the server already generated.
 *
 * Editing the front end otherwise begins with a trip to the terminal to copy
 * the token into the gate, once per browser profile and again every time the
 * server regenerates it. This removes the paste, not the check: every /api
 * call still carries the bearer header and the server still verifies it. The
 * token is delivered automatically rather than waived.
 *
 * `apply: "serve"` confines this to `npm run dev`. It is not in the bundle,
 * and FastAPI -- the thing that serves the UI in production -- has no such
 * route. Set `GANTRY_NO_DEV_TOKEN=1` to type it by hand anyway, which is what
 * you want when the gate itself is the thing being worked on.
 */
function devTokenPlugin() {
  return {
    name: "gantry-dev-token",
    apply: "serve",
    configureServer(server) {
      server.middlewares.use("/__dev_token", (req, res) => {
        // Answer even when opted out, rather than leaving the route to fall
        // through to the SPA shell: a 404 is what the client is looking for,
        // and a 200 of index.html only works by way of a JSON parse error.
        if (process.env.GANTRY_NO_DEV_TOKEN) {
          res.statusCode = 404;
          res.end("dev token disabled");
          return;
        }

        // `vite --host` puts the dev server on the LAN. Reading the file that
        // *is* the perimeter and handing it to whoever asks is not something
        // to do on a network, however convenient it is on this machine.
        if (!LOOPBACK.has(req.socket.remoteAddress || "")) {
          res.statusCode = 403;
          res.end("dev token is loopback-only");
          return;
        }

        // Read per request rather than at startup: on a first run the server
        // writes this file after Vite is already up, and it changes whenever
        // the token is regenerated.
        let token = (process.env.AUTH_TOKEN || "").trim();
        if (!token) {
          try {
            token = readFileSync(TOKEN_FILE, "utf8").trim();
          } catch {
            // Not generated yet. The gate is the fallback, as before.
          }
        }
        if (!token) {
          res.statusCode = 404;
          res.end("no token on disk yet");
          return;
        }

        res.setHeader("Content-Type", "application/json");
        res.setHeader("Cache-Control", "no-store");
        res.end(JSON.stringify({ token }));
      });
    },
  };
}

export default defineConfig({
  plugins: [react(), devTokenPlugin()],
  server: {
    proxy: {
      "/api": { target: API_ORIGIN, changeOrigin: true },
      "/healthz": { target: API_ORIGIN, changeOrigin: true },
    },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
});
