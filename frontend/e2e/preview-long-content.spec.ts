import { expect, test, type Page } from '@playwright/test'

async function expectPageFits(page: Page) {
  const size = await page.evaluate(() => ({
    width: document.documentElement.scrollWidth, height: document.documentElement.scrollHeight,
    viewportWidth: innerWidth, viewportHeight: innerHeight,
  }))
  expect(size.width).toBeLessThanOrEqual(size.viewportWidth + 1)
  expect(size.height).toBeLessThanOrEqual(size.viewportHeight + 1)
}

test('长章节、多人名单与批量结果均在步骤内浏览', async ({ page, request }, testInfo) => {
  const paragraphs = Array.from({ length: 300 }, (_, i) =>
    `阿禾把第${i + 1}封信递给小林。「请读给我听。」小林展开信纸，「这是今天的消息。」`)
  const imported = await request.post('/api/books/import', { multipart: { file: {
    name: '长信回归.txt', mimeType: 'text/plain', buffer: Buffer.from(['第一章 来信', ...paragraphs].join('\n\n')),
  } } })
  expect(imported.ok()).toBe(true)
  const { book_id: id } = (await imported.json()).data
  const chapters = (await (await request.get(`/api/books/${id}/chapters`)).json()).data
  const profile = await request.post('/api/model-profiles', { data: {
    name: '长章节排版测试', protocol: 'fake-provider', model: 'fake-model',
    base_url: 'http://127.0.0.1:1', credential_mode: 'none', params: {},
  } })
  expect(profile.ok()).toBe(true)
  await page.setViewportSize({ width: 1920, height: 1080 })
  await page.goto(`/books/${id}/preview?chapterId=${chapters[0].id}#processing-workflow`)
  await expect(page.getByTestId('window-picker')).toBeVisible()
  await expectPageFits(page)
  await page.getByTestId('preview-step-roster').click()
  for (let i = 0; i < 5; i++) await page.getByTestId('roster-add-character').click()
  await expect(page.getByTestId('roster-candidates').locator('article')).toHaveCount(5)
  await expect(page.getByTestId('roster-confirm')).toBeInViewport({ ratio: 1 })
  await expectPageFits(page)
  await page.screenshot({ path: testInfo.outputPath('roster-1920.png') })

  for (const [i, name] of ['阿禾', '小林', '老赵', '阿晓', '大秦'].entries()) {
    await page.locator('[data-testid^="roster-name-"]').nth(i).fill(name)
  }
  await page.getByRole('radio', { name: '本章第一视角主人公' }).first().check()
  await page.getByTestId('roster-confirm').click()
  await expect(page.getByTestId('roster-confirmed')).toBeVisible()
  await page.getByTestId('preview-step-range').click()
  await page.getByTestId('windows-clear').click()
  await page.getByTestId('window-picker').getByRole('checkbox').first().check()
  await page.getByTestId('preview-step-run').click()
  await page.getByTestId('preview-run').click()
  await expect(page.getByTestId('job-state')).toHaveText('已完成', { timeout: 30_000 })

  for (const mode of ['single', 'batch']) {
    await page.getByTestId('preview-step-config').click()
    await page.getByTestId(`processing-mode-${mode}`).check()
    await page.getByTestId('preview-step-results').click()
    const content = page.getByTestId('preview-document')
    await expect(content).toContainText('第300封信')
    await expectPageFits(page)
    await content.evaluate(el => { el.scrollTop = el.scrollHeight })
    expect(await content.evaluate(el => el.scrollTop)).toBeGreaterThan(0)
    await expect(page.getByRole('navigation', { name: '处理步骤' })).toBeInViewport({ ratio: 1 })
    await page.screenshot({ path: testInfo.outputPath(`${mode}-result-1920.png`) })
  }

  await page.setViewportSize({ width: 360, height: 800 })
  await expect(page.getByTestId('preview-document')).toContainText('第300封信')
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(361)
})
