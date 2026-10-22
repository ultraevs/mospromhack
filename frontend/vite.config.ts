import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, "..", "");
  // порты из общего .env в корне
  const backend = `http://127.0.0.1:${env.BACKEND_PORT || 8000}`;
  return {
    plugins: [react(), tailwindcss()],
    server: {
      port: Number(env.FRONTEND_PORT || 5173),
      strictPort: true,
      proxy: {
        "/api": backend,
        "/media": backend,
        "/ws": { target: backend, ws: true },
      },
    },
    test: { environment: "jsdom", globals: true },
  };
});
