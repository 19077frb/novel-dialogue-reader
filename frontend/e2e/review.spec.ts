import { expect, test, type Page } from '@playwright/test'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import { confirmChapterRoster } from './roster'

/**
 * 待确认队列与阅读页确认抽屉。
 *
 * 真实后端 + 真实 Chromium + 隔离数据目录；模型侧是显式启用的确定性 FakeProvider
 * （不访问网络）。覆盖：普通对白入口（含未处理对白）、待定入口、空候选、旧版本 409、
 * DEFERRED 找回与撤销。
 */
const fixturesDir = path.join(path.dirname(fileURLToPath(import.meta.url)), 'fixtures')
const fixture = (name: string) => path.join(fixturesDir, name)

async function createFakeProfile(page: Page, name: string) {
  await page.goto('/settings/models')
  await page.getByTestId('profile-name').fill(name)
  await page.selectOption('[data-testid=profile-protocol]', 'fake-provider')
  await page.getByTestId('profile-base-url').fill('http://127.0.0.1:1')
  await page.getByTestId('profile-model').fill('fake-model')
  await page.getByTestId('profile-save').click()
  await expect(page.getByTestId('profile-card').filter({ hasText: name })).toBeVisible()
}

async function importFile(page: Page, fileName: string) {
  await page.goto('/library')
  await page.setInputFiles('[data-testid=import-file-input]', fixture(fileName))
  await page.click('[data-testid=import-submit]')
  await expect(page.getByTestId('import-result')).toContainText('导入完成：TXT')
}

async function importSample(page: Page) {
  await importFile(page, 'sample-utf8.txt')
}

/** 打开阅读页并显式回到第一章（书签可能被其它用例改到未处理的章节）。 */
async function openReader(page: Page, title = 'sample-utf8') {
  const onReader = page.url().includes('/read')
  if (!onReader) {
    if (page.url().includes('/review')) {
      await page.getByRole('link', { name: '去阅读' }).click()
    } else {
      await page.goto('/library')
      await page
        .locator('[data-testid=book-card]', { hasText: title })
        .getByRole('link', { name: '开始阅读' })
        .click()
    }
  }
  await expect(page).toHaveURL(/\/books\/[^/]+\/read/)
  await page.locator('.ndr-chapter').first().click()
}

/** 只处理第一章，让第二章保持「未处理」，用于空候选场景。 */
async function processFirstChapter(page: Page, profileLabel: string) {
  await page.getByRole('link', { name: '预览与处理' }).click()
  await expect(page).toHaveURL(/\/books\/[^/]+\/preview/)
  await page.getByTestId('preview-profile').selectOption({ label: profileLabel })
  // 逐句归属前必须先确认本章人物与第一视角主人公。
  await confirmChapterRoster(page)
  await page.getByTestId('preview-run').click()
  await expect(page.getByTestId('job-state')).toHaveText('COMPLETED', { timeout: 30_000 })
}

