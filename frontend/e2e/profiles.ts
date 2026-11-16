import { expect, type Page } from '@playwright/test'

/** Reveal a saved model through the same paginated list used by readers. */
export async function revealProfile(page: Page, name: string) {
  const card = page.getByTestId('profile-card').filter({ hasText: name })
  await expect(card).toBeAttached()
  const pagination = page.getByRole('navigation', { name: '模型配置分页' })
  if (!(await card.isVisible())) {
    const previous = pagination.getByRole('button', { name: '上一页', exact: true })
    while (await previous.isEnabled()) await previous.click()
  }
  while (!(await card.isVisible())) {
    const next = pagination.getByRole('button', { name: '下一页', exact: true })
    await expect(next).toBeEnabled()
    await next.click()
  }
  await expect(card).toBeVisible()
  return card
}
