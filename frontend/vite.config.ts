import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

// 开发前端默认 127.0.0.1:5173，并把 /api 代理到本地后端 127.0.0.1:8765。
// E2E 使用独立端口与数据目录，通过 NDR_DEV_API_TARGET 覆盖代理目标。
const apiTarget = process.env.NDR_DEV_API_TARGET ?? 'http://127.0.0.1:8765'

export default defineConfig({
  plugins: [react()],
  server: {
    host: '127.0.0.1',
    port: 5173,
    strictPort: true,
    proxy: {
      '/api': {
        target: apiTarget,
        changeOrigin: false,
      },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: true,
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./vitest.setup.ts'],
    include: ['tests/**/*.test.{ts,tsx}'],
  },
})
