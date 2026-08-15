import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The root VERSION file is the one source of truth for the release
// version; app/core/version.py reads the same file for /health. Reading it
// here rather than from package.json is what keeps the two halves from
// drifting — package.json no longer carries a version at all.
//
// Baked in at build time, deliberately, rather than fetched at runtime: a
// constant compiled into the bundle describes *the bundle the browser is
// actually running*, which is exactly what needs verifying after a deploy.
// A runtime fetch would report what the server believes, and those two
// disagree precisely in the case worth catching — a stale cached bundle.
const APP_VERSION = readFileSync(
  fileURLToPath(new URL("../VERSION", import.meta.url)),
  "utf8",
).trim();

// Telegram serves the Mini App from an opaque, non-root path inside its
// WebView — `base: "./"` keeps every asset reference relative so the build
// works regardless of where Telegram mounts it.
export default defineConfig({
  plugins: [react()],
  base: "./",
  define: {
    __APP_VERSION__: JSON.stringify(APP_VERSION),
  },
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: "dist",
    sourcemap: false,
  },
});
