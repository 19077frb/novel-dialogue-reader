import { expect, test } from '@playwright/test'
import { randomUUID } from 'node:crypto'

test('批量失败窗口可以直接重试，成功后目录和单章窗口显示已完成', async ({ page }) => {
  await page.goto('/settings/models')
  await page.getByTestId('profile-name').fill('失败重试离线模型')
  await page.getByTestId('profile-protocol').selectOption('fake-provider')
  await page.getByTestId('profile-base-url').fill('http://127.0.0.1:1')
  await page.getByTestId('profile-model').fill('fake-model')
  await page.getByTestId('profile-save').click()
  await expect(page.getByTestId('profile-card').filter({ hasText: '失败重试离线模型' })).toBeVisible()
  await page.goto('/library')
  await page.getByTestId('import-file-input').setInputFiles({ name: '窗口失败重试.txt', mimeType: 'text/plain',
    buffer: Buffer.from('第一章 开始\n「你好。」\n第二章 继续\n「再见。」\n') })
  await page.getByTestId('import-submit').click()
  await expect(page.getByTestId('import-result')).toBeVisible()
  await page.getByTestId('book-card').filter({ hasText: '窗口失败重试' }).getByRole('link', { name: '开始阅读' }).click()
  const bookId = page.url().match(/books\/([^/]+)/)![1]
  await page.getByRole('link', { name: '预览与处理' }).click()
  await page.getByTestId('preview-profile').selectOption({ label: '失败重试离线模型 · fake-provider · fake-model' })
  await page.getByTestId('processing-mode-batch').check()
  let submissions = 0
  // Only the first dialogue submission is simulated as a definite failure.
  // Subsequent jobs exercise the real isolated backend and offline provider.
  await page.route('**/api/jobs', async route => {
    if (route.request().method() !== 'POST') { await route.continue(); return }
    submissions++
    if (submissions !== 1) { await route.continue(); return }
    const body = route.request().postDataJSON()
    await route.fulfill({ status: 202, contentType: 'application/json', body: JSON.stringify({ data: {
      id: randomUUID(), kind: 'INFERENCE', purpose: 'process', state: 'FAILED',
      book_id: body.book_id, book_version_id: body.book_version_id, last_error: '离线模拟窗口失败',
      windows: [], remaining_windows: 1, windows_total: 1, calls: 1, cached_windows: 0,
      unknown_usage_runs: 0, usage: { input_tokens: 5, output_tokens: 5, total_tokens: 10 },
      created_at: new Date().toISOString(), updated_at: new Date().toISOString(),
    } }) })
  })
  await page.getByTestId('batch-run').click()
  await expect(page.getByTestId('batch-estimate')).toBeVisible()
  await page.getByTestId('batch-run').click()
  const result = page.getByTestId('batch-result-panel')
  await expect(result).toContainText('离线模拟窗口失败', { timeout: 30000 })
  await result.getByRole('button', { name: /重试窗口/ }).click()
  await expect.poll(async () => {
    const { data } = await (await page.request.get(`/api/books/${bookId}/chapters`)).json()
    return data.map((chapter: { dialogue_processed: boolean }) => chapter.dialogue_processed)
  }, { timeout: 30000 }).toEqual([true, true])
  await expect(result.locator('[data-task-state="failed"]')).toHaveCount(0)
  expect(submissions).toBe(3)
  await page.getByRole('link', { name: '去阅读' }).click()
  await expect(page.locator('.ndr-chapter[data-processing-state="processed"]')).toHaveCount(2)
  await page.getByRole('link', { name: '预览与处理' }).click()
  await expect(page.getByTestId('window-picker')).toContainText('已完成')
  await expect(page.getByTestId('window-picker').getByRole('checkbox')).not.toBeChecked()
})
