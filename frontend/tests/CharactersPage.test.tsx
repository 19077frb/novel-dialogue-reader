import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import * as api from '../src/api/characters'
import * as booksApi from '../src/api/books'
import * as profilesApi from '../src/api/profiles'
import { updateProcessingPreferences } from '../src/processing/preferences'
import type { CharacterDirectoryOut } from '../src/api/types'
import CharactersPage from '../src/pages/CharactersPage'
import { renderRoute } from './helpers'

vi.mock('../src/api/characters', () => ({
  fetchCharacterDirectory: vi.fn(), editBookCharacter: vi.fn(), mergeBookCharacter: vi.fn(),
  confirmCharacterAutoMerge: vi.fn(),
  startCharacterAutoMerge: vi.fn(), fetchLatestCharacterAutoMerge: vi.fn(),
}))
vi.mock('../src/api/profiles', () => ({ fetchProfiles: vi.fn(), profileKeys: { profiles: () => ['profiles'] } }))
vi.mock('../src/api/books', () => ({
  fetchBook: vi.fn(), queryKeys: { book: (id: string) => ['book', id] },
}))

const entries: CharacterDirectoryOut[] = [
  { character_id: 'u1', name: '悠太', aliases: ['哥哥'], description: '男主角', kind: 'book', version: 2, user_confirmed: true },
  { character_id: 'u2', name: '浅村悠太', aliases: [], description: '书店店员', kind: 'book', version: 1, user_confirmed: false },
  { character_id: 'speaker:s1', name: '女店员', aliases: [], description: '打工前辈', kind: 'speaker', version: 1, user_confirmed: false },
]

const mergeResult = {
  job_id: 'merge-1', state: 'COMPLETED' as const, merged_count: 1, skipped_groups: 0,
  merges: [{ target_character_id: 'u2', target_name: '浅村悠太', source_names: ['悠太'], reason: '别名和说明指向同一人' }],
  usage: { total_tokens: 40 }, unknown_usage_runs: 0, last_error: null,
  created_at: '2026-10-01T00:00:00Z', updated_at: '2026-10-01T00:00:01Z',
}

beforeEach(() => {
  vi.resetAllMocks()
  sessionStorage.clear()
  updateProcessingPreferences({ profileId: 'p1', tokenLimit: null, thinkingMode: 'default', thinkingEffort: 'default' })
  vi.mocked(booksApi.fetchBook).mockResolvedValue({ id: 'b1', title: '测试小说', active_version_id: 'v1' } as never)
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
  it('找回合并建议时只预览，勾选后才能确认且确认不调用模型', async () => {
    const preview = { ...mergeResult, phase: 'awaiting_confirmation' as const, merged_count: 0, merges: [],
      proposals: [{ target: entries[1], sources: [entries[0]], confidence: 0.99, reason: '姓名与别名吻合' }] }
    vi.mocked(api.fetchLatestCharacterAutoMerge).mockResolvedValue(preview)
    vi.mocked(api.confirmCharacterAutoMerge).mockImplementation(async () => {
      const result = { ...mergeResult, phase: 'applied' as const }
      vi.mocked(api.fetchLatestCharacterAutoMerge).mockResolvedValue(result)
      return result
    })
    renderPage()
    const panel = within(await screen.findByRole('region', { name: '合并建议预览' }))
    const accept = panel.getByRole('checkbox', { name: '接受：悠太 → 浅村悠太' })
    expect(accept).not.toBeChecked()
    expect(panel.getByText(/姓名与别名吻合/)).toBeInTheDocument()
    expect(panel.getByText(/男主角/)).toBeInTheDocument()
    expect(api.confirmCharacterAutoMerge).not.toHaveBeenCalled()
    expect(api.startCharacterAutoMerge).not.toHaveBeenCalled()
    expect(panel.getByRole('button', { name: '确认合并所选 0 组' })).toBeDisabled()
    await userEvent.click(accept)
    await userEvent.click(panel.getByRole('button', { name: '确认合并所选 1 组' }))
    await screen.findByText('合并了 1 个重复人物。')
    expect(api.confirmCharacterAutoMerge).toHaveBeenCalledWith('b1', 'merge-1', ['u2'])
    expect(api.startCharacterAutoMerge).not.toHaveBeenCalled()
  })

  it('可放弃全部建议，不修改人物且刷新后保留放弃结果', async () => {
    vi.mocked(api.fetchLatestCharacterAutoMerge).mockResolvedValue({ ...mergeResult, phase: 'awaiting_confirmation', merged_count: 0, merges: [], proposals: [] })
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
    expect(api.startCharacterAutoMerge).toHaveBeenCalledWith('b1', expect.objectContaining({
      book_version_id: 'v1', profile_id: 'p1', max_total_tokens: null, run_now: true,
    }))
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
