import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  // Sub-path deploys (e.g. company.com/my-svc/rca): set VITE_BASE_PATH at build.
  // Bakes asset URLs + import.meta.env.BASE_URL, which the router basename and
  // the API fetch prefix both read. Default "/" (root).
  base: process.env.VITE_BASE_PATH || "/",
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/investigation": "http://localhost:8000",
      "/investigations": "http://localhost:8000",
      "/conversation": "http://localhost:8000",
      "/agent-config": "http://localhost:8000",
      "/kb": "http://localhost:8000",
    },
  },
  build: {
    outDir: "dist",
    sourcemap: true,
  },
  test: {
    environment: "node",
    include: ["src/**/*.test.{ts,tsx}"],
  },
});
