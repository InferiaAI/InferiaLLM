// Temporary: dev server that proxies the API to the tunnelled GPU box, so the
// browser stays same-origin and CORS does not apply. Delete when finished.
import path from "path"
import { defineConfig } from "vite"
import react from "@vitejs/plugin-react"

const BOX = "http://localhost:8000"

export default defineConfig({
  base: "/",
  plugins: [react()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  server: {
    port: 5173,
    proxy: {
      "/api": { target: BOX, changeOrigin: true, ws: true },
      "/inf": { target: BOX, changeOrigin: true, ws: true },
      "/deployment": { target: BOX, changeOrigin: true },
      "/inventory": { target: BOX, changeOrigin: true },
    },
  },
})
