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
const generalSections = [
  { name: '阅读显示', id: 'reading-settings' },
  { name: '人物资料', id: 'character-settings' },
  { name: '章节过滤', id: 'chapter-filter-settings' },
  { name: '自动处理', id: 'automatic-settings' },
  { name: '试验功能', id: 'experimental-settings' },
  { name: '应用配置', id: 'application-settings' },
]

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
    await expect(page.getByText('调整正文的阅读体验', { exact: true })).toBeVisible()
    await page.mouse.move(0, 0)
    await expect(page.getByText('调整正文的阅读体验', { exact: true })).toBeHidden()

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
    const importLimit = page.getByLabel('导入文件大小上限（MB）', { exact: true })
    await expect(importLimit).not.toHaveValue('')
    const originalLimit = await importLimit.inputValue()
    const draftLimit = String(Number(originalLimit) + 1)
    await importLimit.fill(draftLimit)
    await navigation.getByRole('link', { name: '阅读显示', exact: true }).click()
    await expect(page.getByLabel('正文字号', { exact: true })).toHaveValue('24')
    await navigation.getByRole('link', { name: '应用配置', exact: true }).click()
    await expect(importLimit).toHaveValue(draftLimit)
    await expect(save).toHaveCount(1)
    await expect(save).toBeVisible()
    await page.reload()
    await expect(page).toHaveURL(/#application-settings$/)
    await expect(navigation.getByRole('link', { name: '应用配置', exact: true })).toHaveAttribute('aria-current', 'page')
    await expect(importLimit).toHaveValue(originalLimit)
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

  test('统一保存跨分类的阅读、处理与应用草稿，应用配置显示待重启', async ({ page }) => {
    // Only GET reads the isolated server. PATCH is intercepted: never write the user's config.
    let saved: Record<string, unknown> | undefined
    await page.route('**/api/settings/application', async route => {
      if (route.request().method() === 'PATCH') {
        saved = route.request().postDataJSON()
        const response = await route.fetch({ method: 'GET', postData: undefined })
        const payload = await response.json()
        payload.data.fields.find((field: { key: string }) => field.key === 'max_import_bytes').value = 104857600
        payload.data.restart_required = ['max_import_bytes']
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
    await expect(section.getByLabel('导入文件大小上限（MB）', { exact: true })).toBeVisible()
    await section.getByLabel('导入文件大小上限（MB）', { exact: true }).fill('100')
    expect(saved).toBeUndefined()
    expect(await page.evaluate(() => JSON.parse(localStorage.getItem('ndr:general-settings:v1') ?? '{}').fontSize)).not.toBe(21)
    expect(await page.evaluate(() => JSON.parse(localStorage.getItem('ndr:processing-preferences:v1') ?? '{}').thinkingMode)).not.toBe('enabled')
    await navigation.getByRole('link', { name: '阅读显示', exact: true }).click()
    await page.getByRole('button', { name: '保存设置', exact: true }).click()
    await expect.poll(() => saved).toMatchObject({ values: { max_import_bytes: 104857600 } })
    await expect.poll(() => page.evaluate(() => JSON.parse(localStorage.getItem('ndr:general-settings:v1') ?? '{}').fontSize)).toBe(21)
    await expect.poll(() => page.evaluate(() => JSON.parse(localStorage.getItem('ndr:processing-preferences:v1') ?? '{}'))).toMatchObject({ thinkingMode: 'enabled', thinkingEffort: 'medium' })
    await navigation.getByRole('link', { name: '应用配置', exact: true }).click()
    await expect(page.getByText(/已保存，重启后生效：导入文件大小上限/)).toBeVisible()
    await expect(section.getByLabel('导入文件大小上限（MB）', { exact: true })).toHaveValue('100')
    await expect(page.getByRole('button', { name: '保存设置', exact: true })).toBeDisabled()
    await page.setViewportSize({ width: 390, height: 844 })
    await expect(section.getByLabel('导入文件大小上限（MB）', { exact: true })).toBeVisible()
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
    await page.emulateMedia({ colorScheme: 'dark' })
    await expect(page.getByRole('button', { name: '保存并重启', exact: true })).toBeVisible()
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

      await categories.getByRole('link', { name: '应用配置', exact: true }).click()
      await expect(page.getByLabel('导入文件大小上限（MB）', { exact: true })).not.toHaveValue('')
      await expect(page.getByRole('button', { name: /说明$/ })).toHaveCount(0)
      await page.mouse.move(0, 0)
      await expectHeaderActions(page)
      await expect(page.getByRole('button', { name: '保存并重启', exact: true })).toBeInViewport({ ratio: 1 })
      await expect(navigation).toHaveCSS('position', 'sticky')
      const position = await navigation.evaluate(element => ({
        documentTop: element.getBoundingClientRect().top + window.scrollY,
        maxScroll: document.documentElement.scrollHeight - window.innerHeight,
      }))
      const scrollTarget = Math.min(position.documentTop + 300, position.maxScroll)
      expect(scrollTarget).toBeGreaterThan(position.documentTop + 50)
      await page.evaluate(target => window.scrollTo(0, target), scrollTarget)
      await expect.poll(() => page.evaluate(() => window.scrollY)).toBeGreaterThan(position.documentTop + 50)
      await expect.poll(() => navigation.evaluate(element =>
        Math.abs(element.getBoundingClientRect().top - Number.parseFloat(getComputedStyle(element).top)),
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
    await expect(page.getByTestId('protocol-capabilities')).toContainText('OpenAI 接口格式')

    await page.getByTestId('profile-name').fill(name)
    await page.getByTestId('profile-base-url').fill('https://api.example.com/v1')
    await page.getByTestId('profile-model').fill('e2e-model')
    await page.getByTestId('profile-api-key').fill(SECRET)
    await page.getByTestId('profile-thinking-mode').selectOption('adaptive')
    await page.getByTestId('profile-thinking-effort').selectOption('high')
    await page.getByTestId('profile-save').click()

    const card = await revealProfile(page, name)
    await expect(card).toContainText('已保存密钥')
    await expect(card).toContainText('仅本次会话')
    // 密钥绝不回显在页面上
    await expect(page.locator('body')).not.toContainText(SECRET)

    // 编辑（保持不变 → 密钥仍在）
    await card.getByRole('button', { name: '编辑' }).click()
    await expect(page.getByTestId('key-action-keep')).toBeChecked()
    await expect(page.getByTestId('profile-thinking-mode')).toHaveValue('adaptive')
    await expect(page.getByTestId('profile-thinking-effort')).toHaveValue('high')
    await page.getByTestId('profile-model').fill('e2e-model-2')
    await page.getByTestId('profile-save').click()
    await expect(card).toContainText('e2e-model-2')
    await expect(card).toContainText('已保存密钥')

    // 清除密钥
    await card.getByRole('button', { name: '编辑' }).click()
    await page.getByTestId('profile-thinking-mode').selectOption('disabled')
    await expect(page.getByTestId('profile-thinking-effort')).toBeDisabled()
    await page.getByTestId('key-action-remove').check()
    await page.getByTestId('profile-save').click()
    await expect(card).toContainText('未保存密钥')
    await card.getByRole('button', { name: '编辑' }).click()
    await expect(page.getByTestId('profile-thinking-mode')).toHaveValue('disabled')
    await expect(page.getByTestId('profile-thinking-effort')).toHaveValue('default')
    await page.getByTestId('profile-cancel').click()

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
    await expect(result).toContainText('fake-provider')
    await expect(result).toContainText('未知（模型服务未返回用量）')
    // 明确标注这是测试适配器，不是真实模型
    await expect(page.getByTestId('fake-provider-warning')).toContainText('本地模拟测试完成，结果用于验证界面流程')

    // 清理：只删除本用例创建的配置
    await card.getByRole('button', { name: '删除' }).click()
    await expect(card).toHaveCount(0)
  })
})
