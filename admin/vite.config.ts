import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    proxy: {
      "/api": { target: "http://127.0.0.1:8000" },
      "/v1": { target: "http://127.0.0.1:8000" },
      "/connectors": { target: "http://127.0.0.1:8000" },
    },
  },
  test: {
    // Playwright E2E 测试由 @playwright/test 运行，vitest 不得误抓 e2e/ 下的 spec。
    exclude: ["e2e/**", "node_modules/**"],
  },
});
