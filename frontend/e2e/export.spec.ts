import { expect, test, type Download, type Page } from '@playwright/test'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

/**
 * 导出对话框、后端样张与下载闭环。
 *
 * 真实后端 + 真实 Chromium + 隔离数据目录；模型侧只用显式启用的确定性 FakeProvider（不访问网络）。
 * 导出本身不调用模型；这里验证「用户不写脚本就能选择、预览、生成、下载」以及成品的离线可读性。
 */
const fixturesDir = path.join(path.dirname(fileURLToPath(import.meta.url)), 'fixtures')
const fixture = (name: string) => path.join(fixturesDir, name)

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
  await page.locator('.ndr-chapter').first().click()
}

/** 只处理第一章，保证后续章节保持「未处理」。 */
async function processFirstChapter(page: Page, profileLabel: string) {
  await page.getByRole('link', { name: '预览与处理' }).click()
  await page.getByTestId('preview-profile').selectOption({ label: profileLabel })
  await page.getByTestId('preview-run').click()
  await expect(page.getByTestId('job-state')).toHaveText('COMPLETED', { timeout: 30_000 })
}

async function openExportDialog(page: Page) {
  await page.getByTestId('open-export').click()
  await expect(page.getByTestId('export-dialog')).toBeVisible()
  await expect(page.getByTestId('export-preview')).toBeVisible()
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

test.describe('导出对话框与下载闭环', () => {
  test('TXT → HTML：样张、校验报告、中文文件名与断网可读', async ({ page }) => {
    const profileName = '导出样张 HTML 提供方'
    await createProfile(page, profileName, 'fake-model-html')
    await importFile(page, 'sample-utf8.txt')
    await openReader(page, 'sample-utf8')
    await processFirstChapter(page, `${profileName} · fake-provider · fake-model-html`)
    await page.getByRole('link', { name: '去阅读' }).click()
    await page.locator('.ndr-chapter').first().click()

    await openExportDialog(page)
    // 重读：显示全部有效投影（含第一章已确认的编号）
    await page.getByTestId('export-policy-reread').check()
    await expect(page.getByTestId('export-coverage')).toContainText('已接受')
    const sample = page.getByTestId('export-sample')
    await expect(sample).toHaveAttribute('sandbox', '')
    await expect(page.frameLocator('[data-testid=export-sample]').getByText('「雨停了。」')).toBeVisible()

    await generate(page, 'html')
    await expect(page.getByTestId('export-check-mimetype_value')).toHaveCount(0) // HTML 没有 zip 检查
    await expect(page.getByTestId('export-check-text_consistency')).toContainText('✓')
    await expect(page.getByTestId('export-standard')).toContainText('NOT_APPLICABLE')

    const download = await downloadArtifact(page)
    const suggested = download.suggestedFilename()
    expect(suggested).toContain('标注版')
    expect(suggested.endsWith('.html')).toBe(true)

    const file = await download.path()
    expect(file).toBeTruthy()
    const body = fs.readFileSync(file as string).toString('utf-8')
    expect(body).toContain('「雨停了。」')
    expect(body).toContain('〔S1〕') // 颜色被覆盖时靠编号辨认
    expect(body).not.toContain('http://')
    expect(body).not.toContain('https://')
    expect(body).not.toContain('<script')
  })

  test('EPUB → EPUB：结构可读、资源随包、重复下载一致', async ({ page }) => {
    const profileName = '导出样张 EPUB 提供方'
    await createProfile(page, profileName, 'fake-model-epub')
    await importFile(page, 'original-sample.epub')
    await openReader(page, '原创 ruby/插图样例')
    await processFirstChapter(page, `${profileName} · fake-provider · fake-model-epub`)
    await page.getByRole('link', { name: '去阅读' }).click()
    await page.locator('.ndr-chapter').first().click()

    await openExportDialog(page)
    await page.getByTestId('export-policy-reread').check()
    await generate(page, 'epub')
    await expect(page.getByTestId('export-check-mimetype_first')).toContainText('✓')
    await expect(page.getByTestId('export-check-mimetype_stored')).toContainText('✓')
    await expect(page.getByTestId('export-check-resource_closure')).toContainText('✓')
    // 本机没有 EPUBCheck：状态如实显示 NOT_RUN，而不是 PASS
    await expect(page.getByTestId('export-standard')).toContainText('NOT_RUN')

    const first = await downloadArtifact(page)
    expect(first.suggestedFilename()).toContain('标注版')
    const firstBytes = fs.readFileSync((await first.path()) as string)
    expect(firstBytes.subarray(0, 2).toString('binary')).toBe('PK')
    // mimetype 必须是第一项：zip 头部附近应出现它的名字
    expect(firstBytes.subarray(0, 120).toString('binary')).toContain('mimetype')

    // 重复下载不会重新生成：文件名相同、大小一致
    const again = await downloadArtifact(page)
    const againBytes = fs.readFileSync((await again.path()) as string)
    expect(again.suggestedFilename()).toBe(first.suggestedFilename())
    expect(againBytes.length).toBe(firstBytes.length)
  })

  test('指定章节导出：文件名标「节选」，只带选中章节', async ({ page }) => {
    await openReader(page, 'sample-utf8')
    await openExportDialog(page)
    await page.getByTestId('export-scope-chapters').check()
    await expect(page.getByTestId('export-scope-empty')).toBeVisible()
    await page.getByTestId('export-chapter-0').check()

    await generate(page, 'html')
    const download = await downloadArtifact(page)
    expect(download.suggestedFilename()).toContain('节选')
    const body = fs.readFileSync((await download.path()) as string).toString('utf-8')
    expect(body).toContain('「雨停了。」')
    expect(body).not.toContain('「我叫小満。」') // 第二章没有导出
  })

  test('未处理章节：覆盖统计与警告如实展示，导出保持原样', async ({ page }) => {
    // 专用夹具 + 只处理第一章，保证第二章确实没有标注
    const profileName = '导出未处理提供方'
    await createProfile(page, profileName, 'fake-model-unprocessed')
    await importFile(page, 'sample-export.txt')
    await openReader(page, 'sample-export')
    await processFirstChapter(page, `${profileName} · fake-provider · fake-model-unprocessed`)
    await page.getByRole('link', { name: '去阅读' }).click()
    await page.locator('.ndr-chapter').first().click()
    await openExportDialog(page)
    await page.getByTestId('export-policy-reread').check()
    await expect(page.getByTestId('export-coverage')).toContainText('未处理 1')
    await expect(page.getByTestId('export-warnings')).toContainText('还没有标注')

    await generate(page, 'html')
    const download = await downloadArtifact(page)
    const body = fs.readFileSync((await download.path()) as string).toString('utf-8')
    // 未处理的对白仍然在正文里，但没有颜色 span（保持原样）
    expect(body).toContain('「现在轮到我了。」')
    const unprocessedParagraph = body
      .split('\n')
      .find((line) => line.includes('「现在轮到我了。」'))
    expect(unprocessedParagraph ?? '').not.toContain('speaker-')
  })

  test('并发纠正：重新打开导出会提示快照已过期', async ({ page }) => {
    // 用专用夹具，避免影响其它用例依赖的书籍（导出只读快照，但更正会改标注）
    const profileName = '导出并发提供方'
    await createProfile(page, profileName, 'fake-model-concurrent')
    await importFile(page, 'sample-export.txt')
    await openReader(page, 'sample-export')
    await processFirstChapter(page, `${profileName} · fake-provider · fake-model-concurrent`)
    await page.getByRole('link', { name: '去阅读' }).click()
    await page.locator('.ndr-chapter').first().click()

    await openExportDialog(page)
    await generate(page, 'html')
    await expect(page.getByTestId('export-download')).toBeVisible()
    await page.getByTestId('export-close').click()

    // 在抽屉里做一次人工更正（不改动导出文件，但会改变后续快照）
    await page.getByTestId('annotation-span').first().click()
    await page.getByTestId('correction-action').selectOption('mark_unknown')
    await page.getByTestId('correction-submit').click()
    await expect(page.getByTestId('drawer-notice')).toContainText('已确认 1 条')
    await page.getByTestId('drawer-close').click()

    await openExportDialog(page)
    await expect(page.getByTestId('export-stale-snapshot')).toContainText('旧快照')
  })
})