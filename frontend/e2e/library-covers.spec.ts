import { expect, test } from '@playwright/test'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const fixture = path.join(path.dirname(fileURLToPath(import.meta.url)), 'fixtures', 'original-cover.epub')
const title = '雾港来信：沿着海岸寻找春天的旅程'

test('EPUB 封面随导入显示，刷新保留，图片失败回到占位并可恢复', async ({ page }, testInfo) => {
  await page.goto('/library')
  await page.setInputFiles('[data-testid=import-file-input]', fixture)
  await page.getByTestId('import-submit').click()
  await expect(page.getByTestId('import-result')).toContainText('导入完成：EPUB')

  const card = page.getByTestId('book-card').filter({ has: page.getByRole('heading', { name: title, exact: true }) })
  const cover = card.locator('.ndr-book-cover img')
  await expect(cover).toBeVisible()
  await expect.poll(() => cover.evaluate((image: HTMLImageElement) => image.naturalWidth)).toBeGreaterThan(0)
  const source = await cover.getAttribute('src')
  expect(source).toMatch(/\/api\/books\/[^/]+\/resources\//)
  await page.reload()
  await expect(cover).toHaveAttribute('src', source!)
  await expect.poll(() => cover.evaluate((image: HTMLImageElement) => image.naturalWidth)).toBeGreaterThan(0)

  for (const colorScheme of ['light', 'dark'] as const) {
    await page.emulateMedia({ colorScheme, reducedMotion: 'reduce' })
    for (const width of [1440, 768, 390, 320]) {
      await page.setViewportSize({ width, height: 900 })
      await card.scrollIntoViewIfNeeded()
      await expect(cover).toBeVisible()
      await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(1)
      const coverBox = (await cover.boundingBox())!
      const headingBox = (await card.getByRole('heading').boundingBox())!
      expect(headingBox.x).toBeGreaterThanOrEqual(coverBox.x + coverBox.width)
      await page.screenshot({ path: testInfo.outputPath(`library-covers-${colorScheme}-${width}.png`), fullPage: true })
    }
  }

  const imageRoute = new URL(source!, page.url()).href
  await page.route(imageRoute, route => route.fulfill({ status: 404, body: '' }))
  await page.reload()
  await expect(card.locator('.ndr-book-cover svg')).toBeVisible()
  await expect(cover).toHaveCount(0)
  await expect(card.getByRole('link', { name: '开始阅读' })).toBeEnabled()
  await page.unroute(imageRoute)
  await page.reload()
  await expect(cover).toBeVisible()
  await expect.poll(() => cover.evaluate((image: HTMLImageElement) => image.naturalWidth)).toBeGreaterThan(0)
  await card.getByRole('link', { name: '开始阅读' }).click()
  await expect(page.getByTestId('document-renderer')).toContainText('港口的第一封信，在春天到来之前寄出。')
})
