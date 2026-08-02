import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Telegram serves the Mini App from an opaque, non-root path inside its
// WebView — `base: "./"` keeps every asset reference relative so the build
// works regardless of where Telegram mounts it.
export default defineConfig({
  plugins: [react()],
  base: "./",
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
