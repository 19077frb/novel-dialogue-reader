import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { expect, test, type APIRequestContext, type Locator, type Page } from '@playwright/test'

const widthKey = 'ndr:reader-text-width:px:v1'
const longChapter = '第一章 河岸札记\n\n' + Array.from({ length: 120 }, (_, index) =>
  `第${index + 1}段，阿宁沿着石阶走到河边，把今天看到的景色写在纸上。` +
  '浅水里的石子被阳光照亮，远处的小桥有人慢慢走过。小禾收起雨伞，仔细听着树叶间的风声。' +
  '「我们明天还来这里，把剩下的故事写完。」她说。\n\n',
).join('')

async function importLongChapter(request: APIRequestContext) {
  const imported = await request.post('/api/books/import', { multipart: { file: {
    name: '原创阅读宽度回归.txt', mimeType: 'text/plain', buffer: Buffer.from(longChapter),
  } } })
  expect(imported.ok()).toBe(true)
  const { book_id: bookId, book_version_id: versionId } = (await imported.json()).data
  const chapters = (await (await request.get(`/api/books/${bookId}/chapters`)).json()).data
  expect(chapters.length).toBeGreaterThan(0)
  return { bookId: bookId as string, versionId: versionId as string, chapter: chapters[0] as { id: string; start_cp: number } }
}

async function enterReader(page: Page, bookId: string) {
  await page.goto(`/books/${bookId}/read`)
  await expect(page.getByTestId('health-ok')).toBeVisible()
  await expect(page.getByTestId('document-renderer')).toBeVisible()
  await expect(page.locator('[data-paragraph-start]').first()).toBeVisible()
}

async function paragraphAnchor(page: Page) {
  return page.locator('.ndr-reader-content').evaluate(container => {
    const clipTop = Math.max(0, container.getBoundingClientRect().top)
    const paragraph = [...container.querySelectorAll<HTMLElement>('[data-paragraph-start]')]
      .find(element => element.getBoundingClientRect().bottom > clipTop + 1)
    if (!paragraph) throw new Error('The reader has no visible paragraph')
    const node = paragraph.closest<HTMLElement>('[data-node-id]') ?? paragraph
    const nodeBounds = node.getBoundingClientRect()
    return {
      cp: Number(paragraph.dataset.paragraphStart),
      offset: paragraph.getBoundingClientRect().top - clipTop,
      height: paragraph.getBoundingClientRect().height,
      nodeOffset: nodeBounds.top - clipTop,
      nodeHeight: nodeBounds.height,
      lineHeight: parseFloat(getComputedStyle(node.querySelector('p') ?? node).lineHeight),
    }
  })
}

async function expectAnchor(page: Page, anchor: { cp: number; offset: number }) {
  await expect.poll(async () => (await paragraphAnchor(page)).cp).toBe(anchor.cp)
  await expect.poll(async () => Math.abs((await paragraphAnchor(page)).offset - anchor.offset)).toBeLessThanOrEqual(3)
}

async function expectAnchorAfterWidening(page: Page, anchor: Awaited<ReturnType<typeof paragraphAnchor>>) {
  const current = await paragraphAnchor(page)
  expect(current.cp).toBe(anchor.cp)
  if (anchor.nodeOffset <= -current.nodeHeight) {
    // Reflow can make the whole old paragraph fall above the viewport. Recover
    // one visible line of that same paragraph only in this measured boundary.
    const visibleHeight = current.nodeOffset + current.nodeHeight
    expect(visibleHeight).toBeGreaterThanOrEqual(Math.min(current.lineHeight, current.nodeHeight) - 1)
    expect(visibleHeight).toBeLessThanOrEqual(Math.min(current.lineHeight, current.nodeHeight) + 3)
  } else {
    await expectAnchor(page, anchor)
  }
}

async function frameWidth(page: Page) {
  return (await page.getByTestId('reader-text-frame').boundingBox())!.width
}

