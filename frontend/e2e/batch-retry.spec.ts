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
  await page.getByTestId('batch-format-retries').fill('3')
  let submissions = 0
  // Only the first dialogue submission is simulated as a definite failure.
  // Subsequent jobs exercise the real isolated backend and offline provider.
  await page.route('**/api/jobs', async route => {
    if (route.request().method() !== 'POST') { await route.continue(); return }
    submissions++
    expect(route.request().postDataJSON().budget.max_format_retries).toBe(3)
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
  await page.getByRole('link', { name: '去阅读' }).click()
  const retry = page.getByRole('button', { name: '重试第一章 开始的任务' })
  await expect(retry).toBeVisible()
  await retry.click()
  await expect.poll(async () => {
    const { data } = await (await page.request.get(`/api/books/${bookId}/chapters`)).json()
    return data.map((chapter: { dialogue_processed: boolean }) => chapter.dialogue_processed)
  }, { timeout: 30000 }).toEqual([true, true])
  expect(submissions).toBe(3)
  await expect(page.locator('.ndr-chapter[data-processing-state="processed"]')).toHaveCount(2)
  await page.getByRole('link', { name: '预览与处理' }).click()
  await expect(page.getByTestId('window-picker')).toContainText('已完成')
  await expect(page.getByTestId('window-picker').getByRole('checkbox')).not.toBeChecked()
})

test('目录取消图标只停止本章，等待请求收尾后可重试恢复已保存标注', async ({ page }) => {
  await page.goto('/settings/models')
  await page.getByTestId('profile-name').fill('章节取消离线模型')
  await page.getByTestId('profile-protocol').selectOption('fake-provider')
  await page.getByTestId('profile-base-url').fill('http://127.0.0.1:1')
  await page.getByTestId('profile-model').fill('fake-model')
  await page.getByTestId('profile-save').click()
  await expect(page.getByTestId('profile-card').filter({ hasText: '章节取消离线模型' })).toBeVisible()
  await page.goto('/library')
  await page.getByTestId('import-file-input').setInputFiles({ name: '章节取消检查.txt', mimeType: 'text/plain',
    buffer: Buffer.from('第一章 取消\n「先取消这一章。」\n第二章 继续\n「我会继续处理。」\n') })
  await page.getByTestId('import-submit').click()
  await expect(page.getByTestId('import-result')).toBeVisible()
  await page.getByTestId('book-card').filter({ hasText: '章节取消检查' }).getByRole('link', { name: '开始阅读' }).click()
  const bookId = page.url().match(/books\/([^/]+)/)![1]
  await page.getByRole('link', { name: '预览与处理' }).click()
  await page.getByTestId('preview-profile').selectOption({ label: '章节取消离线模型 · fake-provider · fake-model' })
  await page.getByTestId('processing-mode-batch').check()
  await page.getByTestId('batch-concurrency').fill('3')
  let heldJob = ''
  let holdPolling = true
  let submissions = 0
  const pauseIds: string[] = []
  await page.route('**/api/jobs', async route => {
    if (route.request().method() !== 'POST') { await route.continue(); return }
    submissions++
    const response = await route.fetch()
    const payload = await response.json()
    if (!heldJob) heldJob = payload.data.id
    await route.fulfill({ response, json: payload })
  })
  await page.route('**/api/jobs/**', async route => {
    const url = new URL(route.request().url())
    if (url.pathname.endsWith('/pause')) { pauseIds.push(url.pathname.split('/').at(-2)!); await route.continue(); return }
    if (route.request().method() === 'GET' && url.pathname.endsWith(`/${heldJob}`) && holdPolling) {
      const response = await route.fetch()
      const payload = await response.json()
      payload.data.state = 'RUNNING' // Simulate a provider still finishing its request.
      await route.fulfill({ response, json: payload }); return
    }
    await route.continue()
  })
  await page.getByTestId('batch-run').click()
  await expect(page.getByTestId('batch-estimate')).toBeVisible()
  await page.getByTestId('batch-run').click()
  await expect.poll(() => heldJob).not.toBe('')
  await page.getByRole('link', { name: '去阅读' }).click()
  const cancel = page.getByRole('button', { name: '取消第一章 取消的任务' })
  await expect(cancel).toBeVisible()
  await cancel.click()
  await expect(cancel).toBeDisabled()
  await expect(page.locator('.ndr-chapter').filter({ hasText: '第一章' })).toContainText('正在取消')
  await expect.poll(async () => {
    const { data } = await (await page.request.get(`/api/books/${bookId}/chapters`)).json()
    return data.map((chapter: { dialogue_processed: boolean }) => chapter.dialogue_processed)
  }).toEqual([false, true])
  expect(pauseIds).toEqual([heldJob])
  holdPolling = false
  const retry = page.getByRole('button', { name: '重试第一章 取消的任务' })
  await expect(retry).toBeVisible()
  await retry.click()
  await expect(page.locator('.ndr-chapter[data-processing-state="processed"]')).toHaveCount(2)
  expect(submissions).toBe(2) // The completed annotation survives cancellation; recovery makes no new model call.
})
