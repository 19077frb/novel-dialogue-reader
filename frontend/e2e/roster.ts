import { expect, type Page } from '@playwright/test'

/**
 * 「本章人物」是逐句归属的前置步骤：先分析本章人物，再确认名单与第一视角主人公，
 * 之后「试运行预览 / 按此范围正式处理」才会启用。
 *
 * E2E 只使用显式启用的确定性 FakeProvider（不访问网络），因此候选人由后端离线给出；
 * 已经确认过的章节直接复用，避免同一次运行里重复创建人物分析任务。
 */
export async function confirmChapterRoster(page: Page) {
  await expect(page.getByTestId('character-roster-panel')).toBeVisible()
  // 等人物名单读取结束，避免把已确认的名单误判成未分析。
  await expect(page.getByTestId('roster-loading')).toHaveCount(0)
  if (await page.getByTestId('roster-confirmed').isVisible()) {
    return
  }

  await page.getByTestId('roster-analyze').click()
  await expect(page.getByTestId('roster-candidates')).toBeVisible({ timeout: 30_000 })

  const pov = page.getByRole('radio', { name: '本章第一视角主人公' }).first()
  await expect(pov).toBeVisible()
  if (!(await pov.isChecked())) {
    await pov.check()
  }

  await page.getByTestId('roster-confirm').click()
  await expect(page.getByTestId('roster-confirmed')).toBeVisible({ timeout: 30_000 })
}