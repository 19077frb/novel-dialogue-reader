import { expect, test } from '@playwright/test'

for (const layout of [
  { width: 1280, height: 900, colorScheme: 'light' as const },
  { width: 1280, height: 768, colorScheme: 'dark' as const },
  { width: 360, height: 740, colorScheme: 'dark' as const },
]) {
  test(`compact character page fits ${layout.width}x${layout.height} ${layout.colorScheme}`, async ({ page }, testInfo) => {
    page.on('pageerror', error => console.error(error.message))
    await page.setViewportSize(layout)
    await page.emulateMedia({ colorScheme: layout.colorScheme })
    const entries = Array.from({ length: 12 }, (_, index) => ({
      character_id: `p${index}`, kind: 'book', name: `人物${index}${'长名字'.repeat(20)}`,
      aliases: ['很长的别名'.repeat(25)], description: '完整人物说明。'.repeat(60),
      chapter_count: 10, dialogue_count: 200, confirmation_source: 'automatic', version: 1,
    }))
    await page.route('**/api/**', async route => {
      const path = new URL(route.request().url()).pathname
      if (!path.startsWith('/api/')) { await route.continue(); return }
      let data: unknown = []
      if (path === '/api/health') data = { status: 'ok', version: '1.0.0', database: { state: 'READY' } }
      if (path === '/api/books/b1') data = { id: 'b1', title: '紧凑人物测试', active_version_id: 'v1' }
      if (path === '/api/books/b1/character-directory') data = entries
      if (path === '/api/books/b1/character-directory/auto-merge') data = null
      if (path === '/api/jobs/queue') data = { items: [], next_cursor: null }
      await route.fulfill({ json: path === '/api/health' ? data : { data, request_id: 'offline-card-test' } })
    })
    await page.goto('/books/b1/characters')
    const directory = page.locator('.ndr-character-directory')
    const cards = directory.locator('article:visible')
    await expect(cards.first()).toBeVisible()
    await directory.scrollIntoViewIfNeeded()
    await expect.poll(async () => (await directory.boundingBox())?.height ?? Infinity).toBeLessThan(layout.height - 32)
    const collapsedHeight = (await cards.first().boundingBox())!.height
    expect(collapsedHeight).toBeLessThan(250)
    expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBeLessThanOrEqual(1)
    await page.screenshot({ path: testInfo.outputPath('compact-cards.png'), fullPage: true })
    const first = cards.first()
    await expect(first.getByLabel('姓名')).toBeHidden()
    await first.getByRole('button', { name: '展开编辑与合并' }).click()
    await expect(first.getByLabel('姓名')).toBeVisible()
    expect((await first.boundingBox())!.height).toBeGreaterThan(collapsedHeight * 2)
    await first.getByLabel('姓名').fill('未保存草稿')
    await first.getByRole('button', { name: '收起编辑与合并' }).click()
    await expect(first.getByText('有未保存的修改')).toBeVisible()
    const next = directory.getByRole('button', { name: '下一页' })
    await next.click()
    await directory.getByRole('button', { name: '上一页' }).click()
    await first.getByRole('button', { name: '展开编辑与合并' }).click()
    await expect(first.getByLabel('姓名')).toHaveValue('未保存草稿')
  })
}
