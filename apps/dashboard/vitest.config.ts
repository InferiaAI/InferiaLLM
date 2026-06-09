import path from "path";
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  test: {
    environment: "jsdom",
    environmentOptions: {
      jsdom: {
        url: "http://localhost/",
      },
    },
    globals: true,
    setupFiles: ["./src/setupTests.ts"],
    css: false,
    coverage: {
      provider: "v8",
      include: ["src/lib/authMode.ts", "src/services/authService.ts", "src/lib/tokenStore.ts", "src/App.tsx", "src/pages/Login.tsx"],
      exclude: ["src/main.tsx", "src/setupTests.ts", "**/*.test.{ts,tsx}", "**/__tests__/**"],
      reporter: ["text", "html"],
    },
  },
});
