import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

const backend = "http://127.0.0.1:8100";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5178,
    strictPort: true,
    proxy: {
      "/api": backend,
      "/admin": backend,
      "/static": backend,
    },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
  test: {
    environment: "jsdom",
    globals: true,
    restoreMocks: true,
  },
});
