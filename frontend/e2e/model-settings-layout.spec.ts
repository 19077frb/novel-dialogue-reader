import { expect, test, type Page, type TestInfo } from '@playwright/test'

import { revealProfile } from './profiles'

const editorFields = [
  'profile-name', 'profile-protocol', 'profile-base-url', 'profile-model',
  'profile-max_tokens', 'profile-temperature', 'profile-top_p', 'profile-timeout_seconds',
  'profile-thinking-mode', 'profile-thinking-effort', 'credential-mode-session', 'profile-api-key',
] as const

async function expectEditor(page: Page) {
  await expect(page.getByTestId('health-ok')).toBeVisible()
  await expect(page.getByTestId('model-editor-panel')).toBeVisible()
  await expect(page.getByTestId('model-saved-panel')).toBeHidden()
  await expect(page.getByTestId('model-saved-panel')).toBeAttached()
  await expect(page.getByTestId('model-view-editor')).toHaveText('新建配置')
  await expect(page.getByTestId('model-view-saved')).toHaveText('查看配置')
  await expect(page.getByTestId('profile-protocol')).toHaveAttribute('title', /OpenAI/)
  await expect(page.locator('a[href="#profile-editor"], a[href="#profile-generation"], a[href="#profile-thinking"], a[href="#profile-credentials"], a[href="#saved-profiles"]')).toHaveCount(0)
  for (const id of editorFields) await expect(page.getByTestId(id)).toBeVisible()
  for (const mode of ['session', 'system', 'none']) {
    await expect(page.getByTestId(`credential-mode-${mode}`)).toBeVisible()
  }
}

async function screenshot(page: Page, testInfo: TestInfo, name: string, fullPage = false) {
  await page.mouse.move(0, 0)
  const file = testInfo.outputPath(`${name}.png`)
  await page.screenshot({ path: file, fullPage })
  await testInfo.attach(name, { path: file, contentType: 'image/png' })
}

for (const viewport of [
  { width: 1280, height: 720 },
  { width: 1366, height: 768 },
  { width: 1440, height: 900 },
]) {
  for (const colorScheme of ['light', 'dark'] as const) {
    test(`模型新建表单一屏展示全部字段：${viewport.width}×${viewport.height} ${colorScheme}`, async ({ page }, testInfo) => {
      await page.setViewportSize(viewport)
      await page.emulateMedia({ colorScheme })
      await page.goto('/settings/models')
      await expectEditor(page)
      for (const id of [...editorFields, 'credential-mode-system', 'credential-mode-none', 'profile-save', 'profile-test-draft']) {
        await expect(page.getByTestId(id), `${id} 完整位于初始视口`).toBeInViewport({ ratio: 1 })
      }
      for (const id of editorFields) {
        await expect(page.getByTestId(id).locator('xpath=..'), `${id} 标签完整位于初始视口`).toBeInViewport({ ratio: 1 })
      }
      const dimensions = await page.evaluate(() => ({
        height: document.documentElement.scrollHeight,
        width: document.documentElement.scrollWidth,
        viewportHeight: window.innerHeight,
        viewportWidth: window.innerWidth,
        scrollY: window.scrollY,
      }))
      expect(dimensions.height).toBeLessThanOrEqual(dimensions.viewportHeight + 1)
      expect(dimensions.width).toBeLessThanOrEqual(dimensions.viewportWidth + 1)
      expect(dimensions.scrollY).toBe(0)
      await screenshot(page, testInfo, 'model-editor-desktop')
      if (viewport.width === 1280 && colorScheme === 'light') {
        await page.getByTestId('profile-thinking-mode').selectOption('disabled')
        await expect(page.getByTestId('profile-thinking-effort')).toBeDisabled()
        await expect(page.getByText(/默认思考已关闭/)).toBeVisible()
        for (const mode of ['system', 'none']) {
          await page.getByTestId(`credential-mode-${mode}`).check()
          await expect(page.getByTestId('profile-save')).toBeInViewport({ ratio: 1 })
          await expect(page.getByTestId('profile-test-draft')).toBeInViewport({ ratio: 1 })
          expect(await page.evaluate(() => document.documentElement.scrollHeight)).toBeLessThanOrEqual(viewport.height + 1)
          expect(await page.evaluate(() => window.scrollY)).toBe(0)
        }
        await screenshot(page, testInfo, 'model-editor-disabled-thinking')
      }
    })
  }
}

for (const width of [320, 390, 768]) {
  for (const colorScheme of ['light', 'dark'] as const) {
    test(`模型新建表单窄屏自然重排：${width}px ${colorScheme}`, async ({ page }, testInfo) => {
      await page.setViewportSize({ width, height: 844 })
      await page.emulateMedia({ colorScheme })
      await page.goto('/settings/models')
      await expectEditor(page)
      expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width + 1)
      const coordinates = await page.getByTestId('model-editor-panel').evaluate(panel => {
        const inputBounds = [...panel.querySelectorAll<HTMLInputElement | HTMLSelectElement>('input, select')]
          .filter(input => input.getBoundingClientRect().width > 0)
          .map(input => ({ left: input.getBoundingClientRect().left, right: input.getBoundingClientRect().right }))
        return { panel: panel.getBoundingClientRect().toJSON(), inputBounds }
      })
      for (const bounds of coordinates.inputBounds) {
        expect(bounds.left).toBeGreaterThanOrEqual(coordinates.panel.left)
        expect(bounds.right).toBeLessThanOrEqual(coordinates.panel.right)
      }
      await page.getByTestId('profile-save').scrollIntoViewIfNeeded()
      await expect(page.getByTestId('profile-save')).toBeInViewport({ ratio: 1 })
      await page.getByTestId('profile-test-draft').scrollIntoViewIfNeeded()
      await expect(page.getByTestId('profile-test-draft')).toBeInViewport({ ratio: 1 })
      await screenshot(page, testInfo, 'model-editor-narrow', true)
    })
  }
}

