import { defineConfig, devices } from '@playwright/test'

/**
 * E2E 使用独立端口与**每次运行独立的数据目录**，绝不接触用户书库，
 * 也不会与开发中的 8765/5173 冲突。
 */
const E2E_API_PORT = Number(process.env.NDR_E2E_API_PORT ?? 8795)
const E2E_UI_PORT = Number(process.env.NDR_E2E_UI_PORT ?? 5273)

// 每次运行一个全新的数据目录，避免上一轮残留的 SQLite 句柄导致清理失败。
const E2E_RUN_ID = process.env.NDR_E2E_RUN_ID ?? String(Date.now())
const E2E_DATA_DIR = process.env.NDR_E2E_DATA_DIR ?? `.e2e/data-${E2E_RUN_ID}`
process.env.NDR_E2E_DATA_DIR = E2E_DATA_DIR

// 受限环境中 uv 缓存可能不可访问，可用该变量回退到 backend\.venv 的解释器。
const backendCommand =
  process.env.NDR_E2E_BACKEND_CMD ?? 'uv run --project ../backend python -m ndr'

export default defineConfig({
  testDir: './e2e',
  globalSetup: './e2e/global-setup.ts',
  timeout: 45_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  workers: 1,
  reporter: [['list']],
  use: {
    // Optional installed Chromium channel (e.g. msedge); otherwise Playwright Chromium.
    channel: process.env.NDR_E2E_BROWSER_CHANNEL,
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
        // 测试绝不触碰真实系统凭据库：显式使用会话凭据后端。
        NDR_CREDENTIAL_BACKEND: 'session',
        // 仅测试：允许 FakeProvider 用于验证连接测试与预览着色（不会访问网络）。
        NDR_ALLOW_FAKE_PROVIDER: '1',
        // 仅测试：让 FakeProvider 确定性地建一个分组，离线验证颜色/编号链路（真实提供方不受影响）。
        NDR_FAKE_PROVIDER_LABELS: 'deterministic',
        NDR_LLM_TIMEOUT_SECONDS: '5',
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
