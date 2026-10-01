import { expect, test } from '@playwright/test'

test('导入自动修复后可检查章节与引号，章节修复排在前面', async ({ page }) => {
  await page.goto('/library')
  await page.getByTestId('import-file-input').setInputFiles({
    name: '自动修复检查.txt', mimeType: 'text/plain',
    buffer: Buffer.from('第十卷 序章\n\n序章\n“缺失闭合\n“下一句。”\n'),
  })
  await page.getByTestId('import-submit').click()
  await expect(page.getByTestId('import-result')).toContainText('章节修复 1 处，引号修复 1 处')
  await page.getByRole('link', { name: '检查预处理结果' }).click()
  await expect(page.getByTestId('chapter-repair-panel')).toBeVisible()
  await expect(page.getByTestId('chapter-repair-panel').locator('summary')).toHaveCount(1)
  const order = await page.locator('h3').allTextContents()
  expect(order[0]).toBe('章节名与边界修复')
  await page.getByRole('button', { name: '重新检查章节' }).click()
  await expect(page.getByText('第十卷 序章', { exact: true })).toBeVisible()
})

test('阅读自动处理当前章和后一章，跳过范围外章节且可以停止', async ({ page }) => {
  const name = '自动处理测试模型'
  await page.goto('/settings/models')
  await page.getByTestId('profile-name').fill(name)
  await page.getByTestId('profile-protocol').selectOption('fake-provider')
  await page.getByTestId('profile-base-url').fill('http://127.0.0.1:1')
  await page.getByTestId('profile-model').fill('fake-model')
  await page.getByTestId('profile-save').click()
  await expect(page.getByTestId('profile-card').filter({ hasText: name })).toBeVisible()
  await page.goto('/settings/general')
  await page.getByLabel('提前处理后续章节数').fill('1')
  await page.getByTestId('automatic-profile').selectOption({ label: `${name} · fake-provider · fake-model` })
  await page.getByLabel('阅读时自动处理当前章及后续章节（会调用模型）').check()
  await page.goto('/library')
  await page.getByTestId('import-file-input').setInputFiles({
    name: '自动处理范围.txt', mimeType: 'text/plain',
    buffer: Buffer.from('第一章 开始\n「你好。」\n第二章 继续\n「再见。」\n第三章 范围外\n「不用处理。」\n'),
  })
  await page.getByTestId('import-submit').click()
  await expect(page.getByTestId('import-result')).toBeVisible()
  await page.getByTestId('book-card').filter({ hasText: '自动处理范围' }).getByRole('link', { name: '开始阅读' }).click()
  await expect(page.getByTestId('automatic-processing')).toBeVisible()
  const bookId = page.url().match(/books\/([^/]+)/)?.[1]
  await expect.poll(async () => {
    const response = await page.request.get(`/api/books/${bookId}/chapters`)
    const { data } = await response.json()
    return data.map((chapter: { dialogue_processed: boolean }) => chapter.dialogue_processed)
  }, { timeout: 30000 }).toEqual([true, true, false])
  await expect(page.getByTestId('automatic-processing').getByTestId('operation-timer')).toBeVisible()
  await page.getByRole('button', { name: '停止自动处理' }).click()
  await expect(page.getByTestId('automatic-processing')).toHaveCount(0)
  await page.goto('/settings/general')
  await expect(page.getByLabel('阅读时自动处理当前章及后续章节（会调用模型）')).not.toBeChecked()
})

test('切换阅读章节时利用空闲并发追加处理，不等待旧章节的对白窗口结束', async ({ page }) => {
  const name = '持续队列离线模型'
  await page.goto('/settings/models')
  await page.getByTestId('profile-name').fill(name)
  await page.getByTestId('profile-protocol').selectOption('fake-provider')
  await page.getByTestId('profile-base-url').fill('http://127.0.0.1:1')
  await page.getByTestId('profile-model').fill('fake-model')
  await page.getByTestId('profile-save').click()
  await expect(page.getByTestId('profile-card').filter({ hasText: name })).toBeVisible()
  await page.goto('/settings/general')
  await page.getByLabel('提前处理后续章节数').fill('0')
  await page.getByLabel('最大并发任务数', { exact: true }).fill('3')
  await page.getByTestId('automatic-profile').selectOption({ label: `${name} · fake-provider · fake-model` })
  await page.getByLabel('阅读时自动处理当前章及后续章节（会调用模型）').check()
  await page.goto('/library')
  await page.getByTestId('import-file-input').setInputFiles({ name: '持续补充队列.txt', mimeType: 'text/plain',
    buffer: Buffer.from('第一章 等待\n「我先等一会儿。」\n第二章 追加\n「我可以先处理。」\n第三章 不处理\n「范围之外。」\n') })
  await page.getByTestId('import-submit').click()
  await expect(page.getByTestId('import-result')).toBeVisible()
  let held = false
  let release!: () => void
  let submissions = 0
  const gate = new Promise<void>(resolve => { release = resolve })
  await page.route('**/api/jobs', async route => {
    if (route.request().method() === 'POST') {
      submissions++
      if (submissions === 1) { held = true; await gate }
    }
    await route.continue()
  })
  try {
    await page.getByTestId('book-card').filter({ hasText: '持续补充队列' }).getByRole('link', { name: '开始阅读' }).click()
    const bookId = page.url().match(/books\/([^/]+)/)![1]
    await expect.poll(() => held).toBe(true)
    await page.locator('.ndr-chapter').filter({ hasText: '第二章' }).click()
    await expect.poll(async () => {
      const { data } = await (await page.request.get(`/api/books/${bookId}/chapters`)).json()
      return data.map((chapter: { dialogue_processed: boolean }) => chapter.dialogue_processed)
    }, { timeout: 30000 }).toEqual([false, true, false])
    expect(submissions).toBe(2)
    release()
    await expect.poll(async () => {
      const { data } = await (await page.request.get(`/api/books/${bookId}/chapters`)).json()
      return data.map((chapter: { dialogue_processed: boolean }) => chapter.dialogue_processed)
    }, { timeout: 30000 }).toEqual([true, true, false])
    expect(submissions).toBe(2)
    await page.getByRole('button', { name: '停止自动处理' }).click()
  } finally { release() }
})