test('双视图保留草稿，离线测试后保存进入列表，编辑可取消', async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 1366, height: 768 })
  await page.goto('/settings/models')
  await expectEditor(page)
  const mutations: Array<{ method: string; url: string; data: unknown }> = []
  page.on('request', request => {
    if (/\/api\/model-profiles(?:\/|$)/.test(new URL(request.url()).pathname) && request.method() !== 'GET') {
      mutations.push({ method: request.method(), url: new URL(request.url()).pathname, data: request.postDataJSON() })
    }
  })
  const name = `双视图离线配置 ${Date.now()}`
  const secret = 'sk-e2e-view-draft-only'
  const values = {
    'profile-name': name,
    'profile-base-url': 'http://127.0.0.1:1',
    'profile-model': 'fake-model-two-views',
    'profile-max_tokens': '4096',
    'profile-temperature': '0.2',
    'profile-top_p': '0.9',
    'profile-timeout_seconds': '30',
    'profile-api-key': secret,
  }
  for (const [id, value] of Object.entries(values)) await page.getByTestId(id).fill(value)
  await page.getByTestId('profile-protocol').selectOption('fake-provider')
  await page.getByTestId('profile-thinking-mode').selectOption('adaptive')
  await page.getByTestId('profile-thinking-effort').selectOption('high')
  const originalInput = await page.getByTestId('profile-name').elementHandle()
  await page.getByTestId('model-view-saved').click()
  await expect(page.getByTestId('model-editor-panel')).toBeHidden()
  await expect(page.getByTestId('model-editor-panel')).toBeAttached()
  await expect(page.getByTestId('model-saved-panel')).toBeVisible()
  expect(await originalInput!.evaluate(input => input.isConnected)).toBe(true)
  await expect(page.getByTestId('profile-api-key')).toHaveValue(secret)
  await page.getByTestId('model-view-editor').click()
  for (const [id, value] of Object.entries(values)) await expect(page.getByTestId(id)).toHaveValue(value)
  await expect(page.getByTestId('profile-protocol')).toHaveValue('fake-provider')
  await expect(page.getByTestId('profile-thinking-mode')).toHaveValue('adaptive')
  await expect(page.getByTestId('profile-thinking-effort')).toHaveValue('high')
  expect(mutations).toHaveLength(0)

  await page.getByTestId('profile-test-draft').click()
  await expect(page.getByTestId('connection-result')).toContainText('连接成功')
  await expect(page.getByTestId('fake-provider-warning')).toContainText('本地模拟测试完成')
  expect(mutations).toEqual([expect.objectContaining({
    method: 'POST', url: '/api/model-profiles/test',
    data: { draft: expect.objectContaining({ protocol: 'fake-provider', model: values['profile-model'] }) },
  })])
  await page.getByTestId('profile-save').click()
  await expect(page.getByTestId('model-saved-panel')).toBeVisible()
  await expect(page.getByTestId('model-editor-panel')).toBeHidden()
  await expect(page.getByTestId('profile-api-key')).toHaveValue('')
  const card = await revealProfile(page, name)
  await expect(card).toContainText('已保存密钥')
  await expect(page.getByTestId('settings-notice')).toContainText(name)
  const saveRequest = mutations.find(request => request.url === '/api/model-profiles')
  expect(saveRequest).toMatchObject({
    method: 'POST', data: {
      name, protocol: 'fake-provider', model: values['profile-model'], api_key: secret,
      params: { max_tokens: 4096, temperature: 0.2, top_p: 0.9, timeout_seconds: 30, thinking: { type: 'adaptive' }, reasoning_effort: 'high' },
    },
  })
  await screenshot(page, testInfo, 'model-saved-list')

  await card.getByRole('button', { name: '编辑', exact: true }).click()
  await expect(page.getByTestId('model-editor-panel')).toBeVisible()
  await expect(page.getByTestId('model-view-editor')).toHaveText('编辑配置')
  await expect(page.getByTestId('key-action-keep')).toBeChecked()
  await page.getByTestId('profile-model').fill('取消的模型修改')
  await page.getByTestId('model-view-saved').click()
  await page.getByTestId('model-view-editor').click()
  await expect(page.getByTestId('profile-model')).toHaveValue('取消的模型修改')
  await page.getByTestId('profile-cancel').click()
  await expect(page.getByTestId('model-saved-panel')).toBeVisible()
  await expect(card).toContainText(values['profile-model'])
  await expect(card).not.toContainText('取消的模型修改')
  expect(mutations.filter(request => request.method === 'PATCH')).toHaveLength(0)
  await page.getByTestId('model-view-editor').click()
  await expect(page.getByTestId('profile-name')).toHaveValue('')
  await expect(page.getByTestId('model-view-editor')).toHaveText('新建配置')
  await revealProfile(page, name)
  await card.getByRole('button', { name: '删除', exact: true }).click()
  await expect(card).toHaveCount(0)
})

test('旧模型配置锚点仍能打开对应视图', async ({ page }) => {
  await page.goto('/settings/models#saved-profiles')
  await expect(page.getByTestId('model-saved-panel')).toBeVisible()
  await expect(page.getByTestId('model-editor-panel')).toBeHidden()
  await page.goto('/settings/models#profile-thinking')
  await expectEditor(page)
  await expect(page.getByTestId('profile-thinking-mode')).toBeVisible()
})
