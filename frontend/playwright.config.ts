import { defineConfig, devices } from '@playwright/test'

/**
 * E2E 使用独立端口与独立数据目录，绝不接触用户书库，
 * 也不会与开发中的 8765/5173 冲突（DEVELOPMENT.md 2.3）。
 */
const E2E_API_PORT = Number(process.env.NDR_E2E_API_PORT ?? 8795)
const E2E_UI_PORT = Number(process.env.NDR_E2E_UI_PORT ?? 5273)
const E2E_DATA_DIR = process.env.NDR_E2E_DATA_DIR ?? '.e2e/data'

// 受限环境中 uv 缓存可能不可访问，可用该变量回退到 backend\.venv 的解释器。
const backendCommand =
  process.env.NDR_E2E_BACKEND_CMD ?? 'uv run --project ../backend python -m ndr'

export default defineConfig({
  testDir: './e2e',
  timeout: 30_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  workers: 1,
  reporter: [['list']],
  use: {
    baseURL: `http://127.0.0.1:${E2E_UI_PORT}`,
    trace: 'retain-on-failure',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: [
    {
      command: backendCommand,
      url: `http://127.0.0.1:${E2E_API_PORT}/api/health`,
      reuseExistingServer: false,
      timeout: 120_000,
      env: {
        NDR_PORT: String(E2E_API_PORT),
        NDR_HOST: '127.0.0.1',
        NDR_DATA_DIR: E2E_DATA_DIR,
        // E2E 使用隔离数据目录，启动时自动迁移到 head（默认行为仍是显式迁移）。
        NDR_AUTO_MIGRATE: '1',
      },
    },
    {
      command: `npm run dev -- --host 127.0.0.1 --port ${E2E_UI_PORT} --strictPort`,
      url: `http://127.0.0.1:${E2E_UI_PORT}`,
      reuseExistingServer: false,
      timeout: 120_000,
      env: {
        NDR_DEV_API_TARGET: `http://127.0.0.1:${E2E_API_PORT}`,
      },
    },
  ],
})
