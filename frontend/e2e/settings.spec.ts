import { expect, test } from '@playwright/test'

/**
 * T06/T07：用户不改源码即可配置模型；密钥只在提交时出现，界面与接口都不回显；
 * 连接测试用微型结构化请求，并明确标注测试用适配器。
 * 后端使用 session 凭据后端与显式启用的 FakeProvider（见 playwright.config.ts），不会触碰真实凭据库。
 *
 * 注意：E2E 数据目录在同一次运行里是共享的，其它用例也可能留下配置，
 * 因此这里所有断言都按**配置名**限定到具体卡片，不依赖“列表是空的”。
 */
const SECRET = 'sk-e2e-secret-value'

test.describe('模型配置', () => {
  test('新建、编辑、清除密钥与删除配置', async ({ page }) => {
    const name = `E2E 网关 ${Date.now()}`
    await page.goto('/settings/models')
    await expect(page.getByTestId('protocol-capabilities')).toContainText('json_schema')

    await page.getByTestId('profile-name').fill(name)
    await page.getByTestId('profile-base-url').fill('https://api.example.com/v1')
    await page.getByTestId('profile-model').fill('e2e-model')
    await page.getByTestId('profile-api-key').fill(SECRET)
    await page.getByTestId('profile-save').click()

    const card = page.getByTestId('profile-card').filter({ hasText: name })
    await expect(card).toContainText('已保存密钥')
    await expect(card).toContainText('session')
    // 密钥绝不回显在页面上
    await expect(page.locator('body')).not.toContainText(SECRET)

    // 编辑（保持不变 → 密钥仍在）
    await card.getByRole('button', { name: '编辑' }).click()
    await expect(page.getByTestId('key-action-keep')).toBeChecked()
    await page.getByTestId('profile-model').fill('e2e-model-2')
    await page.getByTestId('profile-save').click()
    await expect(card).toContainText('e2e-model-2')
    await expect(card).toContainText('已保存密钥')

    // 清除密钥
    await card.getByRole('button', { name: '编辑' }).click()
    await page.getByTestId('key-action-remove').check()
    await page.getByTestId('profile-save').click()
    await expect(card).toContainText('未保存密钥')

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

  test('连接测试：FakeProvider 明确标注为测试适配器', async ({ page }) => {
    const name = `测试提供方 ${Date.now()}`
    await page.goto('/settings/models')

    await page.getByTestId('profile-name').fill(name)
    await page.selectOption('[data-testid=profile-protocol]', 'fake-provider')
    await page.getByTestId('profile-base-url').fill('http://127.0.0.1:1')
    await page.getByTestId('profile-model').fill('fake-model')
    await page.getByTestId('profile-save').click()

    const card = page.getByTestId('profile-card').filter({ hasText: name })
    await expect(card).toContainText(name)
    await card.getByRole('button', { name: '测试连接' }).click()

    const result = page.getByTestId('connection-result')
    await expect(result).toContainText('连接成功')
    await expect(result).toContainText('fake-provider')
    await expect(result).toContainText('未知（提供方未返回 usage，不按 0 计）')
    // 明确标注这是测试适配器，不是真实模型
    await expect(page.getByTestId('fake-provider-warning')).toContainText('没有访问任何真实服务')

    // 清理：只删除本用例创建的配置
    await card.getByRole('button', { name: '删除' }).click()
    await expect(card).toHaveCount(0)
  })
})