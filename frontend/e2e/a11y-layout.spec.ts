import { expect, test, type Page } from '@playwright/test'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import { confirmChapterRoster } from './roster'

/**
 * 可访问性与 360 / 1280px 布局检查。
 *
 * - 可访问性：语言、标题层级、可访问名称、图片 alt、跳转链接、对话框语义 + Escape + 焦点归还。
 * - 布局：窄屏（360×740）与宽屏（1280×900）都不出现横向溢出，关键控件仍在可点区域。
 *
 * 只用真实前后端与真实 Chromium；模型侧仍是显式启用的确定性 FakeProvider（不访问网络）。
 */
const fixturesDir = path.join(path.dirname(fileURLToPath(import.meta.url)), 'fixtures')
const fixture = (name: string) => path.join(fixturesDir, name)

async function createProfile(page: Page, name: string, model = 'fake-model-a11y') {
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

async function bookIdOf(page: Page, title: string): Promise<string> {
  await page.goto('/library')
  const href = await page
    .locator('[data-testid=book-card]', { hasText: title })
    .getByRole('link', { name: '开始阅读' })
    .getAttribute('href')
  const match = /\/books\/([^/]+)\/read/.exec(href ?? '')
  expect(match, '书架里的「开始阅读」链接应包含书籍 ID').toBeTruthy()
  return match ? match[1] : ''
}

async function expectNoHorizontalOverflow(page: Page, label: string) {
  const size = page.viewportSize()
  const measured = await page.evaluate(() => {
    const doc = document.documentElement
    return { scrollWidth: doc.scrollWidth, clientWidth: doc.clientWidth }
  })
  expect(
    measured.scrollWidth,
    `${label} 在 ${size?.width}×${size?.height} 出现横向溢出：scrollWidth=${measured.scrollWidth} clientWidth=${measured.clientWidth}`,
  ).toBeLessThanOrEqual(measured.clientWidth + 1)
}

/** 近似「可访问名称」：aria-label / aria-labelledby / 关联 label / 可见文本 / title。 */
async function namelessControls(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    function hasName(element: Element): boolean {
      const el = element as HTMLElement & { labels?: NodeListOf<HTMLLabelElement> | null }
      if (el.getAttribute('aria-label')?.trim()) return true
      if (el.getAttribute('aria-labelledby')?.trim()) return true
      if (el.getAttribute('title')?.trim()) return true
      if (el.labels && el.labels.length > 0) return true
      const tag = el.tagName
      if (tag === 'INPUT' || tag === 'SELECT' || tag === 'TEXTAREA') {
        const input = el as HTMLInputElement
        if ((input.type === 'submit' || input.type === 'button' || input.type === 'reset') && input.value) {
          return true
        }
        return false
      }
      return (el.textContent ?? '').trim().length > 0
    }

    const selector = 'button, a[href], input, select, textarea'
    return Array.from(document.querySelectorAll(selector))
      .filter((element) => !element.hasAttribute('aria-hidden') && !element.hasAttribute('disabled'))
      .filter((element) => {
        const input = element as HTMLInputElement
        return !(element.tagName === 'INPUT' && input.type === 'hidden')
      })
      .filter((element) => !hasName(element))
      .map((element) => element.outerHTML.slice(0, 140))
  })
}

