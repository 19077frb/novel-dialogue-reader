import { expect, test } from '@playwright/test'

test('manual bookmarks are independent, editable, persistent and navigate to the saved paragraph', async ({ page, request }) => {
  const imported = await request.post('/api/books/import', { multipart: { file: { name: 'bookmarks.txt', mimeType: 'text/plain', buffer: Buffer.from('第一章 书签\n「😀第一句。」\n第二段正文。\n第二章\n其它正文。') } } })
  expect(imported.ok()).toBe(true)
  const { book_id: bookId, book_version_id: versionId } = (await imported.json()).data
  const chapters = (await (await request.get(`/api/books/${bookId}/chapters`)).json()).data
  await page.goto(`/books/${bookId}/read`)
  await expect(page.getByTestId('document-renderer')).toBeVisible()
  await page.locator('.ndr-reader-bookmarks summary').click()
  page.once('dialog', dialog => dialog.accept('回来再看'))
  await page.getByTestId('add-bookmark').click()
  await expect(page.getByTestId('reader-notice')).toContainText('书签已添加')
  await page.getByRole('link', { name: '管理全部书签' }).click()
  const card = page.getByTestId('bookmark-card').first()
  await expect(card.getByLabel('书签备注')).toHaveValue('回来再看')
  await card.getByLabel('书签备注').fill('新的备注')
  await card.getByRole('button', { name: '保存备注' }).click()
  await page.reload()
  await expect(card.getByLabel('书签备注')).toHaveValue('新的备注')
  const marks = (await (await request.get(`/api/books/${bookId}/bookmarks`)).json()).data.items
  const anchor = chapters[0].start_cp + 8
  await request.put(`/api/books/${bookId}/reading-progress`, { data: { book_version_id: versionId, read_position_cp: anchor, reading_mode: 'initial' } })
  await card.getByRole('link', { name: '跳转阅读' }).click()
  await expect(page).toHaveURL(new RegExp(`positionCp=${marks[0].position_cp}`))
  await expect(page.getByTestId('document-renderer')).toContainText('第一句')
  const unchanged = (await (await request.get(`/api/books/${bookId}/bookmarks`)).json()).data.items
  expect(unchanged[0].position_cp).toBe(marks[0].position_cp)
  await page.goto(`/books/${bookId}/bookmarks`)
  page.once('dialog', dialog => dialog.accept())
  await page.getByRole('button', { name: '删除书签' }).click()
  await expect(page.getByTestId('bookmark-card')).toHaveCount(0)
})