async function trackWidth(page: Page) {
  return (await page.getByTestId('reader-text-track').boundingBox())!.width
}

async function expectFrameWidth(page: Page, expected: number) {
  await expect.poll(async () => Math.abs(await frameWidth(page) - expected)).toBeLessThanOrEqual(1)
}

async function expectFullWidth(page: Page) {
  await expect.poll(async () => Math.abs(await frameWidth(page) - await trackWidth(page))).toBeLessThanOrEqual(1)
  await expect.poll(async () => Math.abs(Number(await page.getByTestId('reader-text-right-handle').getAttribute('aria-valuenow'))
    - await trackWidth(page))).toBeLessThanOrEqual(1)
}

async function handlePoint(page: Page, handle: Locator) {
  const bounds = (await handle.boundingBox())!
  const height = page.viewportSize()!.height
  return { x: bounds.x + bounds.width / 2, y: Math.max(bounds.y + 12, Math.min(bounds.y + bounds.height / 2, height - 40)) }
}

async function dragBoundary(page: Page, handle: Locator, delta: number) {
  const point = await handlePoint(page, handle)
  await page.mouse.move(point.x, point.y)
  await page.mouse.down()
  await page.mouse.move(point.x + delta, point.y, { steps: 12 })
  await page.mouse.up()
  await expect(page.getByTestId('reader-text-frame')).toHaveAttribute('data-resizing', 'false')
}

async function storedWidth(page: Page) {
  return page.evaluate(key => JSON.parse(localStorage.getItem(key) ?? 'null') as number | null, widthKey)
}

async function expectNoOverflow(page: Page) {
  await expect.poll(() => page.evaluate(() => Math.max(document.documentElement.scrollWidth, document.body.scrollWidth) - innerWidth)).toBeLessThanOrEqual(1)
}

async function expectExternalHandles(page: Page) {
  const frame = (await page.getByTestId('reader-text-frame').boundingBox())!
  const left = (await page.getByTestId('reader-text-left-handle').boundingBox())!
  const right = (await page.getByTestId('reader-text-right-handle').boundingBox())!
  expect(left.x + left.width).toBeLessThanOrEqual(frame.x + 1)
  expect(right.x).toBeGreaterThanOrEqual(frame.x + frame.width - 1)
  expect(left.width).toBeGreaterThanOrEqual(16)
  expect(right.width).toBeGreaterThanOrEqual(16)
}

