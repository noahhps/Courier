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
    },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
});
