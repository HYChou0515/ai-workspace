import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/workspaces": "http://localhost:8000",
      "/workspace": "http://localhost:8000",
      "/conversation": "http://localhost:8000",
      "/agent-config": "http://localhost:8000",
    },
  },
  build: {
    outDir: "dist",
    sourcemap: true,
  },
});
