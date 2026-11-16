import { expect, test, type Page } from '@playwright/test'

async function expectFocusInside(page: Page) {
  expect(await page.getByRole('dialog', { name: '对白确认' }).evaluate(
    dialog => dialog.contains(document.activeElement),
  )).toBe(true)
}

test('对白确认隔离背景、保留滚动与焦点，框外点击不穿透，两入口共用', async ({ page }) => {
  // Original text generated only for this isolated database; no model/profile needed.
  const text = '第一章 雨后\n\n' + Array.from({ length: 80 }, (_, i) =>
    `小禾收起第${i + 1}张纸船，沿着河岸走了一段路。风吹动新叶，远处的钟声渐渐散去。\n「这一张留给明天。」小禾说。\n`,
  ).join('\n') + '\n第二章 晴天\n\n「今天的河水很清。」阿宁说。'
  await page.goto('/library')
  await page.getByTestId('import-file-input').setInputFiles({
    name: '原创模态交互.txt', mimeType: 'text/plain', buffer: Buffer.from(text),
  })
  await page.getByTestId('import-submit').click()
  await expect(page.getByTestId('import-result')).toContainText('导入完成：TXT')
  await page.locator('[data-testid=book-card]', { hasText: '原创模态交互' })
    .getByRole('link', { name: '开始阅读' }).click()
  const quote = page.getByTestId('candidate-quote').first()
  await expect(quote).toBeVisible()
  const reader = page.locator('.ndr-reader-content')
  await page.evaluate(() => document.addEventListener('click', () => {
    document.activeElement?.setAttribute('data-modal-original-focus', 'true')
  }, { capture: true, once: true }))
  await quote.click()
  const dialog = page.getByRole('dialog', { name: '对白确认' })
  await expect(dialog).toBeVisible()
  await expect(dialog).toHaveJSProperty('open', true)
  await expectFocusInside(page)
  // Both tab directions stay inside even across several full cycles.
  for (const key of ['Tab', 'Shift+Tab']) {
    for (let i = 0; i < 25; i++) {
      await page.keyboard.press(key)
      await expectFocusInside(page)
    }
  }
  await page.getByTestId('drawer-close').focus()
  const originalScroll = await reader.evaluate(el => el.scrollTop)
  const bodyScroll = await page.evaluate(() => window.scrollY)
  const behind = await reader.boundingBox()
  expect(behind).not.toBeNull()
  const outside = { x: behind!.x + 10, y: behind!.y + 30 }
  await page.mouse.move(outside.x, outside.y)
  await page.mouse.wheel(0, 600)
  await page.waitForTimeout(150)
  expect(await reader.evaluate(el => el.scrollTop)).toBe(originalScroll)
  expect(await page.evaluate(() => window.scrollY)).toBe(bodyScroll)
  // The inner panel retains its own scroll, without chaining to the background.
  const panel = dialog.locator('.ndr-drawer')
  const panelBox = await panel.boundingBox()
  await page.mouse.move(panelBox!.x + 50, panelBox!.y + 200)
  await page.mouse.wheel(0, 700)
  await expect.poll(() => panel.evaluate(el => el.scrollTop)).toBeGreaterThan(0)
  expect(await reader.evaluate(el => el.scrollTop)).toBe(originalScroll)
  await panel.evaluate(el => { el.scrollTop = 0 })
  await page.getByTestId('drawer-quote').click()
  await expect(dialog).toBeVisible()
  // Dragging out from content must not be mistaken for a backdrop click.
  await page.mouse.move(panelBox!.x + 70, panelBox!.y + 80)
  await page.mouse.down()
  await page.mouse.move(outside.x, outside.y)
  await page.mouse.up()
  await expect(dialog).toBeVisible()
  // Count any actual background click, independent of business behavior.
  await page.evaluate(() => {
    document.querySelector('.ndr-reader-workspace')!.addEventListener('click', () => {
      document.body.dataset.backgroundClicks = String(Number(document.body.dataset.backgroundClicks ?? 0) + 1)
    })
  })
  await page.mouse.click(outside.x, outside.y)
  await expect(dialog).toHaveCount(0)
  expect(await page.locator('body').getAttribute('data-background-clicks')).toBeNull()
  expect(await reader.evaluate(el => el.scrollTop)).toBe(originalScroll)
  expect(await page.evaluate(() => window.scrollY)).toBe(bodyScroll)
  expect(await page.evaluate(() => document.body.style.overflow)).toBe('')
  // Restore the actual pre-open element (plain text quote spans are not focusable).
  await expect(page.locator('[data-modal-original-focus]')).toBeFocused()
  await page.mouse.move(outside.x, outside.y)
  await page.mouse.wheel(0, 400)
  await expect.poll(() => reader.evaluate(el => el.scrollTop)).toBeGreaterThan(originalScroll)
  await reader.evaluate(el => { el.scrollTop = 0 })
  await quote.click()
  await page.keyboard.press('Escape')
  await expect(dialog).toHaveCount(0)
  await expect(page.locator('[data-modal-original-focus]')).toBeFocused()
  // Flagging is local and free; the same modal is used by the review queue.
  await quote.click()
  await page.getByTestId('drawer-flag').click()
  await expect(page.getByTestId('drawer-notice')).toContainText('已加入待确认')
  await page.getByTestId('drawer-close').click()
  await page.getByTestId('reader-review-link').click()
  const reviewOpener = page.getByTestId('review-open').first()
  await reviewOpener.click()
  await expect(dialog).toBeVisible()
  await expectFocusInside(page)
  await page.keyboard.press('Escape')
  await expect(dialog).toHaveCount(0)
  await expect(reviewOpener).toBeFocused()
})