test('正文两侧调整保留长章位置和书签草稿，拖动后继续保存阅读进度', async ({ page, request }, testInfo) => {
  test.setTimeout(75_000)
  const { bookId, versionId, chapter } = await importLongChapter(request)
  const created = await request.post(`/api/books/${bookId}/bookmarks`, { data: {
    book_version_id: versionId, chapter_id: chapter.id, position_cp: chapter.start_cp, note: '已保存的原备注',
  } })
  expect(created.ok()).toBe(true)
  await page.setViewportSize({ width: 1440, height: 1000 })
  const progress: { phase: string; cp: number }[] = []
  let phase = 'normal'
  page.on('request', outgoing => {
    if (outgoing.method() === 'PUT' && outgoing.url().endsWith(`/api/books/${bookId}/reading-progress`)) {
      progress.push({ phase, cp: outgoing.postDataJSON().read_position_cp })
    }
  })
  await enterReader(page, bookId)
  const rendered = page.getByTestId('document-renderer')
  const scroll = page.locator('.ndr-reader-content')
  const left = page.getByRole('separator', { name: '调整正文左边界', exact: true })
  const right = page.getByRole('separator', { name: '调整正文右边界', exact: true })
  await expect(left).toHaveAttribute('aria-orientation', 'vertical')
  await expectFullWidth(page)
  await expect.poll(async () => Math.abs(Number(await right.getAttribute('aria-valuemax')) - await trackWidth(page))).toBeLessThanOrEqual(1)
  await rendered.evaluate(element => element.setAttribute('data-width-stable', 'original'))
  await scroll.evaluate(element => element.setAttribute('data-width-stable', 'original'))
  await page.getByRole('tab', { name: '书签', exact: true }).click()
  const bookmark = page.getByTestId('bookmark-card').first()
  await expect(bookmark).toContainText('已保存的原备注')
  await bookmark.getByText('编辑备注', { exact: true }).click()
  const note = bookmark.getByLabel('书签备注')
  await note.fill('调整正文后继续编辑的草稿')
  await note.evaluate(element => element.setAttribute('data-width-stable', 'original'))
  await scroll.evaluate(container => {
    const paragraph = container.querySelectorAll<HTMLElement>('[data-paragraph-start]')[55]
    if (!paragraph) throw new Error('The long chapter fixture is incomplete')
    container.scrollTop += paragraph.getBoundingClientRect().top - container.getBoundingClientRect().top - 8
  })
  const anchor = await paragraphAnchor(page)
  expect(anchor.cp).toBeGreaterThan(1000)
  await expect.poll(async () => (await (await request.get(`/api/books/${bookId}`)).json()).data.read_position_cp).toBe(anchor.cp)
  const initialWidth = await frameWidth(page)

  for (const [boundary, delta, dragPhase] of [[left, 65, 'left-drag'], [right, -45, 'right-drag']] as const) {
    const before = await frameWidth(page)
    const firstRequest = progress.length
    const point = await handlePoint(page, boundary)
    phase = dragPhase
    await page.mouse.move(point.x, point.y)
    await page.mouse.down()
    await page.mouse.move(point.x + delta, point.y, { steps: 12 })
    await expect(page.getByTestId('reader-text-frame')).toHaveAttribute('data-resizing', 'true')
    await expect.poll(() => frameWidth(page)).toBeLessThan(before - 30)
    await expectAnchor(page, anchor)
    // Observe beyond the real 600 ms progress debounce while the pointer is held.
    // This detects a layout-induced write, rather than only checking the final width.
    await page.waitForTimeout(750)
    expect(progress.slice(firstRequest).every(write => write.cp === anchor.cp)).toBe(true)
    await page.mouse.up()
    phase = 'normal'
    await expect(page.getByTestId('reader-text-frame')).toHaveAttribute('data-resizing', 'false')
    await expectAnchor(page, anchor)
    await expect(note).toHaveValue('调整正文后继续编辑的草稿')
    await expect(note).toHaveAttribute('data-width-stable', 'original')
    await expect(rendered).toHaveAttribute('data-width-stable', 'original')
    await expect(scroll).toHaveAttribute('data-width-stable', 'original')
  }
  expect(await frameWidth(page)).toBeLessThan(initialWidth - 100)
  const adjustedWidth = await frameWidth(page)
  expect(await storedWidth(page)).toBeCloseTo(adjustedWidth, 0)
  await expectExternalHandles(page)
  await expectNoOverflow(page)
  await page.screenshot({ path: testInfo.outputPath('reader-width-mid-chapter-draft.png'), fullPage: true })

  await page.getByRole('button', { name: '隐藏阅读导航', exact: true }).click()
  await expect(note).toBeHidden()
  await expectFrameWidth(page, adjustedWidth)
  await page.getByRole('button', { name: '显示阅读导航', exact: true }).click()
  await expectFrameWidth(page, adjustedWidth)
  await expect(note).toHaveValue('调整正文后继续编辑的草稿')
  await expect(rendered).toHaveAttribute('data-width-stable', 'original')
  // A real subsequent scroll must still reach the reading-progress API.
  const beforeManualScroll = await paragraphAnchor(page)
  const saved = page.waitForResponse(response => response.request().method() === 'PUT'
    && response.url().endsWith(`/api/books/${bookId}/reading-progress`) && response.ok())
  await scroll.evaluate(element => { element.scrollTop += 380 })
  await saved
  const afterManualScroll = await paragraphAnchor(page)
  expect(afterManualScroll.cp).toBeGreaterThan(beforeManualScroll.cp)
  await expect.poll(async () => (await (await request.get(`/api/books/${bookId}`)).json()).data.read_position_cp).toBe(afterManualScroll.cp)

  await right.focus()
  const beforeKey = await frameWidth(page)
  await right.press('ArrowLeft')
  await expect.poll(() => frameWidth(page)).toBeLessThan(beforeKey)
  await testInfo.attach('keyboard-resize-anchor-geometry', {
    body: JSON.stringify({ before: afterManualScroll, after: await paragraphAnchor(page) }, null, 2),
    contentType: 'application/json',
  })
  await expectAnchor(page, afterManualScroll)
  const beforeEnd = await paragraphAnchor(page)
  await right.press('End')
  await expectFullWidth(page)
  await testInfo.attach('full-width-anchor-geometry', {
    body: JSON.stringify({ before: beforeEnd, after: await paragraphAnchor(page) }, null, 2),
    contentType: 'application/json',
  })
  await expectAnchorAfterWidening(page, beforeEnd)
  const afterEnd = await paragraphAnchor(page)
  const minimum = await right.getAttribute('aria-valuemin')
  expect(minimum).not.toBeNull()
  await right.press('Home')
  await expect(right).toHaveAttribute('aria-valuenow', minimum!)
  expect(await frameWidth(page)).toBeGreaterThanOrEqual(319)
  await expectAnchor(page, afterEnd)
  const beforeDoubleClick = await paragraphAnchor(page)
  await right.dblclick()
  await expectFullWidth(page)
  await expectAnchorAfterWidening(page, beforeDoubleClick)
  await dragBoundary(page, right, -70)
  const preferredWidth = await storedWidth(page)
  expect(preferredWidth).not.toBeNull()
  expect(preferredWidth).toBeLessThan(await trackWidth(page))
  await expectFrameWidth(page, preferredWidth!)
  await expect(note).toHaveValue('调整正文后继续编辑的草稿')
  const bookmarkSaved = page.waitForResponse(response => response.request().method() === 'PATCH'
    && response.url().includes(`/api/books/${bookId}/bookmarks/`) && response.ok())
  await bookmark.getByRole('button', { name: '保存备注', exact: true }).click()
  await bookmarkSaved
  await page.reload()
  await expect(rendered).toBeVisible()
  await expect.poll(() => storedWidth(page)).toBe(preferredWidth)
  await expect(right).toHaveAttribute('aria-valuenow', String(preferredWidth))
  await expectFrameWidth(page, preferredWidth!)
  await expect(page.locator(`[data-paragraph-start="${afterManualScroll.cp}"]`)).toBeInViewport()
  await page.getByRole('tab', { name: '书签', exact: true }).click()
  await expect(bookmark).toContainText('调整正文后继续编辑的草稿')
})

