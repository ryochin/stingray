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
        "/api": apiProxy,
      },
    },
    test: {
      environment: "happy-dom",
      include: ["src/**/*.test.{ts,tsx}"],
      globals: false,
      setupFiles: ["src/test-setup.ts"],
    },
  }
})
