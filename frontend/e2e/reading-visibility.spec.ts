import { expect, test, type Page } from '@playwright/test'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

/**
 * 证据时点、初读/重读投影与最终定位。
 *
 * 真实后端 + 真实 Chromium + 隔离数据目录；模型侧只用显式启用的 FakeProvider（不访问网络）。
 * 用测试专用的 `params.script=split_then_merge`：先判成两个声音、再用末尾证据合并，
 * 因此初读第一章时不会提前同色；重读（或读到证据之后）才显示为同一个人。
 */
const fixturesDir = path.join(path.dirname(fileURLToPath(import.meta.url)), 'fixtures')
const fixture = (name: string) => path.join(fixturesDir, name)

async function createProfile(page: Page, input: { name: string; model?: string; params?: string }) {
  await page.goto('/settings/models')
  await page.getByTestId('profile-name').fill(input.name)
  await page.selectOption('[data-testid=profile-protocol]', 'fake-provider')
  await page.getByTestId('profile-base-url').fill('http://127.0.0.1:1')
  await page.getByTestId('profile-model').fill(input.model ?? 'fake-model')
  await page.getByTestId('profile-params').fill(input.params ?? '{}')
  // fake-provider 不需要密钥：保留默认的「仅本会话」即可（空密钥不会影响离线提供方）
  await page.getByTestId('profile-save').click()
  await expect(page.getByTestId('profile-card').filter({ hasText: input.name })).toBeVisible()
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

async function runWholeBook(page: Page, profileName: string, model = 'fake-model') {
  await page.getByRole('link', { name: '预览与处理' }).click()
  await expect(page).toHaveURL(/\/books\/[^/]+\/preview/)
  await page.getByTestId('preview-profile').selectOption({
    label: `${profileName} · fake-provider · ${model}`,
  })
  await page.getByTestId('range-chapter').selectOption('') // 整本
  await page.getByTestId('preview-run').click()
  await expect(page.getByTestId('job-state')).toHaveText('COMPLETED', { timeout: 30_000 })
}

test.describe('证据时点与定位回归', () => {
  test('：后文才揭示的合并不会在初读时提前同色，重读才合并', async ({ page }) => {
    const profileName = '合并可见性提供方'
    await createProfile(page, {
      name: profileName,
      model: 'fake-model-merge',
      params: JSON.stringify({ script: 'split_then_merge' }),
    })
    await importFile(page, 'sample-utf8.txt')
    await openReader(page, 'sample-utf8')
    await runWholeBook(page, profileName, 'fake-model-merge')

    // 回到阅读页第一章：两个声音必须是两种颜色/编号（合并证据在第二章末尾）
    await page.getByRole('link', { name: '去阅读' }).click()
    await page.locator('.ndr-chapter').first().click()
    await expect
      .poll(async () => page.getByTestId('annotation-label').allTextContents())
      .toEqual(['〔S1〕', '〔S2〕'])
    const colors = await page
      .getByTestId('annotation-span')
      .evaluateAll((nodes) => nodes.slice(0, 2).map((node) => getComputedStyle(node).color))
    expect(new Set(colors).size).toBe(2)
    // 初读提示会说明 Horizon 与后文证据
    await expect(page.getByTestId('reader-horizon')).toContainText('初读')
    await expect(page.getByTestId('reader-horizon')).toContainText('身份合并')

    // 切到重读：合并生效，两处同色同号
    await page.getByTestId('reader-reading-mode').selectOption('reread')
    await expect(page.getByTestId('reader-horizon')).toHaveCount(0)
    await expect
      .poll(async () => page.getByTestId('annotation-label').allTextContents())
      .toEqual(['〔S1〕', '〔S1〕'])
  })

  test('：emoji 与扩展汉字按码点着色，不切坏代理对', async ({ page }) => {
    const profileName = '码点定位提供方'
    await createProfile(page, { name: profileName, model: 'fake-model-astral' })
    await importFile(page, 'sample-astral.txt')
    await openReader(page, 'sample-astral')
    await page.getByRole('link', { name: '预览与处理' }).click()
    await page.getByTestId('preview-profile').selectOption({
      label: `${profileName} · fake-provider · fake-model-astral`,
    })
    await page.getByTestId('preview-run').click()
    await expect(page.getByTestId('job-state')).toHaveText('COMPLETED', { timeout: 30_000 })

    await page.getByRole('link', { name: '去阅读' }).click()
    await page.locator('.ndr-chapter').first().click()
    const spans = page.getByTestId('annotation-span')
    await expect(spans.first()).toHaveText('〔S1〕「😀𠮷！」')
    await expect(spans.nth(1)).toHaveText('〔S1〕「第二句。」')
    // 原文完整（emoji 与扩展汉字都在，没有被截断）
    await expect(page.getByTestId('document-renderer')).toContainText('「😀𠮷！」她笑了。')
  })

  test('：EPUB 的 ruby 注音不进正文，引语着色位置正确', async ({ page }) => {
    const profileName = '注音提供方'
    await createProfile(page, { name: profileName, model: 'fake-model-ruby' })
    await importFile(page, 'original-sample.epub')
    await openReader(page, '原创 ruby/插图样例')
    await page.getByRole('link', { name: '预览与处理' }).click()
    await page.getByTestId('preview-profile').selectOption({
      label: `${profileName} · fake-provider · fake-model-ruby`,
    })
    await page.getByTestId('preview-run').click()
    await expect(page.getByTestId('job-state')).toHaveText('COMPLETED', { timeout: 30_000 })

    await page.getByRole('link', { name: '去阅读' }).click()
    await page.locator('.ndr-chapter').first().click()
    // 注音仍在 <rt> 里，正文只保留基底文字
    await expect(page.locator('rt').first()).toHaveText('かん')
    await expect(page.getByTestId('annotation-span').first()).toHaveText('〔S1〕「对白」')
    // 跨块的同一句发言也被着色（范围跨越两个节点时编号只出现一次）
    await expect(
      page.getByTestId('annotation-span').filter({ hasText: '「跨块的同一句发言，」' }),
    ).toHaveCount(1)
  })
})