import { expect, test } from '@playwright/test'

/**
 * T00 连通性冒烟：真实的 Vite 前端 + 真实后端，页面必须显示后端返回的状态，
 * 而不是前端硬编码文本。
 */
test('首页显示后端 /api/health 返回的真实状态', async ({ page }) => {
  await page.goto('/')

  await expect(page.getByRole('heading', { name: '轻小说对话辅助阅读器' })).toBeVisible()

  const status = page.getByTestId('health-ok')
  await expect(status).toBeVisible()
  await expect(status).toContainText('ok')
  // E2E 后端使用隔离数据目录并自动迁移，因此页面必须如实显示 READY。
  await expect(status).toContainText('READY')
})

test('经 Vite 代理的 /api/health 可用且不调用模型', async ({ request }) => {
  const response = await request.get('/api/health')
  expect(response.status()).toBe(200)

  const body = await response.json()
  expect(body.status).toBe('ok')
  expect(body.app).toBe('novel-dialogue-reader')
  expect(body.database.state).toBe('READY')
  expect(body.database.revision).toBe(body.database.head_revision)
})
