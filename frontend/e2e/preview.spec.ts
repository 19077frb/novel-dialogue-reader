import { expect, test, type Page } from '@playwright/test'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import { confirmChapterRoster } from './roster'

/**
 * 真实效果预览与按章处理。
 *
 * 使用真实后端 + 真实浏览器 + 隔离数据目录；模型侧用**显式启用**的 FakeProvider
 * （playwright.config.ts 里 NDR_ALLOW_FAKE_PROVIDER=1 且 NDR_FAKE_PROVIDER_LABELS=deterministic），
 * 不访问任何网络。真实提供方联调与效果评测仍属未完成。
 */
const fixturesDir = path.join(path.dirname(fileURLToPath(import.meta.url)), 'fixtures')
const fixture = (name: string) => path.join(fixturesDir, name)

async function createFakeProfile(page: Page, name: string) {
  await page.goto('/settings/models')
  await page.getByTestId('profile-name').fill(name)
  await page.selectOption('[data-testid=profile-protocol]', 'fake-provider')
  await page.getByTestId('profile-base-url').fill('http://127.0.0.1:1')
  await page.getByTestId('profile-model').fill('fake-model')
  await page.getByTestId('profile-save').click()
  await expect(page.getByTestId('profile-card').filter({ hasText: name })).toBeVisible()
}

async function importFile(page: Page, fileName: string) {
  await page.goto('/library')
  await page.setInputFiles('[data-testid=import-file-input]', fixture(fileName))
  await page.click('[data-testid=import-submit]')
}

async function openPreview(page: Page, title: string) {
  await page.goto('/library')
  await page
    .locator('[data-testid=book-card]', { hasText: title })
    .getByRole('link', { name: '开始阅读' })
    .click()
  await page.getByRole('link', { name: '预览与处理' }).click()
  await expect(page).toHaveURL(/\/books\/[^/]+\/preview/)
}

test.describe('预览与按章处理', () => {
  test('TXT：估算 → 试运行着色 → 视图切换零调用 → 正式处理命中缓存', async ({ page }) => {
    const profileName = 'E2E 预览提供方 TXT'
    await createFakeProfile(page, profileName)
    await importFile(page, 'sample-utf8.txt')
    await expect(page.getByTestId('import-result')).toContainText('导入完成：TXT')
    await openPreview(page, 'sample-utf8')

    // 默认选中第一章（范围来自目录的真实起止码点）
    await expect(page.getByTestId('range-summary')).toContainText('本次范围：0 – ')
    await page
      .getByTestId('preview-profile')
      .selectOption({ label: `${profileName} · fake-provider · fake-model` })
    await confirmChapterRoster(page)

    // 本地估算：不调用模型
    await page.getByTestId('preview-estimate').click()
    await expect(page.getByTestId('estimate-summary')).toBeVisible()
    const windows = Number(await page.getByTestId('estimate-windows').innerText())
    const targets = Number(await page.getByTestId('estimate-targets').innerText())
    expect(windows).toBeGreaterThan(0)
    expect(targets).toBeGreaterThan(0)

    // 试运行：真实任务驱动的结果
    await page.getByTestId('preview-run').click()
    await expect(page.getByTestId('job-state')).toHaveText('COMPLETED', { timeout: 30_000 })
    expect(Number(await page.getByTestId('job-calls').innerText())).toBeGreaterThan(0)
    await expect(page.getByTestId('preview-notice')).toContainText('标注投影')

    // TXT 能着色，并且编号是真实文本节点
    const span = page.getByTestId('annotation-span').first()
    await expect(span).toBeAttached()
    await expect(span).toHaveAttribute('data-status', 'ACCEPTED')
    await expect(page.getByTestId('annotation-label').first()).toHaveText('〔S1〕')
    await expect(page.getByTestId('speaker-legend')).toContainText('S1')
    const color = await span.evaluate((element) => getComputedStyle(element).color)
    expect(color).not.toBe('')

    // 原文/标注切换：只改显示，原文不变，也不产生任何模型调用
    await page.getByTestId('view-original').check()
    await expect(page.getByTestId('annotation-span')).toHaveCount(0)
    await expect(page.getByTestId('preview-document')).toContainText('「雨停了。」')
    await page.getByTestId('view-annotated').check()
    await expect(page.getByTestId('annotation-span').first()).toBeAttached()

    // 正式处理：同范围同输入 → 命中缓存，不再发送任何调用
    await page.getByTestId('preview-process').click()
    await expect(page.getByTestId('job-calls')).toHaveText('0', { timeout: 30_000 })
    await expect(page.getByTestId('job-state')).toHaveText('COMPLETED')
    expect(Number(await page.getByTestId('job-cached-windows').innerText())).toBe(windows)
    // cache 命中后标注仍然来自同一份投影
    await expect(page.getByTestId('annotation-span').first()).toBeAttached()
  })

  test('EPUB：按 spine 的预览同样能着色', async ({ page }) => {
    const profileName = 'E2E 预览提供方 EPUB'
    await createFakeProfile(page, profileName)
    await importFile(page, 'original-sample.epub')
    await expect(page.getByTestId('import-result')).toContainText('导入完成：EPUB')
    await openPreview(page, '原创 ruby/插图样例')
    await page
      .getByTestId('preview-profile')
      .selectOption({ label: `${profileName} · fake-provider · fake-model` })
    await confirmChapterRoster(page)

    await page.getByTestId('preview-run').click()
    await expect(page.getByTestId('job-state')).toHaveText('COMPLETED', { timeout: 30_000 })

    await expect(page.getByTestId('annotation-span').first()).toBeAttached()
    await expect(page.getByTestId('annotation-label').first()).toHaveText('〔S1〕')
    // ruby 注音仍在 <rt> 里，没有被当正文重复输出
    await expect(page.locator('rt').first()).toHaveText('かん')
  })

  test('按章处理：切换章节后范围与着色都跟着换', async ({ page }) => {
    const profileName = 'E2E 预览提供方 GB18030'
    await createFakeProfile(page, profileName)
    await importFile(page, 'sample-gb18030.txt')
    await expect(page.getByTestId('import-result')).toContainText('导入完成：TXT')
    await openPreview(page, 'sample-gb18030')
    await page
      .getByTestId('preview-profile')
      .selectOption({ label: `${profileName} · fake-provider · fake-model` })

    // 核对范围：切到第二章，范围随之变化（来自目录的真实起止码点）
    const chapterOption = page.getByTestId('range-chapter').locator('option').nth(2)
    const chapterLabel = (await chapterOption.innerText()).trim()
    const match = chapterLabel.match(/（(\d+) – (\d+)）/)
    expect(match).not.toBeNull()
    const [, startCp, endCp] = match as RegExpMatchArray
    await page.getByTestId('range-chapter').selectOption({ index: 2 })
    await expect(page.getByTestId('range-summary')).toContainText(`${startCp} – ${endCp}`)

    // 换章节后人物名单要重新确认，确认的是这一章的人物与主人公
    await confirmChapterRoster(page)
    await page.getByTestId('preview-run').click()
    await expect(page.getByTestId('job-state')).toHaveText('COMPLETED', { timeout: 30_000 })
    await expect(page.getByTestId('annotation-span').first()).toBeAttached()
    await expect(page.getByTestId('annotation-label').first()).toHaveText('〔S1〕')
  })
})