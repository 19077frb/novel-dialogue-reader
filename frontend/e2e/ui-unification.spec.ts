import { expect, test } from '@playwright/test'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import { confirmChapterRoster } from './roster'

// Actual local frontend/backend, isolated test library, offline FakeProvider only.
const fixture = path.join(path.dirname(fileURLToPath(import.meta.url)), 'fixtures/sample-utf8.txt')
for (const layout of [
  { name: 'desktop-light', width: 1280, height: 900, colorScheme: 'light' as const },
  { name: 'desktop-dark', width: 1280, height: 900, colorScheme: 'dark' as const },
  { name: 'mobile-dark', width: 360, height: 740, colorScheme: 'dark' as const },
]) {
  test(`unified headers, navigation, controls and screenshots: ${layout.name}`, async ({ page }, testInfo) => {
    test.setTimeout(90_000)
    await page.setViewportSize({ width: layout.width, height: layout.height })
    await page.emulateMedia({ colorScheme: layout.colorScheme })
    await page.goto('/settings/models')
    await page.getByTestId('profile-name').fill(`UI ${layout.name}`)
    await page.getByTestId('profile-protocol').selectOption('fake-provider')
    await page.getByTestId('profile-base-url').fill('http://127.0.0.1:1')
    await page.getByTestId('profile-model').fill('fake-model-ui')
    await page.getByTestId('profile-save').click()
    await expect(page.getByTestId('profile-card').filter({ hasText: `UI ${layout.name}` })).toBeVisible()
    await page.goto('/library')
    await page.getByTestId('import-file-input').setInputFiles(fixture)
    await page.getByTestId('import-submit').click()
    await expect(page.getByTestId('import-result')).toContainText('导入完成')
    const href = await page.getByTestId('book-card').filter({ hasText: 'sample-utf8' })
      .first().getByRole('link', { name: '开始阅读' }).getAttribute('href')
    const bookId = /\/books\/([^/]+)\/read/.exec(href ?? '')?.[1]
    expect(bookId).toBeTruthy()
    await page.goto(`/books/${bookId}/preview`)
    await confirmChapterRoster(page)

    const pages = [
      { name: 'library', route: '/library', title: '书架', nav: [] },
      { name: 'reader', route: `/books/${bookId}/read`, title: '阅读', nav: ['预览与处理', '全书人物', '待确认队列', '导出', '返回书架'] },
      { name: 'single', route: `/books/${bookId}/preview`, title: '预览与按章处理', nav: ['去阅读', '全书人物', '待确认队列', '导出', '返回书架'] },
      { name: 'batch', route: `/books/${bookId}/preview`, title: '预览与按章处理', nav: ['去阅读', '全书人物', '待确认队列', '导出', '返回书架'] },
      { name: 'characters', route: `/books/${bookId}/characters`, title: '全书人物', nav: ['去阅读', '预览与处理', '待确认队列', '返回书架'] },
      { name: 'review', route: `/books/${bookId}/review`, title: '待确认', nav: ['去阅读', '预览与处理', '全书人物', '返回书架'] },
      { name: 'models', route: '/settings/models', title: '模型配置', nav: [] },
    ]
    for (const item of pages) {
      await page.goto(item.route)
      const header = page.locator('.ndr-page > .ndr-page-header').first()
      await expect(header).toBeVisible()
      // Reader heading is the book title, not a separate "阅读" title.
      if (item.name !== 'reader') await expect(header.locator('h2')).toContainText(item.title)
      await expect(header.locator('.hint')).not.toBeEmpty()
      if (item.name === 'library') {
        const card = page.getByTestId('book-card').first()
        const read = card.getByRole('link', { name: '开始阅读' })
        const remove = card.getByRole('button', { name: '删除', exact: true })
        await expect(read).toHaveClass('ndr-button ndr-primary')
        const readStyle = await read.evaluate(el => ({ radius: getComputedStyle(el).borderRadius, height: el.getBoundingClientRect().height }))
        const deleteStyle = await remove.evaluate(el => ({ radius: getComputedStyle(el).borderRadius, height: el.getBoundingClientRect().height }))
        expect(readStyle).toEqual(deleteStyle)
      }
      if (item.name === 'single') {
        await expect(page.getByTestId('preview-process')).toHaveClass('ndr-primary')
        await expect(page.getByTestId('preview-run')).not.toHaveClass(/ndr-primary/)
        await expect(page.getByTestId('preview-estimate')).not.toHaveClass(/ndr-primary/)
      }
      if (item.name === 'batch') {
        await page.getByTestId('processing-mode-batch').check()
        await expect(page.getByTestId('batch-processor').locator('.ndr-step-badge')).toHaveText('1')
      }
      if (item.nav.length) {
        const actual = await header.locator('.ndr-book-nav a, .ndr-book-nav button').allTextContents()
        expect(actual.map(text => /^待确认/.test(text.trim()) ? '待确认队列' : text.trim())).toEqual(item.nav)
      }
      await expect(page.locator('.ndr-preview-nav')).toHaveCount(0)
      const dimensions = await page.evaluate(() => ({ width: document.documentElement.clientWidth, scroll: document.documentElement.scrollWidth }))
      expect(dimensions.scroll, `${item.name} must not overflow`).toBeLessThanOrEqual(dimensions.width + 1)
      const screenshot = testInfo.outputPath(`${item.name}.png`)
      await page.screenshot({ path: screenshot, fullPage: true })
      await testInfo.attach(item.name, { path: screenshot, contentType: 'image/png' })
    }
  })
}
