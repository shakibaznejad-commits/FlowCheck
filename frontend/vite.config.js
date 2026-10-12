import { defineConfig } from "vite";

export default defineConfig({
  server: {
    host: "0.0.0.0",
    proxy: {
      "/api": {
        target: process.env.BACKEND_URL || "http://127.0.0.1:8000",
        rewrite: (path) => path === "/api/health" ? "/health" : path,
      },
    },
  },
});
