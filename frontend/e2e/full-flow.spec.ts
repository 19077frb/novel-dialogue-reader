import { expect, test, type Download, type Page } from '@playwright/test'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import { confirmChapterRoster } from './roster'

/**
 * 完整联调（TXT/EPUB 导入 → 处理 → EPUB/HTML 导出 → 下载件离线可读）。
 *
 * 真实后端 + 真实 Chromium + 隔离数据目录；模型侧只用显式启用的确定性 FakeProvider（不访问网络）。
 * 这里不重复导出测试对样张/校验细节的断言，而是把**整条链路**在同一个会话里跑通：
 * 导入 → 本地估算 → 试运行处理 → 导出两种格式 → 下载件真的能离线读。
 */
const fixturesDir = path.join(path.dirname(fileURLToPath(import.meta.url)), 'fixtures')
const fixture = (name: string) => path.join(fixturesDir, name)

const FULL_FLOW_TEXT = 'sample-full-flow.txt'

async function createProfile(page: Page, name: string, model = 'fake-model') {
  await page.goto('/settings/models')
  await page.getByTestId('profile-name').fill(name)
  await page.selectOption('[data-testid=profile-protocol]', 'fake-provider')
  await page.getByTestId('profile-base-url').fill('http://127.0.0.1:1')
  await page.getByTestId('profile-model').fill(model)
  await page.getByTestId('profile-save').click()
  await expect(page.getByTestId('profile-card').filter({ hasText: name })).toBeVisible()
}

async function importFile(page: Page, fileName: string) {
  await page.goto('/library')
  await page.setInputFiles('[data-testid=import-file-input]', fixture(fileName))
  await page.click('[data-testid=import-submit]')
  await expect(page.getByTestId('import-result')).toContainText('导入完成')
}

async function openReader(page: Page, title: string) {
  await page.goto('/library')
  await page
    .locator('[data-testid=book-card]', { hasText: title })
    .getByRole('link', { name: '开始阅读' })
    .click()
  await expect(page).toHaveURL(/\/books\/[^/]+\/read/)
}

async function processFirstChapter(page: Page, profileLabel: string) {
  await page.getByRole('link', { name: '预览与处理' }).click()
  await page.getByTestId('preview-profile').selectOption({ label: profileLabel })
  // 逐句归属前必须先确认本章人物与第一视角主人公。
  await confirmChapterRoster(page)
  await page.getByTestId('preview-estimate').click()
  await expect(page.getByTestId('estimate-summary')).toBeVisible()
  await page.getByTestId('preview-run').click()
  await expect(page.getByTestId('job-state')).toHaveText('已完成', { timeout: 30_000 })
}

async function generate(page: Page, format: 'epub' | 'html') {
  await page.getByTestId(`export-format-${format}`).check()
  await page.getByTestId('export-generate').click()
  await expect(page.getByTestId('export-state')).toHaveText('已完成', { timeout: 30_000 })
}

async function downloadArtifact(page: Page): Promise<Download> {
  const [download] = await Promise.all([
    page.waitForEvent('download'),
    page.getByTestId('export-download-link').click(),
  ])
  return download
}

test.describe('完整联调：导入 → 处理 → 导出', () => {
  test('TXT：估算/处理 → HTML 与 EPUB 都能生成、下载并离线可读', async ({ page }) => {
    const profileName = '全流程 TXT 提供方'
    await createProfile(page, profileName, 'fake-model-full-txt')
    await importFile(page, FULL_FLOW_TEXT)
    await openReader(page, 'sample-full-flow')
    await processFirstChapter(page, `${profileName} · fake-provider · fake-model-full-txt`)

    // 处理结果回到阅读页
    await page.getByRole('link', { name: '去阅读' }).click()
    await page.locator('.ndr-chapter').first().click()
    await expect(page.getByTestId('annotation-label').first()).toHaveText('〔确定性测试说话人〕')

    // 一次会话里导出两种格式
    await page.getByTestId('open-export').click()
    await expect(page.getByTestId('export-dialog')).toBeVisible()
    await page.getByTestId('export-policy-reread').check()

    await generate(page, 'html')
    const html = await downloadArtifact(page)
    expect(html.suggestedFilename().endsWith('.html')).toBe(true)
    const htmlBody = fs.readFileSync((await html.path()) as string).toString('utf-8')
    expect(htmlBody).toContain('「雨停了。」')
    expect(htmlBody).toContain('〔确定性测试说话人〕')
    expect(htmlBody).not.toContain('http://')
    expect(htmlBody).not.toContain('https://')
    expect(htmlBody).not.toContain('<script')

    await generate(page, 'epub')
    await expect(page.getByTestId('export-check-mimetype_first')).toContainText('✓')
    await expect(page.getByTestId('export-check-resource_closure')).toContainText('✓')
    // 没有 EPUBCheck jar：标准检查如实为 NOT_RUN，不伪装成通过
    await expect(page.getByTestId('export-standard')).toContainText('NOT_RUN')
    const epub = await downloadArtifact(page)
    expect(epub.suggestedFilename().endsWith('.epub')).toBe(true)
    const epubBytes = fs.readFileSync((await epub.path()) as string)
    expect(epubBytes.subarray(0, 2).toString('binary')).toBe('PK')
    expect(epubBytes.subarray(0, 120).toString('binary')).toContain('mimetype')

    // 导出对话框关闭后，阅读页仍可用（导出只读快照，不改标注）
    await page.getByTestId('export-close').click()
    await expect(page.getByTestId('annotation-label').first()).toHaveText('〔确定性测试说话人〕')
  })

  test('EPUB：导入 → 处理 → EPUB 与 HTML 都能生成并下载', async ({ page }) => {
    const profileName = '全流程 EPUB 提供方'
    await createProfile(page, profileName, 'fake-model-full-epub')
    await importFile(page, 'original-sample.epub')
    await openReader(page, '原创 ruby/插图样例')
    await processFirstChapter(page, `${profileName} · fake-provider · fake-model-full-epub`)

    await page.getByRole('link', { name: '去阅读' }).click()
    await page.locator('.ndr-chapter').first().click()
    await page.getByTestId('open-export').click()
    await page.getByTestId('export-policy-reread').check()

    await generate(page, 'epub')
    const epub = await downloadArtifact(page)
    const epubBytes = fs.readFileSync((await epub.path()) as string)
    expect(epubBytes.subarray(0, 2).toString('binary')).toBe('PK')
    expect(epubBytes.subarray(0, 120).toString('binary')).toContain('mimetype')

    await generate(page, 'html')
    const html = await downloadArtifact(page)
    const body = fs.readFileSync((await html.path()) as string).toString('utf-8')
    expect(body).not.toContain('<script')
    expect(body).not.toContain('http://')
    // ruby 注音不进正文：导出正文里不应出现当作正文的注音文字
    expect(body).not.toContain('<ruby>')
  })
})