test.describe('可访问性与布局检查', () => {
  test('基础可访问性：语言、标题、可访问名称、图片 alt、跳转链接、导航当前页', async ({ page }) => {
    await page.goto('/library')
    await expect(page.locator('html')).toHaveAttribute('lang', 'zh-CN')
    await expect(page.locator('h1')).toHaveCount(1)
    await expect(page.getByRole('main')).toHaveCount(1)
    await expect(page.getByRole('navigation', { name: '主导航' })).toBeVisible()
    // 当前页用 aria-current 标出来，读屏/键盘用户知道自己在哪
    await expect(page.getByRole('link', { name: '书架' })).toHaveAttribute('aria-current', 'page')

    // 先确认页面上确实有可交互元素，避免「空集合」把检查变成假通过
    const controlCount = await page.locator('button, a[href], input, select, textarea').count()
    expect(controlCount).toBeGreaterThan(5)
    expect(await namelessControls(page), '所有可交互元素都必须有可访问名称').toEqual([])
    expect(
      await page.evaluate(() =>
        Array.from(document.querySelectorAll('img'))
          .filter((img) => !img.hasAttribute('alt'))
          .map((img) => img.outerHTML.slice(0, 120)),
      ),
      '所有 img 都必须有 alt（装饰性图片用 alt=""）',
    ).toEqual([])

    // 键盘第一个 Tab 到「跳到主要内容」，激活后焦点进入 main
    await page.keyboard.press('Tab')
    const skip = page.getByRole('link', { name: '跳到主要内容' })
    await expect(skip).toBeFocused()
    await skip.press('Enter')
    await expect(page.locator('#ndr-main')).toBeFocused()

    // EPUB 阅读页：插图必须有 alt（装饰性图片也要显式 alt=""）
    await page.setInputFiles('[data-testid=import-file-input]', fixture('original-sample.epub'))
    await page.click('[data-testid=import-submit]')
    await expect(page.getByTestId('import-result')).toContainText('导入完成')
    await page
      .locator('[data-testid=book-card]', { hasText: '原创 ruby/插图样例' })
      .getByRole('link', { name: '开始阅读' })
      .click()
    await page.locator('.ndr-chapter').first().click()
    // 正文是异步加载的：先等这一章渲染完，再统计插图，避免在空文档上误判。
    await expect(page.getByTestId('document-renderer')).toContainText('插图之后的对白。')
    const images = page.locator('.ndr-document img')
    expect(await images.count()).toBeGreaterThan(0)
    expect(
      await page.evaluate(() =>
        Array.from(document.querySelectorAll('.ndr-document img'))
          .filter((img) => !img.hasAttribute('alt'))
          .map((img) => img.outerHTML.slice(0, 120)),
      ),
      '阅读页的 img 必须有 alt',
    ).toEqual([])
  })

  test('对话框与抽屉：role/aria-modal/标题关联、Escape 关闭、焦点归还', async ({ page }) => {
    const profileName = '可访问性提供方'
    await createProfile(page, profileName)
    await importFile(page, 'sample-full-flow.txt')

    await page
      .locator('[data-testid=book-card]', { hasText: 'sample-full-flow' })
      .getByRole('link', { name: '开始阅读' })
      .click()
    await page.getByRole('link', { name: '预览与处理' }).click()
    await page.getByTestId('preview-profile').selectOption({
      label: `${profileName} · fake-provider · fake-model-a11y`,
    })
    await confirmChapterRoster(page)
    await page.getByTestId('preview-run').click()
    await expect(page.getByTestId('job-state')).toHaveText('COMPLETED', { timeout: 30_000 })
    await page.getByRole('link', { name: '去阅读' }).click()
    await page.locator('.ndr-chapter').first().click()

    // 导出对话框
    const opener = page.getByTestId('open-export')
    await opener.click()
    const dialog = page.getByRole('dialog', { name: '导出' })
    await expect(dialog).toBeVisible()
    await expect(dialog).toHaveAttribute('aria-modal', 'true')
    const focusedInDialog = await dialog.evaluate((node) => node.contains(document.activeElement))
    expect(focusedInDialog, '打开后焦点必须落在对话框内').toBe(true)

    await page.keyboard.press('Escape')
    await expect(dialog).toHaveCount(0)
    await expect(opener).toBeFocused()

    // 确认抽屉（阅读页点击已标注对白）
    const span = page.getByTestId('annotation-span').first()
    await span.click()
    const drawer = page.getByRole('dialog', { name: '对白确认' })
    await expect(drawer).toBeVisible()
    const focusedInDrawer = await drawer.evaluate((node) => node.contains(document.activeElement))
    expect(focusedInDrawer, '打开后焦点必须落在抽屉内').toBe(true)

    await page.keyboard.press('Escape')
    await expect(drawer).toHaveCount(0)
  })

  test('360px 与 1280px：五个页面都无横向溢出，关键控件可见可点', async ({ page }) => {
    await importFile(page, 'sample-full-flow.txt')
    const bookId = await bookIdOf(page, 'sample-full-flow')

    const routes: Array<[string, string]> = [
      ['书架', '/library'],
      ['阅读页', `/books/${bookId}/read`],
      ['预览与处理', `/books/${bookId}/preview`],
      ['待确认队列', `/books/${bookId}/review`],
      ['模型配置', '/settings/models'],
    ]

    for (const [width, height] of [
      [360, 740],
      [1280, 900],
    ] as Array<[number, number]>) {
      await page.setViewportSize({ width, height })
      for (const [label, route] of routes) {
        await page.goto(route)
        await expect(page.locator('h1')).toHaveCount(1)
        await expectNoHorizontalOverflow(page, label)
      }
    }

    // 窄屏允许纵向滚动；关键控件必须能滚动到视口并操作
    await page.setViewportSize({ width: 360, height: 740 })
    await page.goto('/library')
    await page.getByTestId('import-submit').scrollIntoViewIfNeeded()
    await expect(page.getByTestId('import-submit')).toBeInViewport()
    await expect(page.getByTestId('book-card').first()).toBeVisible()

    await page.goto(`/books/${bookId}/read`)
    await expect(page.locator('.ndr-chapter').first()).toBeVisible()
    await page.getByTestId('open-export').scrollIntoViewIfNeeded()
    await expect(page.getByTestId('open-export')).toBeInViewport()

    // 导出对话框在 360px 下不超出视口
    await page.getByTestId('open-export').click()
    const box = await page.getByTestId('export-dialog').boundingBox()
    expect(box?.width ?? 0).toBeLessThanOrEqual(360)
    await page.keyboard.press('Escape')
    await expect(page.getByTestId('export-dialog')).toHaveCount(0)
  })
})