for (const colorScheme of ['light', 'dark'] as const) {
  test(`正文宽度${colorScheme}主题在宽窄屏保持完整图文和透明边界`, async ({ page, request }, testInfo) => {
    test.setTimeout(75_000)
    const imported = await request.post('/api/books/import', { multipart: { file: {
      name: 'original-sample.epub', mimeType: 'application/epub+zip',
      buffer: readFileSync(fileURLToPath(new URL('./fixtures/original-sample.epub', import.meta.url))),
    } } })
    expect(imported.ok()).toBe(true)
    const bookId = (await imported.json()).data.book_id as string
    await page.emulateMedia({ colorScheme })
    await page.setViewportSize({ width: 1920, height: 1000 })
    await enterReader(page, bookId)
    await expect(page.getByTestId('document-renderer')).toContainText('插图之后的对白。')
    await expect.poll(() => page.locator('.ndr-document img').evaluateAll(images => images.length > 0
      && images.every(image => (image as HTMLImageElement).complete && (image as HTMLImageElement).naturalWidth > 0))).toBe(true)
    const right = page.getByTestId('reader-text-right-handle')
    await expectFullWidth(page)
    const initiallyMeasuredWidth = await frameWidth(page)
    await page.getByRole('button', { name: '隐藏阅读导航', exact: true }).click()
    await expectFrameWidth(page, initiallyMeasuredWidth)
    await page.getByRole('button', { name: '显示阅读导航', exact: true }).click()
    await expectFrameWidth(page, initiallyMeasuredWidth)
    await page.getByRole('button', { name: '隐藏阅读导航', exact: true }).click()
    await expectFrameWidth(page, initiallyMeasuredWidth)
    await right.press('End')
    await expectFullWidth(page)
    expect((await page.getByTestId('document-renderer').boundingBox())!.width).toBeGreaterThan(900)
    await right.evaluate(element => (element as HTMLElement).blur())
    await page.mouse.move(0, 0)
    await page.screenshot({ path: testInfo.outputPath(`reader-width-1920-${colorScheme}-full.png`), fullPage: true })
    await dragBoundary(page, right, -110)
    const preferredWidth = await storedWidth(page)
    expect(preferredWidth).not.toBeNull()
    await expectFrameWidth(page, preferredWidth!)
    // An expanded card clamps to the available column when navigation returns,
    // then recovers its chosen pixel width when that space becomes available.
    await page.getByRole('button', { name: '显示阅读导航', exact: true }).click()
    expect(await trackWidth(page)).toBeLessThan(preferredWidth! - 20)
    await expectFullWidth(page)
    expect(await storedWidth(page)).toBe(preferredWidth)
    await page.getByRole('button', { name: '隐藏阅读导航', exact: true }).click()
    await expectFrameWidth(page, preferredWidth!)
    for (const width of [1920, 1440, 1280, 800, 360]) {
      await page.setViewportSize({ width, height: 1000 })
      await expect(page.getByTestId('document-renderer')).toBeVisible()
      await expectNoOverflow(page)
      expect(await storedWidth(page)).toBe(preferredWidth)
      const frame = (await page.getByTestId('reader-text-frame').boundingBox())!
      const track = (await page.getByTestId('reader-text-track').boundingBox())!
      if (width <= 900) {
        await expect(page.getByTestId('reader-text-left-handle')).toBeHidden()
        await expect(right).toBeHidden()
        expect(Math.abs(frame.width - track.width)).toBeLessThanOrEqual(1)
      } else {
        await expectExternalHandles(page)
        await expectFrameWidth(page, Math.min(preferredWidth!, track.width))
        await expect.poll(async () => Math.abs(Number(await right.getAttribute('aria-valuenow'))
          - Math.min(preferredWidth!, await trackWidth(page)))).toBeLessThanOrEqual(1)
      }
      expect(await page.locator('.ndr-document img').evaluateAll(images => images.every(image => {
        const imageBox = image.getBoundingClientRect()
        const contentBox = image.closest('.ndr-document')!.getBoundingClientRect()
        return imageBox.left >= contentBox.left - 1 && imageBox.right <= contentBox.right + 1
      }))).toBe(true)
      await page.evaluate(() => { if (document.activeElement instanceof HTMLElement) document.activeElement.blur() })
      await page.mouse.move(0, 0)
      if (width > 900) await expect.poll(() => right.evaluate(element => getComputedStyle(element, '::before').opacity)).toBe('0')
      await page.screenshot({ path: testInfo.outputPath(`reader-width-${width}-${colorScheme}-quiet.png`), fullPage: true })
    }
    await page.setViewportSize({ width: 1920, height: 1000 })
    await expectFrameWidth(page, preferredWidth!)
    await expect(right).toHaveAttribute('aria-valuenow', String(preferredWidth))
    expect(await storedWidth(page)).toBe(preferredWidth)
    await page.setViewportSize({ width: 1440, height: 1000 })
    await expectFrameWidth(page, Math.min(preferredWidth!, await trackWidth(page)))
    await right.hover()
    await expect.poll(() => right.evaluate(element => getComputedStyle(element, '::before').opacity)).toBe('1')
    await page.screenshot({ path: testInfo.outputPath(`reader-width-${colorScheme}-hover.png`), fullPage: true })
    await page.mouse.move(0, 0)
    await right.focus()
    await page.keyboard.press('Shift+Tab')
    await page.keyboard.press('Tab')
    await expect(right).toBeFocused()
    expect(await right.evaluate(element => element.matches(':focus-visible'))).toBe(true)
    await expect.poll(() => right.evaluate(element => getComputedStyle(element, '::before').opacity)).toBe('1')
    await page.screenshot({ path: testInfo.outputPath(`reader-width-${colorScheme}-focus.png`), fullPage: true })
  })
}

