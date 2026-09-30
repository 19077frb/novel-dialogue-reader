import { expect, test } from '@playwright/test'

test('sentence bookmarks are available beside the catalog, editable and navigate to the exact sentence', async ({ page, request }, testInfo) => {
  const imported = await request.post('/api/books/import', { multipart: { file: { name: 'bookmarks.txt', mimeType: 'text/plain', buffer: Buffer.from('第一章 书签\n「😀第一句。第二句！」\n第二段正文。\n第二章\n其它正文。') } } })
  expect(imported.ok()).toBe(true)
  const { book_id: bookId, book_version_id: versionId } = (await imported.json()).data
  const chapters = (await (await request.get(`/api/books/${bookId}/chapters`)).json()).data
  await page.goto(`/books/${bookId}/read`)
  await expect(page.getByTestId('document-renderer')).toBeVisible()
  page.once('dialog', dialog => dialog.accept('回来再看'))
  const sentence = page.locator('[data-sentence-start]').filter({ hasText: '第二句！' })
  const sentenceCp = Number(await sentence.getAttribute('data-sentence-start'))
  expect(sentenceCp).toBeGreaterThan(chapters[0].start_cp)
  await sentence.hover()
  await sentence.getByRole('button', { name: /保存书签/ }).click()
  await expect(page.getByTestId('reader-notice')).toContainText('书签已添加')
  await page.getByRole('tab', { name: '书签', exact: true }).click()
  const card = page.getByTestId('bookmark-card').first()
  await expect(card).toContainText('第二句！')
  await expect(card).not.toContainText('第一句')
  await page.screenshot({ path: testInfo.outputPath('sentence-bookmarks.png'), fullPage: true })
  await card.getByText('编辑备注', { exact: true }).click()
  await expect(card.getByLabel('书签备注')).toHaveValue('回来再看')
  await card.getByLabel('书签备注').fill('新的备注')
  await card.getByRole('button', { name: '保存备注' }).click()
  await page.reload()
  await page.getByRole('tab', { name: '书签', exact: true }).click()
  await card.getByText('编辑备注', { exact: true }).click()
  await expect(card.getByLabel('书签备注')).toHaveValue('新的备注')
  const marks = (await (await request.get(`/api/books/${bookId}/bookmarks`)).json()).data.items
  expect(marks[0].position_cp).toBe(sentenceCp)
  const anchor = chapters[0].start_cp + 8
  await request.put(`/api/books/${bookId}/reading-progress`, { data: { book_version_id: versionId, read_position_cp: anchor, reading_mode: 'initial' } })
  await card.getByRole('link', { name: '跳转阅读' }).click()
  await expect(page).toHaveURL(new RegExp(`positionCp=${marks[0].position_cp}`))
  await expect(page.getByTestId('document-renderer')).toContainText('第一句')
  await expect(page.locator(`[data-sentence-start="${sentenceCp}"]`)).toBeInViewport()
  const unchanged = (await (await request.get(`/api/books/${bookId}/bookmarks`)).json()).data.items
  expect(unchanged[0].position_cp).toBe(marks[0].position_cp)
  await page.goto(`/books/${bookId}/bookmarks`)
  page.once('dialog', dialog => dialog.accept())
  await page.getByRole('button', { name: '删除书签' }).click()
  await expect(page.getByTestId('bookmark-card')).toHaveCount(0)
})
