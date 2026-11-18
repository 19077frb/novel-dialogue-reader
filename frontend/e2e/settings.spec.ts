import { revealProfile } from './profiles'
import { expect, test, type Page } from '@playwright/test'

/**
 * 用户不改源码即可配置模型；密钥只在提交时出现，界面与接口都不回显；
 * 连接测试用微型结构化请求，并明确标注测试用适配器。
 * 后端使用 session 凭据后端与显式启用的 FakeProvider（见 playwright.config.ts），不会触碰真实凭据库。
 *
 * 注意：E2E 数据目录在同一次运行里是共享的，其它用例也可能留下配置，
 * 因此这里所有断言都按**配置名**限定到具体卡片，不依赖“列表是空的”。
 */
const SECRET = 'sk-e2e-secret-value'
const applicationRetryLabel = '限流或临时不可用的重试次数'
const generalSections = [
  { name: '阅读显示', id: 'reading-settings' },
  { name: '人物资料', id: 'character-settings' },
  { name: '章节过滤', id: 'chapter-filter-settings' },
  { name: '自动处理', id: 'automatic-settings' },
  { name: '试验功能', id: 'experimental-settings' },
  { name: '应用配置', id: 'application-settings' },
]

async function expectSimplifiedApplicationSettings(page: Page) {
  const section = page.getByRole('region', { name: '应用配置', exact: true })
  await expect(section.getByLabel(applicationRetryLabel, { exact: true })).toHaveValue(/\d+/)
  for (const name of ['凭据', '导入与EPUB安全限制', '离线测试（高级）']) {
    await expect(section.getByRole('heading', { name, exact: true, includeHidden: true })).toHaveCount(0)
  }
  for (const name of [
    '模型密钥保存方式', /^导入文件大小上限/, 'EPUB内部文件数上限', /^EPUB解压总大小上限/,
    /^EPUB单个内部文件大小上限/, 'EPUB正文文档数上限', '启用离线测试提供方',
    '离线测试标注方式', '离线测试错误脚本', '网页文件目录', '允许访问接口的网页来源',
  ]) {
    await expect(section.getByLabel(name, { exact: true })).toHaveCount(0)
  }
  await expect(section.getByRole('button', { name: '重新读取应用配置', exact: true })).toHaveCount(0)
}

async function expectHeaderActions(page: Page) {
  const navigation = page.getByTestId('settings-navigation')
  const categories = page.getByRole('navigation', { name: '通用设置分区' })
  const actions = page.getByTestId('settings-actions')
  await expect(navigation).toHaveCSS('position', 'sticky')
  await expect(categories).toBeInViewport({ ratio: 1 })
  await expect(actions).toHaveCount(1)
  await expect(actions).toBeInViewport({ ratio: 1 })
  const save = page.getByRole('button', { name: '保存设置', exact: true })
  await expect(save).toHaveCount(1)
  await expect(save).toBeInViewport({ ratio: 1 })
  const bounds = await actions.evaluate(element => ({
    left: element.getBoundingClientRect().left,
    top: element.getBoundingClientRect().top,
    bottom: element.getBoundingClientRect().bottom,
    viewportWidth: window.innerWidth,
    viewportHeight: window.innerHeight,
  }))
  const navBounds = await categories.evaluate(element => ({
    right: element.getBoundingClientRect().right,
    bottom: element.getBoundingClientRect().bottom,
  }))
  expect(bounds.top).toBeGreaterThanOrEqual(0)
  expect(bounds.bottom).toBeLessThanOrEqual(bounds.viewportHeight)
  if (bounds.viewportWidth >= 1024) expect(bounds.left).toBeGreaterThanOrEqual(navBounds.right - 1)
  else expect(bounds.left >= navBounds.right - 1 || bounds.top >= navBounds.bottom - 1).toBe(true)
}

