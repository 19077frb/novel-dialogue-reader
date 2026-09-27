import { expect, test } from '@playwright/test'

/**
 * T06：用户不改源码即可配置模型；密钥只在提交时出现，界面与接口都不回显。
 * 后端使用 session 凭据后端（见 playwright.config.ts），不会触碰真实系统凭据库。
 */
const SECRET = 'sk-e2e-secret-value'

test.describe('模型配置', () => {
  test('新建、编辑、清除密钥与删除配置', async ({ page }) => {
    await page.goto('/settings/models')
    await expect(page.getByTestId('profiles-empty')).toBeVisible()
    // 页面明确说明连接测试尚未提供（T07）
    await expect(page.getByText(/连接测试与实际识别调用在 T07 提供/)).toBeVisible()
    await expect(page.getByTestId('protocol-capabilities')).toContainText('json_schema')

    await page.getByTestId('profile-name').fill('E2E 网关')
    await page.getByTestId('profile-base-url').fill('https://api.example.com/v1')
    await page.getByTestId('profile-model').fill('e2e-model')
    await page.getByTestId('profile-api-key').fill(SECRET)
    await page.getByTestId('profile-save').click()

    const card = page.getByTestId('profile-card')
    await expect(card).toContainText('E2E 网关')
    await expect(card).toContainText('已保存密钥')
    await expect(card).toContainText('session')
    // 密钥绝不回显在页面上
    await expect(page.locator('body')).not.toContainText(SECRET)

    // 编辑（保持不变 → 密钥仍在）
    await page.getByRole('button', { name: '编辑' }).click()
    await expect(page.getByTestId('key-action-keep')).toBeChecked()
    await page.getByTestId('profile-model').fill('e2e-model-2')
    await page.getByTestId('profile-save').click()
    await expect(page.getByTestId('profile-card')).toContainText('e2e-model-2')
    await expect(page.getByTestId('profile-card')).toContainText('已保存密钥')

    // 清除密钥
    await page.getByRole('button', { name: '编辑' }).click()
    await page.getByTestId('key-action-remove').check()
    await page.getByTestId('profile-save').click()
    await expect(page.getByTestId('profile-card')).toContainText('未保存密钥')

    // 删除
    await page.getByRole('button', { name: '删除' }).click()
    await expect(page.getByTestId('profiles-empty')).toBeVisible()
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
})