import { expect, test } from '@playwright/test'

/** Offline fixture in the isolated E2E library; never starts model processing. */
test('Nova阅读导航调整宽度、收起和窄屏切换保留正文', async ({ page, request }, testInfo) => {
  const imported = await request.post('/api/books/import', { multipart: { file: {
    name: 'nova-layout-original.txt', mimeType: 'text/plain',
    buffer: Buffer.from('第一章 窗边\n「雨停了，我们去看看院子。」\n' + '窗沿上留着几滴清水，树叶慢慢舒展开来。\n'.repeat(24) + '第二章 晨光\n「带上那把伞吧。」\n门口的脚步声渐渐远去。'),
  } } })
  expect(imported.ok()).toBe(true)
  const { book_id: bookId } = (await imported.json()).data
  await page.setViewportSize({ width: 1280, height: 900 })
  await page.goto(`/books/${bookId}/read`)
  const renderedDocument = page.getByTestId('document-renderer')
  await expect(renderedDocument).toBeVisible()
  await renderedDocument.evaluate(element => element.setAttribute('data-layout-stable', 'true'))
  const main = page.locator('.ndr-reader-document-panel')
  const navigation = page.locator('.ndr-reader-navigation-panel')
  const separator = page.getByRole('separator', { name: '调整阅读导航宽度' })
  const openWidth = (await main.boundingBox())!.width
  await page.getByRole('button', { name: '隐藏阅读导航' }).click()
  await expect(navigation).toBeHidden()
  await expect.poll(async () => (await main.boundingBox())!.width).toBeGreaterThan(openWidth + 100)
  await page.getByRole('button', { name: '显示阅读导航' }).click()
  await expect(navigation).toBeVisible()
  const beforeDrag = (await navigation.boundingBox())!.width
  const handle = (await separator.boundingBox())!
  const x = handle.x + handle.width / 2
  const y = handle.y + Math.min(30, handle.height / 2)
  await page.mouse.move(x, y)
  await page.mouse.down()
  await page.mouse.move(x + 70, y, { steps: 8 })
  await page.mouse.up()
  await expect.poll(async () => (await navigation.boundingBox())!.width).toBeGreaterThan(beforeDrag + 20)
  const beforeKeyboard = (await navigation.boundingBox())!.width
  await separator.focus()
  await separator.press('ArrowLeft')
  await expect.poll(async () => (await navigation.boundingBox())!.width).toBeLessThan(beforeKeyboard - 10)
  for (const width of [360, 800, 1280]) {
    await page.setViewportSize({ width, height: 900 })
    await expect(renderedDocument).toHaveAttribute('data-layout-stable', 'true')
    await expect(renderedDocument).toBeVisible()
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(1)
    if (width <= 900) await expect(separator).toBeHidden()
    else await expect(separator).toBeVisible()
    await page.screenshot({ path: testInfo.outputPath(`nova-reader-${width}.png`), fullPage: true })
  }
})
