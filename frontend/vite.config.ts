import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";

export default defineConfig({
  plugins: [vue()],
  server: {
    host: "0.0.0.0",
    port: 5173,
    // /api → api_dashboard（compose service，容器内 8000）
    proxy: {
      "/api": {
        target: process.env.API_TARGET ?? "http://127.0.0.1:8000",
        changeOrigin: true,
      },
    },
  },
  test: {
    environment: "jsdom",
    include: ["tests/**/*.spec.ts"],
  },
});
