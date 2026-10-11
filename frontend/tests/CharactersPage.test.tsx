import { act, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import * as api from '../src/api/characters'
import * as booksApi from '../src/api/books'
import * as profilesApi from '../src/api/profiles'
import * as jobsApi from '../src/api/jobs'
import * as completion from '../src/processing/jobCompletion'
import { updateProcessingPreferences } from '../src/processing/preferences'
import { updateGeneralSettings } from '../src/settings/preferences'
import type { CharacterDirectoryOut } from '../src/api/types'
import CharactersPage from '../src/pages/CharactersPage'
import { renderRoute } from './helpers'

vi.mock('../src/api/characters', () => ({
  fetchCharacterDirectory: vi.fn(), editBookCharacter: vi.fn(), mergeBookCharacter: vi.fn(),
  setCharacterColor: vi.fn(),
  confirmCharacterAutoMerge: vi.fn(),
  startCharacterAutoMerge: vi.fn(), fetchLatestCharacterAutoMerge: vi.fn(),
}))
vi.mock('../src/api/profiles', () => ({ fetchProfiles: vi.fn(), profileKeys: { profiles: () => ['profiles'] } }))
vi.mock('../src/api/jobs', async importOriginal => ({
  ...await importOriginal<typeof import('../src/api/jobs')>(),
  fetchTaskQueue: vi.fn().mockResolvedValue({ items: [], next_cursor: null }),
}))
vi.mock('../src/processing/jobCompletion', async importOriginal => ({
  ...await importOriginal<typeof import('../src/processing/jobCompletion')>(),
  waitForJobCompletion: vi.fn(async job => ({ ...job, state: 'COMPLETED' })),
}))
vi.mock('../src/api/books', () => ({
  fetchBook: vi.fn(), fetchJob: vi.fn(), fetchChapters: vi.fn(), queryKeys: { book: (id: string) => ['book', id], chapters: (id: string) => ['chapters', id] },
}))

const entries: CharacterDirectoryOut[] = [
  { character_id: 'u1', name: '悠太', aliases: ['哥哥'], description: '男主角', kind: 'book', version: 2, user_confirmed: true, name_locked: false, confirmation_source: 'manual' },
  { character_id: 'u2', name: '浅村悠太', aliases: [], description: '书店店员', kind: 'book', version: 1, user_confirmed: false, name_locked: false, confirmation_source: 'model' },
  { character_id: 'speaker:s1', name: '女店员', aliases: [], description: '打工前辈', kind: 'speaker', version: 1, user_confirmed: false, name_locked: false, confirmation_source: 'model' },
]

const mergeResult = {
  job_id: 'merge-1', state: 'COMPLETED' as const, merged_count: 1, skipped_groups: 0,
  merges: [{ target_character_id: 'u2', target_name: '浅村悠太', source_names: ['悠太'], reason: '别名和说明指向同一人' }],
  usage: { total_tokens: 40 }, unknown_usage_runs: 0, last_error: null,
  created_at: '2026-10-01T00:00:00Z', updated_at: '2026-10-01T00:00:01Z',
}

beforeEach(() => {
  vi.resetAllMocks()
  localStorage.clear()
  vi.mocked(jobsApi.fetchTaskQueue).mockResolvedValue({ items: [], next_cursor: null })
  vi.mocked(completion.waitForJobCompletion).mockImplementation(async job => ({ ...job, state: 'COMPLETED' }))
  sessionStorage.clear()
  updateProcessingPreferences({ profileId: 'p1', tokenLimit: null, thinkingMode: 'default', thinkingEffort: 'default' })
  vi.mocked(booksApi.fetchBook).mockResolvedValue({ id: 'b1', title: '测试小说', active_version_id: 'v1' } as never)
  vi.mocked(booksApi.fetchChapters).mockResolvedValue([])
  vi.mocked(profilesApi.fetchProfiles).mockResolvedValue([{ id: 'p1', name: '合并模型', protocol: 'fake', model: 'test', params: {} }] as never)
  vi.mocked(api.startCharacterAutoMerge).mockResolvedValue({ id: 'merge-1', state: 'QUEUED' } as never)
  vi.mocked(api.fetchLatestCharacterAutoMerge).mockResolvedValue(null)
  vi.mocked(api.fetchCharacterDirectory).mockResolvedValue(entries)
  vi.mocked(api.editBookCharacter).mockResolvedValue(entries[0])
  vi.mocked(api.mergeBookCharacter).mockResolvedValue(entries[0])
})

function renderPage() {
  return renderRoute('/books/:bookId/characters', <CharactersPage />, '/books/b1/characters?chapterId=c2')
}

describe('CharactersPage', () => {
  it('本次合并等待单独计时，旧结果保持结束时间并显示阻塞章节', async () => {
    vi.mocked(api.fetchLatestCharacterAutoMerge).mockResolvedValue(mergeResult)
    vi.mocked(jobsApi.fetchTaskQueue).mockResolvedValue({ items: [{ id: 'old', state: 'QUEUED',
      chapter_title: '第十二卷 10月22日' }], next_cursor: null } as never)
    const user = userEvent.setup()
    renderPage()
    await screen.findByText('用时 1 秒')
    await user.click(screen.getByRole('checkbox', { name: /我同意调用模型/ }))
    await user.click(screen.getByRole('button', { name: '分析合并建议' }))
    await screen.findByText('本次自动合并：等待执行')
    await screen.findByText(/等待任务：第十二卷 10月22日/)
    expect(screen.getByText('用时 1 秒')).toBeInTheDocument()
    expect(screen.getByText('以下为上一次合并结果。')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '查看等待任务' })).toBeInTheDocument()
    expect(api.startCharacterAutoMerge).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: '停止本次分析' }))
    await waitFor(() => expect(screen.queryByText('本次自动合并：等待执行')).not.toBeInTheDocument(), { timeout: 2500 })
  })
  it('六个人物一页显示完整且不显示分页导航', async () => {
    vi.mocked(api.fetchCharacterDirectory).mockResolvedValue(Array.from({ length: 6 }, (_, i) => ({
      ...entries[0], character_id: `person-${i}`, name: `人物 ${i}`,
    })))
    renderPage()
    await screen.findByRole('article', { name: '人物 人物 5' })
    expect(screen.getAllByRole('article', { name: /^人物 人物/ })).toHaveLength(6)
    expect(screen.queryByRole('navigation', { name: '人物资料分页' })).not.toBeInTheDocument()
  })
  it('翻页保留人物草稿，搜索更换后回到第一页，不调用模型', async () => {
    vi.mocked(api.fetchCharacterDirectory).mockResolvedValue(Array.from({ length: 7 }, (_, i) => ({
      ...entries[0], character_id: `person-${i}`, name: `人物 ${i}`,
    })))
    renderPage()
    const card = await screen.findByRole('article', { name: '人物 人物 0' })
    await waitFor(() => expect(within(card).getByLabelText('姓名')).toBeEnabled())
    await userEvent.type(within(card).getByLabelText('姓名'), '草稿')
    expect(within(card).getByLabelText('姓名')).toHaveValue('人物 0草稿')
    const nav = screen.getByRole('navigation', { name: '人物资料分页' })
    expect(screen.getAllByRole('article', { name: /^人物 人物/ })).toHaveLength(6)
    await userEvent.click(within(nav).getByRole('button', { name: '下一页' }))
    expect(card).not.toBeVisible()
    expect(screen.getByRole('article', { name: '人物 人物 6' })).toBeVisible()
    expect(screen.getAllByRole('article', { name: /^人物 人物/ })).toHaveLength(1)
    await userEvent.click(within(nav).getByRole('button', { name: '上一页' }))
    expect(within(card).getByLabelText('姓名')).toHaveValue('人物 0草稿')
    await userEvent.click(within(nav).getByRole('button', { name: '下一页' }))
    await userEvent.type(screen.getByLabelText('搜索人物'), '人物 0')
    expect(screen.getByRole('article', { name: '人物 人物 0' })).toBeVisible()
    expect(api.editBookCharacter).not.toHaveBeenCalled()
    expect(api.startCharacterAutoMerge).not.toHaveBeenCalled()
  })
  it('公共配色说明及全书锁定只出现一次，各人物保留禁用悬停', async () => {
    vi.mocked(api.fetchLatestCharacterAutoMerge).mockResolvedValue({
      job_id: 'merge-1', state: 'RUNNING', usage: {}, merged_count: 0, unknown_usage_runs: 0,
      created_at: '2026-10-01T00:00:00Z', updated_at: '2026-10-01T00:00:01Z',
    } as never)
    renderPage()
    await screen.findByText('自动合并：处理中')
    expect(screen.getAllByText(/^颜色由程序生成，保存不调用模型/)).toHaveLength(1)
    expect(screen.getAllByText('本书任务或合并决定正在执行，请等待结束或先停止任务后再编辑人物。')).toHaveLength(1)
    for (const input of screen.getAllByLabelText('姓名')) {
      expect(input).toBeDisabled()
      expect(input).toHaveAttribute('title', '本书任务或合并决定正在执行，请等待结束或先停止任务后再编辑人物。')
    }
    expect(api.editBookCharacter).not.toHaveBeenCalled()
  })
  it('可恢复自动配色，有未保存的人物资料时阻止改色并说明原因', async () => {
    vi.mocked(api.fetchCharacterDirectory).mockResolvedValue(entries.map((row, index) => ({
      ...row, color_index: index === 0 ? 8 : index, preferred_color_index: index === 0 ? 8 : null,
    })))
    vi.mocked(api.setCharacterColor).mockResolvedValue(entries[0])
    renderPage()
    const card = await screen.findByRole('article', { name: '人物 悠太' })
    await waitFor(() => expect(within(card).getByLabelText('人物颜色')).toBeEnabled())
    await userEvent.selectOptions(within(card).getByLabelText('人物颜色'), '')
    await userEvent.type(within(card).getByLabelText('姓名'), '未保存')
    expect(within(card).getByRole('button', { name: '保存颜色' })).toBeDisabled()
    expect(within(card).getByText(/有未保存的人物资料，请先保存/)).toBeVisible()
    await userEvent.clear(within(card).getByLabelText('姓名'))
    await userEvent.type(within(card).getByLabelText('姓名'), '悠太')
    await userEvent.click(within(card).getByRole('button', { name: '保存颜色' }))
    await waitFor(() => expect(api.setCharacterColor).toHaveBeenCalledWith('b1', 'u1', { color_index: null, expected_version: 2 }))
    expect(api.editBookCharacter).not.toHaveBeenCalled()
  })
  it('预览并独立保存颜色，不提交姓名资料或调用模型', async () => {
    vi.mocked(api.fetchCharacterDirectory).mockResolvedValue(entries.map((row, index) => ({
      ...row, color_index: index, preferred_color_index: null,
    })))
    vi.mocked(api.setCharacterColor).mockResolvedValue({ ...entries[0], color_index: 8, preferred_color_index: 8 })
    renderPage()
    const card = await screen.findByRole('article', { name: '人物 悠太' })
    await waitFor(() => expect(within(card).getByLabelText('人物颜色')).toBeEnabled())
    expect(within(card).getByRole('button', { name: '保存颜色' })).toBeDisabled()
    await userEvent.selectOptions(within(card).getByLabelText('人物颜色'), '8')
    await userEvent.click(within(card).getByRole('button', { name: '保存颜色' }))
    await waitFor(() => expect(api.setCharacterColor).toHaveBeenCalledWith('b1', 'u1', { color_index: 8, expected_version: 2 }))
    expect(api.editBookCharacter).not.toHaveBeenCalled()
    expect(api.startCharacterAutoMerge).not.toHaveBeenCalled()
    const anonymous = screen.getByRole('article', { name: '人物 女店员' })
    expect(within(anonymous).getByLabelText('人物颜色')).toBeDisabled()
    expect(within(anonymous).getByText(/请先保存人物资料，纳入全书人物后再设置颜色/)).toBeVisible()
  })
  it('按用户选择的揭示章节提交人物修改，默认不推断提前可见', async () => {
    updateGeneralSettings({ enableExperimentalFeatures: true })
    vi.mocked(booksApi.fetchChapters).mockResolvedValue([
      { id: 'c1', ordinal: 0, title: '第一章', start_cp: 0, end_cp: 100 },
      { id: 'c2', ordinal: 1, title: '第二章', start_cp: 100, end_cp: 200 },
    ] as never)
    renderPage()
    const select = await screen.findByLabelText('本次人物修改从哪一章起可见（初读·试验）')
    expect(select).toHaveValue('')
    await screen.findByRole('option', { name: '第二章结束后' })
    await waitFor(() => expect(select).toBeEnabled())
    await userEvent.selectOptions(select, '200')
    const card = await screen.findByRole('article', { name: '人物 悠太' })
    await userEvent.click(within(card).getByRole('button', { name: '保存人物资料' }))
    await waitFor(() => expect(api.editBookCharacter).toHaveBeenCalledWith('b1', 'u1',
      expect.objectContaining({ visible_from_cp: 200 })))
    act(() => updateGeneralSettings({ enableExperimentalFeatures: false }))
    expect(screen.queryByLabelText(/本次人物修改从哪一章/)).not.toBeInTheDocument()
    await waitFor(() => expect(within(card).getByRole('button', { name: '保存人物资料' })).toBeEnabled())
    await userEvent.click(within(card).getByRole('button', { name: '保存人物资料' }))
    await waitFor(() => expect(api.editBookCharacter).toHaveBeenCalledTimes(2))
    expect(vi.mocked(api.editBookCharacter).mock.calls[1][2]).not.toHaveProperty('visible_from_cp')
  })
  it('折叠人物资料不丢失未保存编辑，也不发起修改或模型请求', async () => {
    renderPage()
    const card = await screen.findByRole('article', { name: '人物 悠太' })
    const input = within(card).getByLabelText('姓名')
    await waitFor(() => expect(input).toBeEnabled())
    await userEvent.clear(input)
    await userEvent.type(input, '未保存姓名')
    await userEvent.click(screen.getByRole('button', { name: '收起人物资料列表' }))
    expect(card).not.toBeVisible()
    expect(screen.getByText('当前显示 3 个人物')).toBeVisible()
    await userEvent.click(screen.getByRole('button', { name: '展开人物资料列表' }))
    expect(input).toHaveValue('未保存姓名')
    expect(api.editBookCharacter).not.toHaveBeenCalled()
    expect(api.startCharacterAutoMerge).not.toHaveBeenCalled()
  })
  it('按目录频次顺序展示人物和章节对白统计，搜索保持顺序', async () => {
    vi.mocked(api.fetchCharacterDirectory).mockResolvedValue([
      { ...entries[1], chapter_count: 8, dialogue_count: 90 },
      { ...entries[0], chapter_count: 3, dialogue_count: 20 },
      { ...entries[2], chapter_count: 1, dialogue_count: 2 },
    ])
    renderPage()
    await screen.findByText('出现 8 章 · 90 句对白')
    expect(screen.getAllByRole('article').map((node) => node.getAttribute('aria-label')))
      .toEqual(['人物 浅村悠太', '人物 悠太', '人物 女店员'])
    await userEvent.type(screen.getByLabelText('搜索人物'), '悠太')
    expect(screen.getAllByRole('article').map((node) => node.getAttribute('aria-label')))
      .toEqual(['人物 浅村悠太', '人物 悠太'])
    expect(screen.getByText('出现 3 章 · 20 句对白')).toBeInTheDocument()
  })
  it('区分批量自动确认、人工确认、旧记录与导入人物，不靠旧布尔值声称人工确认', async () => {
    const records: CharacterDirectoryOut[] = [
      { ...entries[0], character_id: 'auto', name: '自动人物', confirmation_source: 'automatic', user_confirmed: false },
      { ...entries[0], character_id: 'old', name: '历史人物', confirmation_source: 'legacy', user_confirmed: true },
      { ...entries[0], character_id: 'import', name: '导入人物', confirmation_source: 'imported', user_confirmed: true },
      entries[0],
    ]
    vi.mocked(api.fetchCharacterDirectory).mockResolvedValue(records)
    renderPage()
    const automatic = within(await screen.findByRole('article', { name: '人物 自动人物' }))
    expect(automatic.getByText('批量自动确认（未经人工复核）')).toBeInTheDocument()
    expect(automatic.queryByText('已人工确认')).not.toBeInTheDocument()
    expect(screen.getByText('已确认（旧记录未区分来源）')).toBeInTheDocument()
    expect(screen.getByText('导入恢复的人物')).toBeInTheDocument()
    expect(screen.getAllByText('已人工确认')).toHaveLength(1)
  })
  it('没有重复记录也能预览代称升级为真实姓名，确认前不修改人物', async () => {
    const target = { ...entries[0], name: '女神', aliases: ['阿库娅', '水之女神'] }
    vi.mocked(api.fetchCharacterDirectory).mockResolvedValue([target])
    vi.mocked(api.fetchLatestCharacterAutoMerge).mockResolvedValue({
      ...mergeResult, phase: 'awaiting_confirmation', merged_count: 0, merges: [],
      proposals: [{ target, sources: [], preferred_name: '阿库娅', confidence: 0.99,
        reason: '资料明确表明女神的姓名为阿库娅', merged_description: '阿库娅，水之女神。' }],
    })
    renderPage()
    const panel = within(await screen.findByRole('region', { name: '合并建议预览' }))
    expect(panel.getByText('接受：女神 → 阿库娅')).toBeInTheDocument()
    expect(panel.getByText(/正式名称：女神 → 阿库娅；原称呼保留为别名/)).toBeInTheDocument()
    expect(panel.getByRole('button', { name: '确认合并所选 0 组' })).toBeDisabled()
    expect(api.confirmCharacterAutoMerge).not.toHaveBeenCalled()
    expect(api.editBookCharacter).not.toHaveBeenCalled()
  })
  it('合并引用失败显示具体分组与可展开详情，不自动付费重试', async () => {
    vi.mocked(api.fetchLatestCharacterAutoMerge).mockResolvedValue({
      ...mergeResult, state: 'FAILED', merged_count: 0, merges: [],
      last_error: '第2组与第1组冲突；已拒绝整份方案，未执行合并',
      validation_issues: [{ code: 'dependent_groups', group_index: 2,
        related_group_index: 1, field: 'source_ids[0]', character_ref: 'C3',
        message: '第2组与第1组冲突：“悠太”（C3）形成相互依赖' }],
    })
    renderPage()
    expect(await screen.findByRole('alert')).toHaveTextContent('第2组与第1组冲突')
    expect(screen.getByText('查看校验详情（1 处）')).toBeInTheDocument()
    expect(screen.getByText(/字段：source_ids\[0\]/)).toBeInTheDocument()
    expect(screen.getByText(/不会自动再次调用模型/)).toBeInTheDocument()
    expect(api.startCharacterAutoMerge).not.toHaveBeenCalled()
    expect(api.confirmCharacterAutoMerge).not.toHaveBeenCalled()
  })
  it('部分组无效仍展示有效建议，可选择确认且保留排除原因', async () => {
    const issue = { code: 'invalid_preferred_name', group_index: 2,
      related_group_index: null, field: 'preferred_name', character_ref: 'C3',
      message: '第2组的建议姓名没有资料依据' }
    const preview = { ...mergeResult, phase: 'awaiting_confirmation' as const,
      merged_count: 0, merges: [], last_error: null, skipped_groups: 1,
      validation_issues: [issue], proposals: [{ target: entries[1], sources: [entries[0]],
        preferred_name: null, confidence: 0.99, reason: '姓名一致', merged_description: '人物说明' }] }
    vi.mocked(api.fetchLatestCharacterAutoMerge).mockResolvedValue(preview)
    vi.mocked(api.confirmCharacterAutoMerge).mockImplementation(async () => {
      const applied = { ...mergeResult, phase: 'applied' as const, validation_issues: [issue], skipped_groups: 1 }
      vi.mocked(api.fetchLatestCharacterAutoMerge).mockResolvedValue(applied)
      return applied
    })
    renderPage()
    const panel = within(await screen.findByRole('region', { name: '合并建议预览' }))
    expect(screen.getByText('部分建议未通过检查，已排除；其余建议仍可查看并选择确认。')).toBeVisible()
    await userEvent.click(screen.getByText('查看校验详情（1 处）'))
    expect(screen.getByText(/第2组的建议姓名没有资料依据/)).toBeVisible()
    expect(panel.queryByText(/正式名称：/)).not.toBeInTheDocument()
    await userEvent.click(panel.getByRole('button', { name: '全选合并建议' }))
    await userEvent.click(panel.getByRole('button', { name: '确认合并所选 1 组' }))
    await screen.findByText(/未通过检查的组没有执行，仅合并了你选择的有效建议/)
    expect(api.confirmCharacterAutoMerge).toHaveBeenCalledWith('b1', 'merge-1', ['u2'])
    expect(api.startCharacterAutoMerge).not.toHaveBeenCalled()
  })

  it('全选多组建议只提交一次，计数包含同组多个重复人物', async () => {
    const proposals = [
      { target: entries[1], sources: [entries[0], { ...entries[0], character_id: 'u3' }], confidence: 0.99, reason: '第一行\n第二行', merged_description: '主人公浅村悠太，在书店打工。' },
      { target: { ...entries[1], character_id: 'u4', name: '沙季' }, sources: [{ ...entries[0], character_id: 'u5' }], confidence: 0.99, reason: '别名一致', merged_description: '沙季，与悠太同住的高中生。' },
    ]
    vi.mocked(api.fetchLatestCharacterAutoMerge).mockResolvedValue({ ...mergeResult, phase: 'awaiting_confirmation', merged_count: 0, merges: [], proposals })
    vi.mocked(api.confirmCharacterAutoMerge).mockImplementation(async () => {
      const result = { ...mergeResult, phase: 'applied' as const, merged_count: 3 }
      vi.mocked(api.fetchLatestCharacterAutoMerge).mockResolvedValue(result)
      return result
    })
    renderPage()
    const panel = within(await screen.findByRole('region', { name: '合并建议预览' }))
    expect(panel.getByText('共 2 组建议，涉及 3 条重复人物记录；已选 0 组。')).toBeInTheDocument()
    await userEvent.click(panel.getByRole('button', { name: '全选合并建议' }))
    expect(panel.getAllByRole('checkbox').every(box => (box as HTMLInputElement).checked)).toBe(true)
    await userEvent.click(panel.getByRole('button', { name: '清空选择' }))
    expect(panel.getAllByRole('checkbox').every(box => !(box as HTMLInputElement).checked)).toBe(true)
    await userEvent.click(panel.getByRole('button', { name: '全选合并建议' }))
    await userEvent.click(panel.getByRole('button', { name: '确认合并所选 2 组' }))
    await screen.findByText('合并了 3 个重复人物。')
    expect(api.confirmCharacterAutoMerge).toHaveBeenCalledTimes(1)
    expect(api.confirmCharacterAutoMerge).toHaveBeenCalledWith('b1', 'merge-1', ['u2', 'u4'])
    expect(api.startCharacterAutoMerge).not.toHaveBeenCalled()
  })

  it('找回合并建议时只预览，勾选后才能确认且确认不调用模型', async () => {
    const preview = { ...mergeResult, phase: 'awaiting_confirmation' as const, merged_count: 0, merges: [],
      proposals: [{ target: entries[1], sources: [entries[0]], confidence: 0.99, reason: '姓名与别名吻合', merged_description: '主人公浅村悠太，在书店打工，别名哥哥。' }] }
    vi.mocked(api.fetchLatestCharacterAutoMerge).mockResolvedValue(preview)
    vi.mocked(api.confirmCharacterAutoMerge).mockImplementation(async () => {
      const result = { ...mergeResult, phase: 'applied' as const }
      vi.mocked(api.fetchLatestCharacterAutoMerge).mockResolvedValue(result)
      return result
    })
    renderPage()
    const panel = within(await screen.findByRole('region', { name: '合并建议预览' }))
    const accept = panel.getByRole('checkbox', { name: '接受：悠太 → 浅村悠太' })
    expect(screen.getByLabelText('我同意调用模型生成合并建议（会消耗 Tokens）')).toBeDisabled()
    expect(screen.getByText('已有合并建议等待确认，请先确认或放弃当前建议，再开始新的分析。')).toBeVisible()
    expect(accept).not.toBeChecked()
    expect(panel.getByText(/姓名与别名吻合/)).toBeInTheDocument()
    expect(panel.getByText(/男主角/)).toBeInTheDocument()
    expect(panel.getByText('合并后的人物说明')).toBeInTheDocument()
    expect(panel.getByText('主人公浅村悠太，在书店打工，别名哥哥。')).toBeInTheDocument()
    expect(api.confirmCharacterAutoMerge).not.toHaveBeenCalled()
    expect(api.startCharacterAutoMerge).not.toHaveBeenCalled()
    expect(panel.getByRole('button', { name: '确认合并所选 0 组' })).toBeDisabled()
    await userEvent.click(accept)
    await userEvent.click(panel.getByRole('button', { name: '确认合并所选 1 组' }))
    await screen.findByText('合并了 1 个重复人物。')
    expect(api.confirmCharacterAutoMerge).toHaveBeenCalledWith('b1', 'merge-1', ['u2'])
    expect(api.startCharacterAutoMerge).not.toHaveBeenCalled()
  })

  it('旧建议没有整理说明时不能接受，允许放弃后重新分析', async () => {
    vi.mocked(api.fetchLatestCharacterAutoMerge).mockResolvedValue({
      ...mergeResult, phase: 'awaiting_confirmation', merged_count: 0, merges: [],
      proposals: [{ target: entries[1], sources: [entries[0]], confidence: 0.99, reason: '姓名一致', merged_description: null }],
    })
    renderPage()
    const panel = within(await screen.findByRole('region', { name: '合并建议预览' }))
    expect(panel.getByText('旧建议没有整理后的人物说明，请放弃本次建议并重新分析。')).toBeInTheDocument()
    await userEvent.click(panel.getByRole('button', { name: '全选合并建议' }))
    expect(panel.getByRole('button', { name: '确认合并所选 1 组' })).toBeDisabled()
    expect(panel.getByRole('button', { name: '放弃本次建议' })).toBeEnabled()
    expect(api.confirmCharacterAutoMerge).not.toHaveBeenCalled()
    expect(api.startCharacterAutoMerge).not.toHaveBeenCalled()
  })

  it('可放弃全部建议，不修改人物且刷新后保留放弃结果', async () => {
    vi.mocked(api.fetchLatestCharacterAutoMerge).mockResolvedValue({ ...mergeResult, phase: 'awaiting_confirmation', merged_count: 0, merges: [],
      proposals: [{ target: entries[1], sources: [entries[0]], confidence: 0.99, reason: '姓名一致', merged_description: '主人公浅村悠太。' }] })
    vi.mocked(api.confirmCharacterAutoMerge).mockImplementation(async () => {
      const discarded = { ...mergeResult, phase: 'discarded' as const, merged_count: 0, merges: [] }
      vi.mocked(api.fetchLatestCharacterAutoMerge).mockResolvedValue(discarded)
      return discarded
    })
    renderPage()
    await userEvent.click(await screen.findByRole('button', { name: '放弃本次建议' }))
    await screen.findByText('自动合并：本次建议已放弃')
    expect(api.confirmCharacterAutoMerge).toHaveBeenCalledWith('b1', 'merge-1', [])
    expect(api.mergeBookCharacter).not.toHaveBeenCalled()
    expect(api.startCharacterAutoMerge).not.toHaveBeenCalled()
  })

  it.each(['awaiting_confirmation', 'no_suggestions'] as const)('完成的空建议（%s）解锁配置且不会自动重试', async phase => {
    vi.mocked(api.fetchLatestCharacterAutoMerge).mockResolvedValue({
      ...mergeResult, phase, merged_count: 0, merges: [], proposals: [], skipped_groups: 2,
    })
    renderPage()
    await screen.findByText('自动合并：分析完成，没有可接受的合并或更名建议')
    const profile = await screen.findByTestId('character-merge-profile')
    await waitFor(() => expect(profile).toBeEnabled())
    const consent = screen.getByLabelText('我同意调用模型生成合并建议（会消耗 Tokens）')
    expect(consent).toBeEnabled()
    expect(consent).not.toBeChecked()
    expect(screen.getByText(/有 2 组建议未达到接受条件/)).toBeVisible()
    expect(screen.getByText('已知消耗 40 Tokens')).toBeVisible()
    expect(screen.queryByRole('button', { name: '放弃本次建议' })).not.toBeInTheDocument()
    expect(screen.queryByRole('region', { name: '合并建议预览' })).not.toBeInTheDocument()
    expect(api.startCharacterAutoMerge).not.toHaveBeenCalled()
    expect(api.confirmCharacterAutoMerge).not.toHaveBeenCalled()
    expect(api.fetchCharacterDirectory).toHaveBeenCalledTimes(1)
    await userEvent.selectOptions(profile, 'p1')
    vi.mocked(api.fetchLatestCharacterAutoMerge).mockResolvedValue({
      ...mergeResult, job_id: 'merge-2', state: 'RUNNING', phase: null, proposals: [], merges: [],
    })
    await userEvent.click(consent)
    await userEvent.click(screen.getByRole('button', { name: '分析合并建议' }))
    await screen.findByText('自动合并任务正在排队或运行，结束后才能重新配置。')
    expect(profile).toBeDisabled()
    expect(consent).toBeDisabled()
    await waitFor(() => expect(api.startCharacterAutoMerge).toHaveBeenCalledTimes(1))
    expect(api.confirmCharacterAutoMerge).not.toHaveBeenCalled()
  })

  it('停止收尾时保持锁定并显示原因，失败结果允许再次配置', async () => {
    vi.mocked(api.fetchLatestCharacterAutoMerge).mockResolvedValue({
      ...mergeResult, state: 'PAUSING', phase: null, proposals: [],
    })
    const page = renderPage()
    await screen.findByText('自动合并正在停止收尾，结束后才能重新配置。')
    expect(screen.getByLabelText('我同意调用模型生成合并建议（会消耗 Tokens）')).toBeDisabled()
    page.unmount()
    vi.mocked(api.fetchLatestCharacterAutoMerge).mockResolvedValue({
      ...mergeResult, state: 'FAILED', phase: null, proposals: [], last_error: '校验失败',
    })
    renderPage()
    await screen.findByText('校验失败')
    await waitFor(() => expect(screen.getByLabelText('我同意调用模型生成合并建议（会消耗 Tokens）')).toBeEnabled())
    expect(api.startCharacterAutoMerge).not.toHaveBeenCalled()
  })

  it('任务状态读取失败时保持锁定，重新读取成功后才允许配置', async () => {
    vi.mocked(api.fetchLatestCharacterAutoMerge).mockRejectedValue(new Error('状态读取失败'))
    renderPage()
    await screen.findByText(/任务状态读取失败：状态读取失败/)
    await userEvent.click(screen.getByLabelText('自动合并人物（先预览，再确认）'))
    const consent = screen.getByLabelText('我同意调用模型生成合并建议（会消耗 Tokens）')
    expect(consent).toBeDisabled()
    expect(screen.getByText('任务状态读取失败，重新读取成功后才能开始新的分析。')).toBeVisible()
    expect(api.startCharacterAutoMerge).not.toHaveBeenCalled()
    vi.mocked(api.fetchLatestCharacterAutoMerge).mockResolvedValue(null)
    await userEvent.click(screen.getByRole('button', { name: '重新读取进度' }))
    await waitFor(() => expect(consent).toBeEnabled())
    expect(api.startCharacterAutoMerge).not.toHaveBeenCalled()
  })

  it('自动合并默认关闭，确认后才派发模型任务并展示结果与用量', async () => {
    renderPage()
    await screen.findByText('共 3 个人物')
    expect(api.startCharacterAutoMerge).not.toHaveBeenCalled()
    await userEvent.click(screen.getByLabelText('自动合并人物（先预览，再确认）'))
    const start = screen.getByRole('button', { name: '分析合并建议' })
    expect(start).toBeDisabled()
    await userEvent.selectOptions(await screen.findByTestId('character-merge-profile'), 'p1')
    await userEvent.click(screen.getByLabelText('我同意调用模型生成合并建议（会消耗 Tokens）'))
    vi.mocked(api.fetchLatestCharacterAutoMerge).mockResolvedValue(mergeResult)
    await userEvent.click(start)
    await waitFor(() => expect(api.startCharacterAutoMerge).toHaveBeenCalledWith('b1', expect.objectContaining({
      book_version_id: 'v1', profile_id: 'p1', max_total_tokens: null, run_now: true,
    })))
    await screen.findByText('合并了 1 个重复人物。')
    expect(screen.getByText(/悠太 → 浅村悠太/)).toHaveTextContent('别名和说明指向同一人')
    expect(screen.getByText('已知消耗 40 Tokens')).toBeInTheDocument()
    expect(api.fetchCharacterDirectory).toHaveBeenCalledTimes(2)
  })

  it('自动合并进行中禁止新任务与人物编辑，重新进入页面继续跟踪任务', async () => {
    vi.mocked(api.fetchLatestCharacterAutoMerge).mockResolvedValue({
      job_id: 'merge-1', state: 'RUNNING', usage: {}, merged_count: 0, unknown_usage_runs: 0,
      created_at: '2026-10-01T00:00:00Z', updated_at: '2026-10-01T00:00:01Z',
    } as never)
    const page = renderPage()
    await screen.findByText('自动合并：处理中')
    expect(screen.getByRole('button', { name: '分析合并建议' })).toBeDisabled()
    expect(screen.getAllByRole('button', { name: '保存人物资料' }).every(button => (button as HTMLButtonElement).disabled)).toBe(true)
    expect(screen.getByRole('button', { name: '停止自动合并' })).toBeEnabled()
    expect(api.startCharacterAutoMerge).not.toHaveBeenCalled()
    page.unmount()
    localStorage.clear()
    sessionStorage.clear()
    renderPage()
    await screen.findByText('自动合并：处理中')
    expect(api.fetchLatestCharacterAutoMerge).toHaveBeenCalledWith('b1', 'v1', expect.any(AbortSignal))
    expect(api.startCharacterAutoMerge).not.toHaveBeenCalled()
  })

  it('没有浏览器记录也能找回完成结果与用量，不重新调用模型', async () => {
    vi.mocked(api.fetchLatestCharacterAutoMerge).mockResolvedValue(mergeResult)
    renderPage()
    await screen.findByText('合并了 1 个重复人物。')
    expect(screen.getByText('已知消耗 40 Tokens')).toBeInTheDocument()
    expect(api.startCharacterAutoMerge).not.toHaveBeenCalled()
  })

  it('任务状态读取失败时提供重读按钮且不允许重复创建任务', async () => {
    vi.mocked(api.fetchLatestCharacterAutoMerge).mockRejectedValue(new Error('服务器暂时不可用'))
    renderPage()
    expect(await screen.findByRole('alert')).toHaveTextContent('服务器暂时不可用')
    await userEvent.click(screen.getByLabelText('自动合并人物（先预览，再确认）'))
    expect(screen.getByRole('button', { name: '分析合并建议' })).toBeDisabled()
    vi.mocked(api.fetchLatestCharacterAutoMerge).mockResolvedValue(mergeResult)
    await userEvent.click(screen.getByRole('button', { name: '重新读取进度' }))
    await screen.findByText('合并了 1 个重复人物。')
    expect(api.startCharacterAutoMerge).not.toHaveBeenCalled()
  })

  it('自动合并失败展示具体错误，不清空原有人物资料', async () => {
    renderPage()
    await screen.findByText('共 3 个人物')
    await userEvent.click(screen.getByLabelText('自动合并人物（先预览，再确认）'))
    vi.mocked(api.startCharacterAutoMerge).mockRejectedValue(new Error('本书有处理任务正在运行'))
    await screen.findByRole('option', { name: /合并模型/ })
    await userEvent.click(screen.getByLabelText('我同意调用模型生成合并建议（会消耗 Tokens）'))
    await userEvent.click(screen.getByRole('button', { name: '分析合并建议' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('本书有处理任务正在运行')
    expect(screen.getByRole('article', { name: '人物 浅村悠太' })).toBeInTheDocument()
  })
  it('列出全书与未关联人物，保留阅读章节并可按别名搜索', async () => {
    renderPage()
    await screen.findByText('共 3 个人物')
    expect(await screen.findByRole('heading', { name: '全书人物：测试小说' })).toBeInTheDocument()
    expect(screen.getByRole('navigation', { name: '本书导航' })).toHaveClass('ndr-book-nav')
    expect(screen.getByRole('link', { name: '去阅读' })).toHaveAttribute('href', '/books/b1/read?chapterId=c2')
    expect(screen.getByRole('article', { name: '人物 女店员' })).toBeInTheDocument()
    await userEvent.type(screen.getByLabelText('搜索人物'), '哥哥')
    expect(screen.getByText('共 3 个人物，匹配 1 个')).toBeInTheDocument()
    expect(screen.queryByRole('article', { name: '人物 女店员' })).not.toBeInTheDocument()
  })

  it('保存姓名、别名和说明并携带并发版本', async () => {
    renderPage()
    const card = within(await screen.findByRole('article', { name: '人物 浅村悠太' }))
    await waitFor(() => expect(card.getByLabelText('姓名')).toBeEnabled())
    await userEvent.clear(card.getByLabelText('姓名'))
    expect(card.getByRole('button', { name: '保存人物资料' })).toBeDisabled()
    expect(card.getByText('请填写人物姓名后再保存。')).toBeInTheDocument()
    await userEvent.type(card.getByLabelText('姓名'), '浅村优太')
    await userEvent.type(card.getByLabelText('别名（用、分隔）'), '悠太、哥哥')
    await userEvent.clear(card.getByLabelText('说明'))
    await userEvent.type(card.getByLabelText('说明'), '主人公')
    await userEvent.click(card.getByRole('button', { name: '保存人物资料' }))
    await waitFor(() => expect(api.editBookCharacter).toHaveBeenCalledWith('b1', 'u2', {
      name: '浅村优太', aliases: ['悠太', '哥哥'], description: '主人公', expected_version: 1,
    }))
    await screen.findByText('人物修改已保存。')
  })

  it('合并前二次确认，不允许选自己或未关联人物', async () => {
    renderPage()
    const card = within(await screen.findByRole('article', { name: '人物 浅村悠太' }))
    const select = card.getByLabelText('合并到全书人物')
    await waitFor(() => expect(select).toBeEnabled())
    expect(card.getByRole('button', { name: '合并人物…' })).toBeDisabled()
    expect(card.getByText('请选择合并目标后再合并。')).toBeInTheDocument()
    expect(within(select).queryByRole('option', { name: '浅村悠太' })).not.toBeInTheDocument()
    expect(within(select).queryByRole('option', { name: '女店员' })).not.toBeInTheDocument()
    await userEvent.selectOptions(select, 'u1')
    expect(card.getByText('目标说明：男主角')).toBeInTheDocument()
    await userEvent.click(card.getByRole('button', { name: '合并人物…' }))
    expect(api.mergeBookCharacter).not.toHaveBeenCalled()
    expect(card.getByRole('button', { name: '确认合并' })).toHaveClass('ndr-danger')
    await userEvent.click(card.getByRole('button', { name: '确认合并' }))
    await waitFor(() => expect(api.mergeBookCharacter).toHaveBeenCalledWith('b1', 'u2', {
      target_character_id: 'u1', expected_version: 1, expected_target_version: 2,
    }))
  })

  it('显示保存的具体错误并保留未保存输入', async () => {
    vi.mocked(api.editBookCharacter).mockRejectedValue(new Error('人物版本冲突，请重新读取'))
    renderPage()
    const card = within(await screen.findByRole('article', { name: '人物 浅村悠太' }))
    await userEvent.click(card.getByRole('button', { name: '保存人物资料' }))
    expect(await card.findByRole('alert')).toHaveTextContent('人物版本冲突，请重新读取')
    expect(card.getByLabelText('姓名')).toHaveValue('浅村悠太')
  })

  it('读取失败有具体错误与重新读取按钮', async () => {
    vi.mocked(api.fetchCharacterDirectory).mockRejectedValue(new Error('数据库暂时不可用'))
    renderPage()
    expect(await screen.findByRole('alert')).toHaveTextContent('数据库暂时不可用')
    vi.mocked(api.fetchCharacterDirectory).mockResolvedValue(entries)
    await userEvent.click(screen.getByRole('button', { name: '重新读取' }))
    await screen.findByText('共 3 个人物')
  })
})
