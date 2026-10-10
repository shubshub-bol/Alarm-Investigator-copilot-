import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// In development the Vite server proxies /api to the copilot backend, so the browser talks to one origin.
// In Docker, nginx does the same. VITE_API_BASE_URL can point the app at another backend instead.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { "/api": process.env.COPILOT_BACKEND_URL ?? "http://localhost:8080" },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
  },
});
