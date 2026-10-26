/// <reference types="vitest" />
import path from "node:path"
import { fileURLToPath } from "node:url"
import tailwindcss from "@tailwindcss/vite"
import react from "@vitejs/plugin-react"
import { defineConfig, loadEnv } from "vite"

const repoRoot: string = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "..",
)

export default defineConfig(({ mode }) => {
  const env: Record<string, string> = loadEnv(mode, repoRoot, "")
  const apiProxy: string = env.VITE_API_PROXY || "http://localhost:20080"
  return {
    plugins: [react(), tailwindcss()],
    server: {
      proxy: {
        // Keep the browser's Host so the backend's Origin check sees the
        // request as same-origin (a string target implies changeOrigin: true).
        "/api": { target: apiProxy, changeOrigin: false },
      },
    },
    test: {
      environment: "happy-dom",
      include: ["src/**/*.test.{ts,tsx}"],
      globals: false,
      setupFiles: ["src/test-timezone.ts", "src/test-setup.ts"],
      coverage: {
        provider: "v8",
        include: ["src/**/*.{ts,tsx}"],
        exclude: ["src/**/*.test.{ts,tsx}", "src/test-*.ts", "src/**/*.d.ts"],
      },
    },
  }
})