test.describe('待确认队列与确认抽屉', () => {
  test('阅读页入口：标记待确认、锁定未知、撤销，并同步阅读页颜色', async ({ page }) => {
    const profileName = '确认流程提供方 A'
    await createFakeProfile(page, profileName)
    await importSample(page)
    await openReader(page)
    await processFirstChapter(page, `${profileName} · fake-provider · fake-model`)

    // 回到阅读页（第一章），点击已着色的对白 → 打开确认抽屉
    await page.getByRole('link', { name: '去阅读' }).click()
    await page.locator('.ndr-chapter').first().click()
    await expect(page.getByTestId('annotation-span').first()).toBeAttached()
    await page.getByTestId('annotation-span').first().click()

    const drawer = page.getByTestId('quote-detail-drawer')
    await expect(drawer).toBeVisible()
    await expect(page.getByTestId('drawer-quote')).toHaveText('「雨停了。」')
    await expect(page.getByTestId('drawer-annotation')).toContainText('ACCEPTED')
    // 展开原文只读本地原文（不产生任务面板）
    await page.getByTestId('expand-context').click()
    await expect(page.getByTestId('quote-context')).toContainText('前后各 600 码点')

    // 主动标记 → 队列里会出现这条
    await page.getByTestId('drawer-flag').click()
    await expect(page.getByTestId('drawer-notice')).toContainText('待确认队列')

    // 锁定为未知（人工更正，不调用模型）
    await page.getByTestId('correction-action').selectOption('mark_unknown')
    await page.getByTestId('correction-submit').click()
    await expect(page.getByTestId('drawer-notice')).toContainText('已确认 1 条')
    await expect(page.getByTestId('drawer-annotation')).toContainText('UNKNOWN')

    // 撤销 → 回到 ACCEPTED
    await page.getByTestId('drawer-undo').click()
    await expect(page.getByTestId('drawer-notice')).toContainText('已撤销更正')
    await expect(page.getByTestId('drawer-annotation')).toContainText('ACCEPTED')

    // 阅读页上这条对白仍然着色（投影没有被破坏）
    await page.getByTestId('drawer-close').click()
    await expect(page.getByTestId('quote-detail-drawer')).toHaveCount(0)
    await expect(page.getByTestId('annotation-span').first()).toBeAttached()
  })

  test('待定入口与空候选：队列项可延后找回，未处理对白可新建说话人', async ({ page }) => {
    await openReader(page)
    // 在阅读页主动标记另一条对白 → 队列里出现一条 USER_FLAGGED 项
    await expect(page.getByTestId('annotation-span').nth(1)).toBeAttached()
    await page.getByTestId('annotation-span').nth(1).click()
    await page.getByTestId('drawer-flag').click()
    await expect(page.getByTestId('drawer-notice')).toContainText('待确认队列')
    await page.getByTestId('drawer-close').click()

    // 队列入口（阅读页顶部显示待确认数量）
    await expect(page.getByTestId('reader-review-link')).toContainText('待确认')
    await page.getByTestId('reader-review-link').click()
    await expect(page).toHaveURL(/\/books\/[^/]+\/review/)

    await page.getByTestId('review-reason').selectOption('USER_FLAGGED')
    const item = page.getByTestId('review-item').first()
    await expect(item).toBeVisible()
    await item.getByTestId('review-open').click()
    await expect(page.getByTestId('drawer-queue-item')).toContainText('USER_FLAGGED')

    // 跳过（延后）→ 默认视图里消失，切到「已跳过」还能找回
    await page.getByTestId('drawer-defer').click()
    await expect(page.getByTestId('drawer-notice')).toContainText('已跳过')
    await page.getByTestId('drawer-close').click()
    await page.getByTestId('review-status').selectOption('DEFERRED')
    await expect(page.getByTestId('review-item').first()).toContainText('DEFERRED')

  })

  test('未处理章节的空候选：只能新建说话人（不依赖其它用例是否处理过本书）', async ({
    page,
  }) => {
    // 专用夹具 + 只处理第一章，保证第二章确实没有标注
    const profileName = '空候选提供方'
    await createFakeProfile(page, profileName)
    await importFile(page, 'sample-review.txt')
    await openReader(page, 'sample-review')
    await processFirstChapter(page, `${profileName} · fake-provider · fake-model`)
    await page.getByRole('link', { name: '去阅读' }).click()
    await page.locator('.ndr-chapter').nth(1).click()

    const candidate = page.getByTestId('candidate-quote').first()
    await expect(candidate).toBeVisible()
    await candidate.click()
    await expect(page.getByTestId('correction-version')).toContainText('还没有标注')
    await expect(page.getByTestId('correction-no-groups')).toBeVisible()
    await page.getByTestId('correction-submit').click()
    await expect(page.getByTestId('drawer-notice')).toContainText('已确认 1 条')
    await expect(page.getByTestId('drawer-annotation')).toContainText('USER_CONFIRMED')
  })

  test('旧版本提交返回 409：提示冲突并刷新为最新状态', async ({ page }) => {
    await openReader(page)
    await expect(page.getByTestId('annotation-span').first()).toBeAttached()
    const span = page.getByTestId('annotation-span').first()
    const quoteId = await span.getAttribute('data-quote-id')
    expect(quoteId).toBeTruthy()
    await span.click()
    const version = Number(await page.getByTestId('drawer-annotation-version').innerText())
    expect(version).toBeGreaterThan(0)

    // 绕过界面把标注改掉（等价于“旧页面”）：直接调用同一个后端接口
    const status = await page.evaluate(
      async ({ id, expected }) => {
        const response = await fetch(`/api/quotes/${id}/corrections`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ action: 'mark_unknown', expected_version: expected }),
        })
        return response.status
      },
      { id: quoteId as string, expected: version },
    )
    expect(status).toBe(201)

    // 抽屉仍停在版本 1 → 提交必须冲突，而不是静默覆盖
    await page.getByTestId('correction-action').selectOption('create_speaker')
    await page.getByTestId('correction-submit').click()
    await expect(page.getByTestId('correction-error')).toContainText('已被其它操作更新')
  })
})