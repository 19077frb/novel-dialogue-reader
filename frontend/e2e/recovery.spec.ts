import { expect, test, type Page } from '@playwright/test'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import { confirmChapterRoster } from './roster'

/**
 * 暂停恢复、预算到顶与故障闭环（界面侧）。
 *
 * 真实后端 + 真实 Chromium + 隔离数据目录；模型侧只用显式启用的 FakeProvider
 * （`NDR_ALLOW_FAKE_PROVIDER=1`），**不访问任何真实服务**。
 * 进程重启后的恢复扫描由 `backend/tests/integration/test_recovery.py` 覆盖（E2E 无法重启 webServer）。
 */
const fixturesDir = path.join(path.dirname(fileURLToPath(import.meta.url)), 'fixtures')
const fixture = (name: string) => path.join(fixturesDir, name)

async function createProfile(
  page: Page,
  input: {
    name: string
    protocol?: string
    model?: string
    credentialMode?: 'session' | 'none'
    apiKey?: string
    params?: string
  },
) {
  await page.goto('/settings/models')
  await page.getByTestId('profile-name').fill(input.name)
  await page.selectOption('[data-testid=profile-protocol]', input.protocol ?? 'fake-provider')
  await page.getByTestId('profile-base-url').fill('http://127.0.0.1:1')
  await page.getByTestId('profile-model').fill(input.model ?? 'fake-model')
  await page.getByTestId('profile-params').fill(input.params ?? '{}')
  await page.getByTestId('credential-mode-session').check()
  if (input.credentialMode === 'none') {
    await page.getByTestId('credential-mode-none').check()
  }
  if (input.apiKey) {
    await page.getByTestId('profile-api-key').fill(input.apiKey)
  }
  await page.getByTestId('profile-save').click()
  await expect(page.getByTestId('profile-card').filter({ hasText: input.name })).toBeVisible()
}

async function openPreview(page: Page, title = 'sample-utf8') {
  await page.goto('/library')
  await page
    .locator('[data-testid=book-card]', { hasText: title })
    .getByRole('link', { name: '开始阅读' })
    .click()
  await page.getByRole('link', { name: '预览与处理' }).click()
  await expect(page).toHaveURL(/\/books\/[^/]+\/preview/)
}

async function importFile(page: Page, fileName: string) {
  await page.goto('/library')
  await page.setInputFiles('[data-testid=import-file-input]', fixture(fileName))
  await page.click('[data-testid=import-submit]')
  await expect(page.getByTestId('import-result')).toContainText('导入完成')
}

test.describe('预算与故障恢复', () => {
  test('预算到顶：不发调用、提示「用新预算重新处理」，提高预算后显式重算可完成', async ({
    page,
  }) => {
    const profileName = '预算到顶提供方'
    await createProfile(page, { name: profileName })
    await importFile(page, 'sample-utf8.txt')
    await openPreview(page)
    await page
      .getByTestId('preview-profile')
      .selectOption({ label: `${profileName} · fake-provider · fake-model` })
    await confirmChapterRoster(page)

    await page.getByTestId('budget-max-input').fill('1')
    await page.getByTestId('preview-run').click()
    await expect(page.getByTestId('job-state')).toHaveText('BUDGET_EXHAUSTED', {
      timeout: 30_000,
    })
    // 到顶后不再产生调用
    await expect(page.getByTestId('job-calls')).toHaveText('0')
    await expect(page.getByTestId('recovery-summary')).toContainText('预算')
    await expect(page.getByTestId('job-action-new_job')).toContainText('提高预算后重新处理')
    // 默认不会自动重算：这里必须由用户显式点
    await expect(page.getByTestId('recompute-entry')).toContainText('不会')

    await page.getByTestId('budget-max-input').fill('200000')
    await page.getByTestId('preview-recompute').click()
    await expect(page.getByTestId('job-state')).toHaveText('COMPLETED', { timeout: 30_000 })
    await expect(page.getByTestId('annotation-span').first()).toBeAttached()
  })

  test('缺 Key：任务明确失败并指向模型配置，原文仍然可读', async ({ page }) => {
    const profileName = '缺密钥提供方'
    const rosterProfileName = '缺密钥人物提供方'
    // 人物名单先用离线提供方确认：要验证的是标注任务在缺 Key 时如实失败。
    await createProfile(page, { name: rosterProfileName, model: 'fake-model' })
    await createProfile(page, {
      name: profileName,
      protocol: 'chat-completions-compatible',
      model: 'fake-model-no-key',
      credentialMode: 'session',
    })
    await importFile(page, 'sample-gb18030.txt')
    await openPreview(page, 'sample-gb18030')
    await page
      .getByTestId('preview-profile')
      .selectOption({ label: `${rosterProfileName} · fake-provider · fake-model` })
    await confirmChapterRoster(page)
    await page
      .getByTestId('preview-profile')
      .selectOption({ label: `${profileName} · chat-completions-compatible · fake-model-no-key` })

    await page.getByTestId('preview-run').click()
    await expect(page.getByTestId('job-state')).toHaveText('FAILED', { timeout: 30_000 })
    await expect(page.getByTestId('recovery-summary')).toContainText('失败')
    await expect(page.getByTestId('job-action-open_settings')).toBeVisible()

    // 原文不受任务失败影响
    await page.getByRole('link', { name: '去阅读' }).click()
    await page.locator('.ndr-chapter').first().click()
    await expect(page.getByTestId('candidate-quote').first()).toBeVisible()
  })

  test('提供方超时：结果未知不自动重发，可保留未知或确认重发', async ({ page }) => {
    const profileName = '超时提供方'
    await createProfile(page, {
      name: profileName,
      model: 'fake-model-timeout',
      params: JSON.stringify({ script: 'timeout_once' }),
    })
    await importFile(page, 'original-sample.epub')
    await openPreview(page, '原创 ruby/插图样例')
    await page
      .getByTestId('preview-profile')
      .selectOption({ label: `${profileName} · fake-provider · fake-model-timeout` })
    await confirmChapterRoster(page)

    await page.getByTestId('preview-run').click()
    await expect(page.getByTestId('job-state')).toHaveText('NEEDS_RECONCILIATION', {
      timeout: 30_000,
    })
    await expect(page.getByTestId('recovery-summary')).toContainText('不会')
    await expect(page.getByTestId('job-action-reconcile_retry')).toContainText('可能计费')
    await expect(page.getByTestId('job-action-reconcile_keep')).not.toContainText('可能计费')
    // 未知用量单独计数，不写 0
    await expect(page.getByTestId('job-unknown-usage')).toHaveText('1')

    await page.getByTestId('job-action-reconcile_keep').click()
    await expect(page.getByTestId('job-state')).toHaveText('PARTIAL', { timeout: 30_000 })
    await expect(page.getByTestId('recovery-summary')).toContainText('部分完成')
  })
})