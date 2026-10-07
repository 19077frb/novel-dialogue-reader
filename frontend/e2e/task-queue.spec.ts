import { expect, test } from '@playwright/test'

test('主导航提供跨书籍队列、任务详情和历史，无需调用模型', async ({ page, request }) => {
  const jobs: string[] = []
  for (const suffix of ['甲', '乙']) {
    const imported = await request.post('/api/books/import', { multipart: {
      title: `队列测试${suffix}`,
      file: { name: `queue-${suffix}.txt`, mimeType: 'text/plain',
        buffer: Buffer.from(`第一章\n「早上好${suffix}。」\n「再见${suffix}。」\n这是一份原创离线测试文本。`) },
    } })
    expect(imported.status()).toBe(202)
    const bookId = (await imported.json()).data.book_id
    const chapters = (await (await request.get(`/api/books/${bookId}/chapters`)).json()).data
    const response = await request.post('/api/jobs', { data: {
      book_id: bookId, range: { chapter_id: chapters[0].id, start_cp: 0, end_cp: 20 },
      idempotency_key: `queue-e2e-${suffix}`, run_now: false,
    } })
    expect(response.status()).toBe(202)
    jobs.push((await response.json()).data.id)
  }
  await page.goto('/library')
  await page.getByRole('navigation', { name: '主导航' }).getByRole('link', { name: '任务队列' }).click()
  await expect(page).toHaveURL(/\/tasks$/)
  await expect(page.getByText('队列测试甲', { exact: true })).toBeVisible()
  await expect(page.getByText('队列测试乙', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: '查看任务', exact: true }).first().click()
  await expect(page.getByRole('heading', { name: '任务详情', exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: '关闭详情' })).toBeVisible()
  await page.getByRole('button', { name: '关闭详情' }).click()
  for (const id of jobs) expect((await request.post(`/api/jobs/${id}/pause`)).status()).toBe(202)
  await page.getByLabel('显示已结束任务').check()
  await expect(page.getByText('已暂停', { exact: false })).toHaveCount(2)
})
