import { expect, test, type Page } from '@playwright/test'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

/**
 * T04：不填写任何 API 配置，也能导入 TXT/EPUB 并阅读原文。
 * 使用真实后端（隔离数据目录 + 自动迁移）与真实浏览器；不涉及任何模型调用。
 */
const fixturesDir = path.join(path.dirname(fileURLToPath(import.meta.url)), 'fixtures')
const fixture = (name: string) => path.join(fixturesDir, name)

async function openBook(page: Page, title: string) {
  await page
    .locator('[data-testid=book-card]', { hasText: title })
    .getByRole('link', { name: '开始阅读' })
    .click()
}

async function importFile(page: Page, fileName: string, encoding?: string) {
  await page.setInputFiles('[data-testid=import-file-input]', fixture(fileName))
  if (encoding) {
    await page.selectOption('[data-testid=import-encoding]', encoding)
  }
  await page.click('[data-testid=import-submit]')
}

test.describe('导入与阅读', () => {
  test('导入 UTF-8 TXT 并阅读原文、保存阅读位置', async ({ page }) => {
    await page.goto('/library')
    await importFile(page, 'sample-utf8.txt')

    const result = page.getByTestId('import-result')
    await expect(result).toBeVisible()
    await expect(result).toContainText('导入完成：TXT')
    await expect(result).toContainText('码点')

    await openBook(page, 'sample-utf8')
    await expect(page).toHaveURL(/\/books\/[^/]+\/read/)
    await expect(page.locator('.ndr-chapter-heading')).toHaveText('序章 雨夜')
    await expect(page.getByText('「雨停了。」少女合上伞。')).toBeVisible()
    // 节点带有码点定位属性，供后续标注层使用。
    const node = page.locator('[data-node-id]').nth(1)
    await expect(node).toHaveAttribute('data-start-cp', /\d+/)

    // 切换到第二章：位置应被保存，刷新后仍停在第二章。
    await page.getByRole('button', { name: /第二章 名字/ }).click()
    await expect(page.getByText('𠮷野家的猫🐈跳上窗台。')).toBeVisible()
    await page.reload()
    const active = page.locator('button[aria-current="true"]')
    await expect(active).toContainText('第二章 名字')
  })

  test('导入 EPUB：按 spine 阅读、ruby 注音与插图都能显示', async ({ page }) => {
    await page.goto('/library')
    await importFile(page, 'original-sample.epub')

    const result = page.getByTestId('import-result')
    await expect(result).toContainText('导入完成：EPUB')

    await openBook(page, '原创 ruby/插图样例')
    await expect(page.locator('.ndr-chapter-heading')).toHaveText('第一章 注音')

    // ruby：基底文字在正文里，注音在 rt 里
    const ruby = page.locator('ruby').first()
    await expect(ruby).toContainText('漢')
    await expect(page.locator('rt').first()).toHaveText('かん')

    // 插图通过受控资源端点加载，并且真的解码成功
    const image = page.locator('img.ndr-image').first()
    await expect(image).toHaveAttribute('src', /\/resources\/r\d+/)
    await expect
      .poll(async () => image.evaluate((element) => (element as HTMLImageElement).naturalWidth))
      .toBeGreaterThan(0)
  })

  test('编码选错时给出候选与预演，换编码后可恢复', async ({ page }) => {
    await page.goto('/library')
    await importFile(page, 'sample-gb18030.txt', 'utf-8')

    const error = page.getByTestId('import-error')
    await expect(error).toBeVisible()
    await expect(error).toContainText('VALIDATION_ERROR')
    await expect(page.getByTestId('import-preview')).toBeVisible()
    await expect(page.getByTestId('retry-gb18030')).toBeVisible()

    await page.getByTestId('retry-gb18030').click()
    await expect(page.getByTestId('import-result')).toContainText('导入完成：TXT')
    await expect(page.getByTestId('import-result')).toContainText('编码 gb18030')
  })

  test('不支持的格式给出可理解的错误', async ({ page }) => {
    await page.goto('/library')
    await page.setInputFiles('[data-testid=import-file-input]', {
      name: 'notes.md',
      mimeType: 'text/markdown',
      buffer: Buffer.from('# 不是小说', 'utf-8'),
    })
    await page.click('[data-testid=import-submit]')

    await expect(page.getByTestId('import-error')).toContainText('只支持 .txt 与 .epub 文件')
  })
})