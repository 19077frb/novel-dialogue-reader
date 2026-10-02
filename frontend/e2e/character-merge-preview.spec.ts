import { expect, test } from '@playwright/test'

for (const layout of [
  { name: 'desktop-light', width: 1280, height: 900, colorScheme: 'light' as const },
  { name: 'desktop-dark', width: 1280, height: 900, colorScheme: 'dark' as const },
  { name: 'mobile-dark', width: 360, height: 740, colorScheme: 'dark' as const },
]) {
  test(`merge long text wraps and all groups confirm once: ${layout.name}`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width: layout.width, height: layout.height })
    await page.emulateMedia({ colorScheme: layout.colorScheme })
    const person = (id: string) => ({ character_id: id, name: `人物${id}${'LongName'.repeat(12)}`,
      aliases: ['Alias'.repeat(24)], description: `第一段\n${'Description'.repeat(35)}\n最后一段`, kind: 'book', version: 1 })
    const proposals = ['1', '2'].map(id => ({ target: person(`t${id}`), sources: [person(`s${id}`)],
      confidence: 0.99, reason: `第一行依据\n${'Reason'.repeat(60)}\n最后一行依据` }))
    let result = { job_id: 'merge1', state: 'COMPLETED', phase: 'awaiting_confirmation', proposals,
      usage: { total_tokens: 100 }, merged_count: 0, skipped_groups: 0, unknown_usage_runs: 0,
      created_at: '2026-10-02T00:00:00Z', updated_at: '2026-10-02T00:01:00Z',
      merges: [] as { target_character_id: string; target_name: string; source_names: string[]; reason: string }[] }
    const submissions: string[][] = []
    // Full UI rendering with offline responses; no library changes or model calls.
    await page.route('**/api/**', async route => {
      const request = route.request()
      const path = new URL(request.url()).pathname
      if (!path.startsWith('/api/')) { await route.continue(); return }
      let data: unknown = null
      if (path === '/api/health') data = { status: 'ok', version: '0.2.0', database: { state: 'ready' } }
      if (path === '/api/books/b1') data = { id: 'b1', title: '合并预览测试', active_version_id: 'v1' }
      if (path === '/api/books/b1/character-directory') data = []
      if (path === '/api/model-profiles') data = []
      if (path === '/api/books/b1/character-directory/auto-merge') data = result
      if (path.endsWith('/auto-merge/merge1/confirm')) {
        submissions.push(request.postDataJSON().selected_target_ids)
        result = { ...result, phase: 'applied', merged_count: 2,
          merges: proposals.map(group => ({ target_character_id: group.target.character_id,
            target_name: group.target.name, source_names: group.sources.map(source => source.name), reason: group.reason })) }
        data = result
      }
      await route.fulfill({ json: path === '/api/health' ? data : { data, request_id: 'offline-preview-test' } })
    })
    await page.goto('/books/b1/characters')
    const preview = page.getByRole('region', { name: '合并建议预览' })
    await expect(preview).toBeVisible()
    await expect(preview.locator('.ndr-merge-card')).toHaveCount(2)
    await expect(preview.getByText('共 2 组建议，涉及 2 条重复人物记录；已选 0 组。')).toBeVisible()
    const textStyle = await preview.locator('.ndr-merge-people dd').last().evaluate(element => ({
      wrap: getComputedStyle(element).overflowWrap, whiteSpace: getComputedStyle(element).whiteSpace,
    }))
    expect(textStyle).toEqual({ wrap: 'anywhere', whiteSpace: 'pre-wrap' })
    const assertNoOverflow = async () => {
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
      expect(overflow).toBeLessThanOrEqual(1)
      for (const card of await page.locator('.ndr-merge-card').all()) {
        expect(await card.evaluate(element => element.scrollWidth - element.clientWidth)).toBeLessThanOrEqual(1)
      }
    }
    await assertNoOverflow()
    await page.screenshot({ path: testInfo.outputPath(`${layout.name}.png`), fullPage: true })
    await preview.getByRole('button', { name: '全选合并建议' }).click()
    await preview.getByRole('button', { name: '确认合并所选 2 组' }).click()
    await expect(page.getByText('合并了 2 个重复人物。')).toBeVisible()
    expect(submissions).toEqual([['t1', 't2']])
    await assertNoOverflow()
  })
}