test.describe('通用设置保存', () => {
  test('分类切换保持滚动位置，各面板间距一致，用途提示位于导航内', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 600 })
    await page.goto('/settings/general')
    await expect(page.getByTestId('health-ok')).toBeVisible()
    await expect(page.getByLabel(applicationRetryLabel, { exact: true })).not.toHaveValue('')
    const navigation = page.getByTestId('settings-navigation')
    const categories = page.getByRole('navigation', { name: '通用设置分区' })
    await expect.poll(() => page.evaluate(() => window.scrollY)).toBe(0)
    const gaps: number[] = []
    for (const section of generalSections) {
      await categories.getByRole('link', { name: section.name, exact: true }).click()
      await expect(page.locator(`#${section.id}`)).toBeVisible()
      await page.mouse.move(0, 0)
      await expect.poll(() => page.evaluate(() => window.scrollY)).toBe(0)
      const panelTop = await page.locator(`#${section.id}`).evaluate(element => {
        const card = element.matches('.card') ? element : element.querySelector('.card')!
        return card.getBoundingClientRect().top
      })
      const navigationBottom = await navigation.evaluate(element => element.getBoundingClientRect().bottom)
      const gap = panelTop - navigationBottom
      expect(gap, `${section.name}应与导航留出间距`).toBeGreaterThanOrEqual(12)
      gaps.push(gap)
    }
    expect(Math.max(...gaps) - Math.min(...gaps)).toBeLessThanOrEqual(1)

    await categories.getByRole('link', { name: '阅读显示', exact: true }).click()
    await categories.getByRole('link', { name: '自动处理', exact: true }).hover()
    const description = navigation.locator('.ndr-settings-feedback').getByText('设置自动处理与模型偏好', { exact: true })
    await expect(description).toBeVisible()
    const descriptionBox = (await description.boundingBox())!
    const navigationBox = (await navigation.boundingBox())!
    const titleBox = (await page.locator('#reading-settings').getByRole('heading', { name: '阅读显示', exact: true }).boundingBox())!
    expect(descriptionBox.x).toBeGreaterThanOrEqual(navigationBox.x)
    expect(descriptionBox.x + descriptionBox.width).toBeLessThanOrEqual(navigationBox.x + navigationBox.width)
    expect(descriptionBox.y).toBeGreaterThanOrEqual(navigationBox.y)
    expect(descriptionBox.y + descriptionBox.height).toBeLessThanOrEqual(navigationBox.y + navigationBox.height)
    expect(descriptionBox.y + descriptionBox.height).toBeLessThanOrEqual(titleBox.y)
    await expect(categories.getByRole('link', { name: '阅读显示', exact: true })).toHaveAttribute('aria-current', 'page')

    await categories.getByRole('link', { name: '自动处理', exact: true }).click()
    await page.mouse.move(0, 0)
    const automaticMaxScroll = await page.evaluate(() => document.documentElement.scrollHeight - window.innerHeight)
    await categories.getByRole('link', { name: '应用配置', exact: true }).click()
    await page.mouse.move(0, 0)
    const applicationMaxScroll = await page.evaluate(() => document.documentElement.scrollHeight - window.innerHeight)
    const scrollOffset = Math.max(0, Math.min(60, automaticMaxScroll, applicationMaxScroll))
    await page.evaluate(offset => window.scrollTo(0, offset), scrollOffset)
    await expect.poll(() => page.evaluate(() => window.scrollY)).toBe(scrollOffset)
    await categories.getByRole('link', { name: '自动处理', exact: true }).click()
    await page.mouse.move(0, 0)
    await expect.poll(() => page.evaluate(() => window.scrollY)).toBe(scrollOffset)
    await categories.getByRole('link', { name: '应用配置', exact: true }).click()
    await page.mouse.move(0, 0)
    await expect.poll(() => page.evaluate(() => window.scrollY)).toBe(scrollOffset)
  })

  test('六个分类按需展示，切换与历史导航保留草稿，刷新恢复当前分类', async ({ page }) => {
    await page.goto('/settings/general')
    await expect(page.getByTestId('health-ok')).toBeVisible()
    const navigation = page.getByRole('navigation', { name: '通用设置分区' })
    const save = page.getByRole('button', { name: '保存设置', exact: true })
    const panels = page.locator(generalSections.map(section => `#${section.id}:visible`).join(', '))
    await expect(navigation.getByRole('link')).toHaveCount(6)
    await expect(navigation.getByRole('link', { name: '阅读显示', exact: true })).toHaveAttribute('aria-current', 'page')
    await expect(panels).toHaveCount(1)
    await expect(page.getByLabel('正文字号', { exact: true })).toBeVisible()
    await expect(save).toHaveCount(1)
    await expect(save).toBeVisible()
    for (const section of generalSections) {
      const link = navigation.getByRole('link', { name: section.name, exact: true })
      await expect(link).toHaveAttribute('href', `/settings/general#${section.id}`)
      await expect(link).toHaveAttribute('aria-describedby', `${section.id}-description`)
      await expect(page.locator(`#${section.id}-description`)).not.toBeEmpty()
      await link.click()
      await expect(page).toHaveURL(new RegExp(`#${section.id}$`))
      await expect(link).toHaveAttribute('aria-current', 'page')
      await expect(panels).toHaveCount(1)
      await expect(page.locator(`#${section.id}`)).toBeVisible()
      await expect(save).toHaveCount(1)
      await expect(save).toBeVisible()
      await expect(page.getByTestId('settings-actions')).toHaveCount(1)
      if (section.id === 'application-settings') await expect(page.getByRole('button', { name: '保存并重启', exact: true })).toBeVisible()
      else await expect(page.getByRole('button', { name: '保存并重启', exact: true })).toBeHidden()
    }
    await navigation.getByRole('link', { name: '阅读显示', exact: true }).hover()
    const categoryDescription = page.getByTestId('settings-navigation').locator('.ndr-settings-feedback').getByText('调整正文的阅读体验', { exact: true })
    await expect(categoryDescription).toBeVisible()
    await page.mouse.move(0, 0)
    await expect(categoryDescription).toBeHidden()

    await navigation.getByRole('link', { name: '阅读显示', exact: true }).click()
    await page.getByLabel('正文字号', { exact: true }).fill('24')
    await navigation.getByRole('link', { name: '自动处理', exact: true }).click()
    await page.getByTestId('processing-thinking-mode').selectOption('enabled')
    await page.getByTestId('processing-thinking-effort').selectOption('high')
    await page.goBack()
    await expect(page).toHaveURL(/#reading-settings$/)
    await expect(page.getByLabel('正文字号', { exact: true })).toHaveValue('24')
    await expect(panels).toHaveCount(1)
    await page.goForward()
    await expect(page).toHaveURL(/#automatic-settings$/)
    await expect(page.getByTestId('processing-thinking-effort')).toHaveValue('high')
    await expect(panels).toHaveCount(1)

    await navigation.getByRole('link', { name: '应用配置', exact: true }).click()
    const retryCount = page.getByLabel(applicationRetryLabel, { exact: true })
    await expect(retryCount).not.toHaveValue('')
    const originalRetries = await retryCount.inputValue()
    const draftRetries = String(Number(originalRetries) === 20 ? 19 : Number(originalRetries) + 1)
    await retryCount.fill(draftRetries)
    await navigation.getByRole('link', { name: '阅读显示', exact: true }).click()
    await expect(page.getByLabel('正文字号', { exact: true })).toHaveValue('24')
    await navigation.getByRole('link', { name: '应用配置', exact: true }).click()
    await expect(retryCount).toHaveValue(draftRetries)
    await expect(save).toHaveCount(1)
    await expect(save).toBeVisible()
    await page.reload()
    await expect(page).toHaveURL(/#application-settings$/)
    await expect(navigation.getByRole('link', { name: '应用配置', exact: true })).toHaveAttribute('aria-current', 'page')
    await expect(retryCount).toHaveValue(originalRetries)
    await expect(panels).toHaveCount(1)
    await page.goBack()
    await expect(page).toHaveURL(/#reading-settings$/)
    await expect(page.getByLabel('正文字号', { exact: true })).toHaveValue('16')
  })

  test('阅读和思考配置编辑后保存才生效，重新加载后保留', async ({ page }) => {
    await page.goto('/settings/general#reading-settings')
    await page.getByLabel('正文字号', { exact: true }).fill('22')
    await page.getByRole('navigation', { name: '通用设置分区' }).getByRole('link', { name: '自动处理', exact: true }).click()
    await page.getByTestId('processing-thinking-mode').selectOption('enabled')
    await page.getByTestId('processing-thinking-effort').selectOption('high')
    expect(await page.evaluate(() => JSON.parse(localStorage.getItem('ndr:general-settings:v1') ?? '{}').fontSize)).not.toBe(22)
    expect(await page.evaluate(() => JSON.parse(localStorage.getItem('ndr:processing-preferences:v1') ?? '{}').thinkingMode)).not.toBe('enabled')
    await page.getByRole('button', { name: '保存设置', exact: true }).click()
    await expect(page.getByText('阅读与处理设置已保存。', { exact: true })).toBeVisible()
    await page.reload()
    await expect(page.getByTestId('processing-thinking-mode')).toHaveValue('enabled')
    await expect(page.getByTestId('processing-thinking-effort')).toHaveValue('high')
    await page.getByRole('navigation', { name: '通用设置分区' }).getByRole('link', { name: '阅读显示', exact: true }).click()
    await expect(page.getByLabel('正文字号', { exact: true })).toHaveValue('22')
    page.once('dialog', dialog => dialog.accept())
    await page.getByRole('button', { name: '恢复阅读显示默认值', exact: true }).click()
    await expect(page.getByLabel('正文字号', { exact: true })).toHaveValue('16')
    await page.getByRole('navigation', { name: '通用设置分区' }).getByRole('link', { name: '自动处理', exact: true }).click()
    await expect(page.getByTestId('processing-thinking-mode')).toHaveValue('enabled')
    expect(await page.evaluate(() => JSON.parse(localStorage.getItem('ndr:general-settings:v1') ?? '{}').fontSize)).toBe(22)
    await page.getByRole('button', { name: '保存设置', exact: true }).click()
    await page.reload()
    await expect(page.getByTestId('processing-thinking-effort')).toHaveValue('high')
    await page.getByRole('navigation', { name: '通用设置分区' }).getByRole('link', { name: '阅读显示', exact: true }).click()
    await expect(page.getByLabel('正文字号', { exact: true })).toHaveValue('16')
  })

  test('试验子功能独立选择，跨分类保存并在关闭和重新开启总开关后保留', async ({ page }) => {
    await page.goto('/settings/general#experimental-settings')
    const navigation = page.getByRole('navigation', { name: '通用设置分区' })
    const experimental = page.locator('#experimental-settings')
    const master = experimental.getByRole('checkbox', { name: '启用试验功能', exact: true })
    const initialReading = experimental.getByRole('checkbox', { name: '初读与安全导出', exact: true })
    const dialogue = experimental.getByRole('checkbox', { name: '试验对白策略', exact: true })
    const repair = experimental.getByTestId('roster-repair-enabled')
    const strategy = experimental.getByTestId('dialogue-strategy')
    const repairLimit = experimental.getByLabel('人物证据最多修复次数（0 关闭修复）', { exact: true })
    const formatRetries = page.locator('#automatic-settings').getByLabel('校验失败后的重试次数（与对白共用）', { exact: true })
    const save = page.getByRole('button', { name: '保存设置', exact: true })
    const storedGeneral = () => page.evaluate(() => JSON.parse(localStorage.getItem('ndr:general-settings:v1') ?? '{}'))
    const storedProcessing = () => page.evaluate(() => JSON.parse(localStorage.getItem('ndr:processing-preferences:v1') ?? '{}'))

    await expect(master).not.toBeChecked()
    for (const option of [initialReading, dialogue, repair]) {
      await expect(option).toBeVisible()
      await expect(option).not.toBeChecked()
      await expect(option).toBeDisabled()
    }
    await master.check()
    for (const option of [initialReading, dialogue, repair]) {
      await expect(option).toBeEnabled()
      await expect(option).not.toBeChecked()
    }
    await initialReading.check()
    await expect(dialogue).not.toBeChecked()
    await expect(repair).not.toBeChecked()
    await expect(strategy).toBeHidden()
    await repair.check()
    await repairLimit.fill('3')
    await navigation.getByRole('link', { name: '自动处理', exact: true }).click()
    await expect(page.locator('#automatic-settings').getByTestId('dialogue-strategy')).toHaveCount(0)
    await expect(page.locator('#automatic-settings').getByTestId('roster-repair-enabled')).toHaveCount(0)
    await page.getByLabel('最大并发任务数', { exact: true }).fill('3')
    await formatRetries.fill('2')
    await navigation.getByRole('link', { name: '试验功能', exact: true }).click()
    await expect(initialReading).toBeChecked()
    await expect(dialogue).not.toBeChecked()
    await expect(repair).toBeChecked()
    await expect(repairLimit).toHaveValue('3')
    await expect(formatRetries).toHaveValue('2')
    await dialogue.check()
    await strategy.selectOption('complete-blocks')
    await initialReading.uncheck()
    await expect(dialogue).toBeChecked()
    await expect(strategy).toHaveValue('complete-blocks')
    await expect(repair).toBeChecked()
    await initialReading.check()
    await master.uncheck()
    for (const option of [initialReading, dialogue, repair]) {
      await expect(option).toBeChecked()
      await expect(option).toBeDisabled()
    }
    await master.check()
    await expect(strategy).toHaveValue('complete-blocks')
    await expect(repairLimit).toHaveValue('3')
    expect(await storedGeneral()).not.toMatchObject({ enableExperimentalFeatures: true })
    expect(await storedProcessing()).not.toMatchObject({ dialogueStrategy: 'complete-blocks' })
    await navigation.getByRole('link', { name: '自动处理', exact: true }).click()
    await save.click()
    await expect.poll(storedGeneral).toMatchObject({ enableExperimentalFeatures: true, enableInitialReading: true, enableDialogueStrategies: true })
    await expect.poll(storedProcessing).toMatchObject({ concurrency: 3, dialogueStrategy: 'complete-blocks', rosterRepairEnabled: true, maxRosterRepairs: 3, maxFormatRetries: 2 })
    await page.reload()
    await navigation.getByRole('link', { name: '试验功能', exact: true }).click()
    for (const option of [master, initialReading, dialogue, repair]) await expect(option).toBeChecked()
    await expect(strategy).toHaveValue('complete-blocks')
    await expect(repairLimit).toHaveValue('3')

    await dialogue.uncheck()
    await expect(initialReading).toBeChecked()
    await expect(repair).toBeChecked()
    await expect(strategy).toBeHidden()
    await save.click()
    await expect.poll(storedGeneral).toMatchObject({ enableExperimentalFeatures: true, enableInitialReading: true, enableDialogueStrategies: false })
    await expect.poll(storedProcessing).toMatchObject({ dialogueStrategy: 'complete-blocks', rosterRepairEnabled: true })
    await page.reload()
    await expect(dialogue).not.toBeChecked()
    await expect(initialReading).toBeChecked()
    await expect(repair).toBeChecked()
    await dialogue.check()
    await expect(strategy).toHaveValue('complete-blocks')
    await master.uncheck()
    await save.click()
    await expect.poll(storedGeneral).toMatchObject({ enableExperimentalFeatures: false, enableInitialReading: true, enableDialogueStrategies: true })
    await page.reload()
    await expect(master).not.toBeChecked()
    for (const option of [initialReading, dialogue, repair]) {
      await expect(option).toBeChecked()
      await expect(option).toBeDisabled()
    }
    await master.check()
    await expect(strategy).toHaveValue('complete-blocks')
    await expect(repairLimit).toHaveValue('3')
    await expect(formatRetries).toHaveValue('2')
    await save.click()
    await expect.poll(storedGeneral).toMatchObject({ enableExperimentalFeatures: true, enableInitialReading: true, enableDialogueStrategies: true })
    await expect.poll(storedProcessing).toMatchObject({ dialogueStrategy: 'complete-blocks', rosterRepairEnabled: true, maxRosterRepairs: 3, maxFormatRetries: 2 })
  })

  test('已有试验偏好延续初读和对白功能，保存后记录独立开关', async ({ page }) => {
    await page.addInitScript(() => {
      localStorage.setItem('ndr:general-settings:v1', JSON.stringify({ enableExperimentalFeatures: true }))
      localStorage.setItem('ndr:processing-preferences:v1', JSON.stringify({ dialogueStrategy: 'complete-blocks', rosterRepairEnabled: true }))
    })
    await page.goto('/settings/general#experimental-settings')
    await expect(page.getByRole('checkbox', { name: '启用试验功能', exact: true })).toBeChecked()
    await expect(page.getByRole('checkbox', { name: '初读与安全导出', exact: true })).toBeChecked()
    await expect(page.getByRole('checkbox', { name: '试验对白策略', exact: true })).toBeChecked()
    await expect(page.getByTestId('dialogue-strategy')).toHaveValue('complete-blocks')
    await expect(page.getByTestId('roster-repair-enabled')).toBeChecked()
    await page.getByRole('navigation', { name: '通用设置分区' }).getByRole('link', { name: '阅读显示', exact: true }).click()
    await page.getByLabel('正文字号', { exact: true }).fill('20')
    await page.getByRole('button', { name: '保存设置', exact: true }).click()
    await expect.poll(() => page.evaluate(() => JSON.parse(localStorage.getItem('ndr:general-settings:v1') ?? '{}'))).toMatchObject({
      enableExperimentalFeatures: true, enableInitialReading: true, enableDialogueStrategies: true, fontSize: 20,
    })
  })

  test('统一保存跨分类的阅读、处理与应用草稿，应用配置显示待重启', async ({ page }) => {
    // Only GET reads the isolated server. PATCH is intercepted: never write the user's config.
    let saved: { values: Record<string, unknown> } | undefined
    await page.route('**/api/settings/application', async route => {
      if (route.request().method() === 'PATCH') {
        saved = route.request().postDataJSON()
      }
      if (saved) {
        const response = await route.fetch({ method: 'GET', postData: undefined })
        const payload = await response.json()
        payload.data.fields.find((field: { key: string }) => field.key === 'rate_limit_max_retries').value = saved.values.rate_limit_max_retries
        payload.data.restart_required = ['rate_limit_max_retries']
        payload.data.revision = 'browser-test-only'
        await route.fulfill({ json: payload })
      } else {
        await route.continue()
      }
    })
    await page.goto('/settings/general')
    const navigation = page.getByRole('navigation', { name: '通用设置分区' })
    await page.getByLabel('正文字号', { exact: true }).fill('21')
    await navigation.getByRole('link', { name: '自动处理', exact: true }).click()
    await page.getByTestId('processing-thinking-mode').selectOption('enabled')
    await page.getByTestId('processing-thinking-effort').selectOption('medium')
    await navigation.getByRole('link', { name: '应用配置', exact: true }).click()
    const section = page.getByRole('region', { name: '应用配置', exact: true })
    const retryCount = section.getByLabel(applicationRetryLabel, { exact: true })
    await expectSimplifiedApplicationSettings(page)
    await expect(retryCount).toHaveValue('2')
    await expect(retryCount).toHaveAttribute('max', '20')
    await retryCount.fill('3')
    expect(saved).toBeUndefined()
    expect(await page.evaluate(() => JSON.parse(localStorage.getItem('ndr:general-settings:v1') ?? '{}').fontSize)).not.toBe(21)
    expect(await page.evaluate(() => JSON.parse(localStorage.getItem('ndr:processing-preferences:v1') ?? '{}').thinkingMode)).not.toBe('enabled')
    await navigation.getByRole('link', { name: '阅读显示', exact: true }).click()
    await page.getByRole('button', { name: '保存设置', exact: true }).click()
    await expect.poll(() => saved?.values).toEqual({ rate_limit_max_retries: 3 })
    await expect.poll(() => page.evaluate(() => JSON.parse(localStorage.getItem('ndr:general-settings:v1') ?? '{}').fontSize)).toBe(21)
    await expect.poll(() => page.evaluate(() => JSON.parse(localStorage.getItem('ndr:processing-preferences:v1') ?? '{}'))).toMatchObject({ thinkingMode: 'enabled', thinkingEffort: 'medium' })
    await navigation.getByRole('link', { name: '应用配置', exact: true }).click()
    await expect(page.getByText(/已保存，重启后生效：限流或临时不可用的重试次数/)).toBeVisible()
    await expect(retryCount).toHaveValue('3')
    await expectSimplifiedApplicationSettings(page)
    await expect(page.getByRole('button', { name: '保存设置', exact: true })).toBeDisabled()
    await page.reload()
    await expect(retryCount).toHaveValue('3')
    await expectSimplifiedApplicationSettings(page)
    await expect(page.getByRole('button', { name: '保存设置', exact: true })).toBeDisabled()
    await page.setViewportSize({ width: 390, height: 844 })
    await expect(retryCount).toBeVisible()
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
    await page.emulateMedia({ colorScheme: 'dark' })
    await expect(page.getByRole('button', { name: '保存并重启', exact: true })).toBeVisible()
  })

  test('应用配置读取失败时可重试，成功后显示精简配置', async ({ page }) => {
    let failRead = true
    await page.route('**/api/settings/application', async route => {
      if (failRead && route.request().method() === 'GET') {
        await route.fulfill({ status: 503, json: { error: { code: 'TEMPORARILY_UNAVAILABLE', message: '测试读取失败' } } })
      } else await route.continue()
    })
    await page.goto('/settings/general#application-settings')
    const section = page.getByRole('region', { name: '应用配置', exact: true })
    await expect(section.getByText(/应用配置读取失败：测试读取失败/)).toBeVisible()
    const retry = section.getByRole('button', { name: '重新读取应用配置', exact: true })
    await expect(retry).toBeEnabled()
    failRead = false
    await retry.click()
    await expectSimplifiedApplicationSettings(page)
  })
})

for (const width of [320, 390, 768, 1440]) {
  for (const colorScheme of ['light', 'dark'] as const) {
    test(`通用设置分类与顶部保存导航：${width}px ${colorScheme}`, async ({ page }, testInfo) => {
      await page.setViewportSize({ width, height: 844 })
      await page.emulateMedia({ colorScheme })
      await page.goto('/settings/general')
      await expect(page.getByTestId('health-ok')).toBeVisible()
      const navigation = page.getByTestId('settings-navigation')
      const categories = page.getByRole('navigation', { name: '通用设置分区' })
      await expect(categories.getByRole('link')).toHaveCount(6)
      await expect(categories.getByRole('link', { name: '阅读显示', exact: true })).toHaveAttribute('aria-current', 'page')
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
      await expect(page.getByLabel('正文字号', { exact: true })).toBeVisible()
      await expect(page.getByText(/^正在读取.+…$/)).toHaveCount(0)
      await page.mouse.move(0, 0)
      await expectHeaderActions(page)
      const reading = testInfo.outputPath('settings-reading.png')
      await page.screenshot({ path: reading })
      await testInfo.attach('默认阅读显示与顶部保存', { path: reading, contentType: 'image/png' })
      await expect(page.getByRole('button', { name: /说明$/ })).toHaveCount(0)
      for (const field of [
        { name: '正文字号', meanings: [/保存/, /阅读|预览/] },
        { name: '正文行距', meanings: [/保存/, /阅读|预览/] },
        { name: '显示待确认对白标记', meanings: [/待确认/, /阅读|预览/] },
        { name: '双击目录章节名切换完成状态', meanings: [/未处理/, /已完成/] },
      ]) {
        const control = page.getByLabel(field.name, { exact: true })
        await expect(control).toHaveAttribute('title', /\S.+/)
        for (const meaning of field.meanings) await expect(control).toHaveAttribute('title', meaning)
      }

      await categories.getByRole('link', { name: '自动处理', exact: true }).click()
      const automatic = page.locator('#automatic-settings')
      await automatic.getByRole('checkbox', { name: '阅读时自动处理章节（会调用模型）', exact: true }).check()
      await automatic.getByTestId('processing-thinking-mode').selectOption('disabled')
      await expect(automatic.getByTestId('processing-thinking-effort')).toBeDisabled()
      await expect(automatic.getByText(/思考已关闭，无法调整思考强度/)).toBeVisible()
      await expect(automatic.getByText(/处理当前章及后续章节，可能产生费用/)).toBeVisible()
      const automaticLayout = await automatic.evaluate(element => {
        const bounds = (node: Element) => {
          const rectangle = node.getBoundingClientRect()
          return { left: rectangle.left, top: rectangle.top, width: rectangle.width }
        }
        return {
          heading: bounds(element.querySelector('h3')!),
          groupHeadings: [...element.querySelectorAll('h4')].map(bounds),
          thinking: [...element.querySelectorAll('.ndr-thinking-fields > .ndr-field')].map(bounds),
          parameters: [...element.querySelectorAll('.ndr-settings-fields > .ndr-field')].map(bounds),
        }
      })
      expect(automaticLayout.thinking).toHaveLength(3)
      expect(automaticLayout.parameters).toHaveLength(4)
      for (const content of [...automaticLayout.groupHeadings, automaticLayout.thinking[0], automaticLayout.parameters[0]]) {
        expect(Math.abs(content.left - automaticLayout.heading.left)).toBeLessThanOrEqual(1)
      }
      if (width >= 768) {
        for (let column = 0; column < 3; column += 1) {
          expect(Math.abs(automaticLayout.thinking[column].left - automaticLayout.parameters[column].left)).toBeLessThanOrEqual(1)
          expect(Math.abs(automaticLayout.thinking[column].width - automaticLayout.parameters[column].width)).toBeLessThanOrEqual(1)
          expect(Math.abs(automaticLayout.thinking[column].top - automaticLayout.thinking[0].top)).toBeLessThanOrEqual(1)
          expect(Math.abs(automaticLayout.parameters[column].top - automaticLayout.parameters[0].top)).toBeLessThanOrEqual(1)
          if (column > 0) expect(automaticLayout.thinking[column].left).toBeGreaterThan(automaticLayout.thinking[column - 1].left)
        }
      }
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
      await page.mouse.move(0, 0)
      await page.evaluate(() => window.scrollTo(0, 0))
      const automaticScreenshot = testInfo.outputPath('settings-automatic.png')
      await page.screenshot({ path: automaticScreenshot, fullPage: true })
      await testInfo.attach('自动处理列对齐与禁用提示', { path: automaticScreenshot, contentType: 'image/png' })

      await categories.getByRole('link', { name: '试验功能', exact: true }).click()
      const experimental = page.locator('#experimental-settings')
      await expect(experimental.getByRole('checkbox', { name: '初读与安全导出', exact: true })).toBeDisabled()
      await expect(experimental.getByText('先启用试验功能，再选择下方功能。', { exact: true })).toBeVisible()
      await experimental.getByRole('checkbox', { name: '启用试验功能', exact: true }).check()
      await experimental.getByRole('checkbox', { name: '初读与安全导出', exact: true }).check()
      await experimental.getByRole('checkbox', { name: '试验对白策略', exact: true }).check()
      await experimental.getByTestId('dialogue-strategy').selectOption('complete-blocks')
      await experimental.getByTestId('roster-repair-enabled').check()
      await expect(experimental.getByLabel('人物证据最多修复次数（0 关闭修复）', { exact: true })).toBeVisible()
      await expect(experimental.getByText(/额外调用会增加用量与等待/)).toBeVisible()
      expect(await page.evaluate(() => JSON.parse(localStorage.getItem('ndr:general-settings:v1') ?? '{}').enableExperimentalFeatures)).not.toBe(true)
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
      await page.mouse.move(0, 0)
      await page.evaluate(() => window.scrollTo(0, 0))
      const experimentalScreenshot = testInfo.outputPath('settings-experimental-enabled.png')
      await page.screenshot({ path: experimentalScreenshot, fullPage: true })
      await testInfo.attach('试验功能全部开启的未保存草稿', { path: experimentalScreenshot, contentType: 'image/png' })

      await categories.getByRole('link', { name: '应用配置', exact: true }).click()
      await expectSimplifiedApplicationSettings(page)
      await expect(page.getByRole('button', { name: /说明$/ })).toHaveCount(0)
      await page.mouse.move(0, 0)
      await expectHeaderActions(page)
      await expect(page.getByRole('button', { name: '保存并重启', exact: true })).toBeInViewport({ ratio: 1 })
      await expect(navigation).toHaveCSS('position', 'sticky')
      const position = await navigation.evaluate(element => ({
        documentTop: element.getBoundingClientRect().top + window.scrollY,
        maxScroll: Math.max(0, document.documentElement.scrollHeight - window.innerHeight),
        stickyTop: Number.parseFloat(getComputedStyle(element).top),
      }))
      const scrollTarget = Math.min(Math.ceil(position.documentTop + 300), position.maxScroll)
      await page.evaluate(target => window.scrollTo(0, target), scrollTarget)
      await expect.poll(() => page.evaluate(target => Math.abs(window.scrollY - target), scrollTarget)).toBeLessThanOrEqual(1)
      const expectedNavigationTop = Math.max(position.stickyTop, position.documentTop - scrollTarget)
      await expect.poll(() => navigation.evaluate((element, expectedTop) =>
        Math.abs(element.getBoundingClientRect().top - expectedTop), expectedNavigationTop,
      )).toBeLessThanOrEqual(2)
      await expect(categories.getByRole('link', { name: '应用配置', exact: true })).toHaveAttribute('aria-current', 'page')
      await expectHeaderActions(page)
      await expect(page.getByRole('button', { name: '保存并重启', exact: true })).toBeInViewport({ ratio: 1 })
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
      const selected = testInfo.outputPath('settings-application-sticky.png')
      await page.screenshot({ path: selected })
      await testInfo.attach('应用配置与吸顶导航', { path: selected, contentType: 'image/png' })
    })
  }
}

test.describe('模型配置', () => {
  test('新建、编辑、清除密钥与删除配置', async ({ page }) => {
    const name = `E2E 网关 ${Date.now()}`
    await page.goto('/settings/models')
    await expect(page.getByTestId('profile-protocol')).toHaveAttribute('title', /OpenAI 接口格式/)

    await page.getByTestId('profile-name').fill(name)
    await page.getByTestId('profile-base-url').fill('https://api.example.com/v1')
    await page.getByTestId('profile-model').fill('e2e-model')
    await page.getByTestId('profile-api-key').fill(SECRET)
    await page.getByTestId('profile-thinking-mode').selectOption('adaptive')
    await page.getByTestId('profile-thinking-effort').selectOption('high')
    await page.getByTestId('profile-save').click()

    const card = await revealProfile(page, name)
    await expect(card).toContainText('已保存密钥')
    await expect(card).toContainText('会话（Session）')
    // 密钥绝不回显在页面上
    await expect(page.locator('body')).not.toContainText(SECRET)

    // 编辑（保持不变 → 密钥仍在）
    await card.getByRole('button', { name: '编辑' }).click()
    await expect(page.getByTestId('key-action-keep')).toBeChecked()
    await expect(page.getByTestId('profile-thinking-mode')).toHaveValue('adaptive')
    await expect(page.getByTestId('profile-thinking-effort')).toHaveValue('high')
    await page.getByTestId('profile-model').fill('e2e-model-2')
    await page.getByTestId('profile-save').click()
    await expect(page.getByTestId('model-saved-panel')).toBeVisible()
    await expect(card).toContainText('e2e-model-2')
    await expect(card).toContainText('已保存密钥')

    // 清除密钥
    await card.getByRole('button', { name: '编辑' }).click()
    await page.getByTestId('profile-thinking-mode').selectOption('disabled')
    await expect(page.getByTestId('profile-thinking-effort')).toBeDisabled()
    await page.getByTestId('key-action-remove').check()
    await page.getByTestId('profile-save').click()
    await expect(page.getByTestId('model-saved-panel')).toBeVisible()
    await expect(card).toContainText('未保存密钥')
    await card.getByRole('button', { name: '编辑' }).click()
    await expect(page.getByTestId('profile-thinking-mode')).toHaveValue('disabled')
    await expect(page.getByTestId('profile-thinking-effort')).toHaveValue('default')
    await page.getByTestId('profile-cancel').click()
    await expect(page.getByTestId('model-saved-panel')).toBeVisible()

    // 删除
    await card.getByRole('button', { name: '删除' }).click()
    await expect(card).toHaveCount(0)
    await expect(page.getByTestId('settings-notice')).toContainText('凭据引用也已清理')
  })

  test('把完整端点填进 Base URL 会得到可理解的错误', async ({ page }) => {
    await page.goto('/settings/models')
    await page.getByTestId('profile-name').fill('错误端点')
    await page.getByTestId('profile-base-url').fill('https://api.example.com/v1/chat/completions')
    await page.getByTestId('profile-model').fill('e2e-model')
    await page.getByTestId('profile-save').click()

    await expect(page.getByTestId('settings-error')).toContainText('API 根路径')
  })

  test('本地无鉴权服务可以显式选择「不需要密钥」', async ({ page }) => {
    const name = `本地网关 ${Date.now()}`
    await page.goto('/settings/models')
    await page.getByTestId('profile-name').fill(name)
    await page.getByTestId('profile-base-url').fill('http://127.0.0.1:11434/v1')
    await page.getByTestId('profile-model').fill('local-model')
    await page.getByTestId('credential-mode-none').check()
    await page.getByTestId('profile-save').click()

    const card = await revealProfile(page, name)
    await expect(card).toContainText('未保存密钥')
    await expect(card).toContainText('不使用密钥')
    await card.getByRole('button', { name: '删除' }).click()
    await expect(card).toHaveCount(0)
  })

  test('连接测试：FakeProvider 明确标注为测试适配器', async ({ page }) => {
    const name = `测试提供方 ${Date.now()}`
    await page.goto('/settings/models')

    await page.getByTestId('profile-name').fill(name)
    await page.selectOption('[data-testid=profile-protocol]', 'fake-provider')
    await page.getByTestId('profile-base-url').fill('http://127.0.0.1:1')
    await page.getByTestId('profile-model').fill('fake-model')
    await page.getByTestId('profile-save').click()

    const card = await revealProfile(page, name)
    await expect(card).toContainText(name)
    await card.getByRole('button', { name: '测试连接' }).click()

    const result = page.getByTestId('connection-result')
    await expect(result).toContainText('连接成功')
    await result.getByText('连接详情', { exact: true }).click()
    await expect(result).toContainText('fake-provider')
    await expect(result).toContainText('未知（模型服务未返回用量）')
    await expect(result.getByText('用量：未知（模型服务未返回用量）', { exact: true })).toBeVisible()
    // 明确标注这是测试适配器，不是真实模型
    await expect(page.getByTestId('fake-provider-warning')).toContainText('本地模拟测试完成，结果用于验证界面流程')

    // 清理：只删除本用例创建的配置
    await card.getByRole('button', { name: '删除' }).click()
    await expect(card).toHaveCount(0)
  })
})