test.describe('触屏正文宽度', () => {
  test.use({ hasTouch: true, viewport: { width: 1440, height: 1000 } })
  test('外侧热区支持触摸拖动且与目录分隔线独立，取消后恢复正常滚动', async ({ page, request, context }) => {
    const { bookId } = await importLongChapter(request)
    await enterReader(page, bookId)
    const right = page.getByTestId('reader-text-right-handle')
    const point = await handlePoint(page, right)
    const before = await frameWidth(page)
    const cdp = await context.newCDPSession(page)
    await cdp.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ ...point, id: 1 }] })
    await cdp.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: [{ x: point.x - 70, y: point.y, id: 1 }] })
    await expect.poll(() => frameWidth(page)).toBeLessThan(before - 40)
    await cdp.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] })
    await expect(page.getByTestId('reader-text-frame')).toHaveAttribute('data-resizing', 'false')
    const navigationBefore = (await page.locator('.ndr-reader-navigation-panel').boundingBox())!.width
    const beforeLeftDrag = await frameWidth(page)
    const leftPoint = await handlePoint(page, page.getByTestId('reader-text-left-handle'))
    await cdp.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ ...leftPoint, id: 2 }] })
    await cdp.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: [{ x: leftPoint.x - 40, y: leftPoint.y, id: 2 }] })
    await expect.poll(() => frameWidth(page)).toBeGreaterThan(beforeLeftDrag + 30)
    expect(Math.abs((await page.locator('.ndr-reader-navigation-panel').boundingBox())!.width - navigationBefore)).toBeLessThanOrEqual(1)
    await cdp.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] })
    const beforeCancellation = await frameWidth(page)
    const cancelPoint = await handlePoint(page, right)
    await cdp.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ ...cancelPoint, id: 3 }] })
    await cdp.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: [{ x: cancelPoint.x - 35, y: cancelPoint.y, id: 3 }] })
    await expect.poll(() => frameWidth(page)).toBeLessThan(beforeCancellation - 20)
    await cdp.send('Input.dispatchTouchEvent', { type: 'touchCancel', touchPoints: [] })
    await expect(page.getByTestId('reader-text-frame')).toHaveAttribute('data-resizing', 'false')
    await expect.poll(async () => Math.abs(await frameWidth(page) - beforeCancellation)).toBeLessThanOrEqual(1)
    const scroll = page.locator('.ndr-reader-content')
    const scrollBefore = await scroll.evaluate(element => element.scrollTop)
    const bounds = (await scroll.boundingBox())!
    const x = bounds.x + bounds.width / 2
    const y = Math.min(bounds.y + bounds.height - 50, page.viewportSize()!.height - 50)
    await cdp.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x, y, id: 4 }] })
    for (let step = 1; step <= 6; step++) {
      await cdp.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: [{ x, y: y - step * 25, id: 4 }] })
    }
    await cdp.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] })
    await expect.poll(() => scroll.evaluate(element => element.scrollTop)).toBeGreaterThan(scrollBefore)
    await expectNoOverflow(page)
    await cdp.detach()
  })
})
