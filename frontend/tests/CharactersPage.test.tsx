import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import * as api from '../src/api/characters'
import type { CharacterDirectoryOut } from '../src/api/types'
import CharactersPage from '../src/pages/CharactersPage'
import { renderRoute } from './helpers'

vi.mock('../src/api/characters', () => ({
  fetchCharacterDirectory: vi.fn(), editBookCharacter: vi.fn(), mergeBookCharacter: vi.fn(),
}))

const entries: CharacterDirectoryOut[] = [
  { character_id: 'u1', name: '悠太', aliases: ['哥哥'], description: '男主角', kind: 'book', version: 2, user_confirmed: true },
  { character_id: 'u2', name: '浅村悠太', aliases: [], description: '书店店员', kind: 'book', version: 1, user_confirmed: false },
  { character_id: 'speaker:s1', name: '女店员', aliases: [], description: '打工前辈', kind: 'speaker', version: 1, user_confirmed: false },
]

beforeEach(() => {
  vi.resetAllMocks()
  vi.mocked(api.fetchCharacterDirectory).mockResolvedValue(entries)
  vi.mocked(api.editBookCharacter).mockResolvedValue(entries[0])
  vi.mocked(api.mergeBookCharacter).mockResolvedValue(entries[0])
})

function renderPage() {
  return renderRoute('/books/:bookId/characters', <CharactersPage />, '/books/b1/characters?chapterId=c2')
}

describe('CharactersPage', () => {
  it('列出全书与未关联人物，保留阅读章节并可按别名搜索', async () => {
    renderPage()
    await screen.findByText('共 3 个人物')
    expect(screen.getByRole('link', { name: '去阅读' })).toHaveAttribute('href', '/books/b1/read?chapterId=c2')
    expect(screen.getByRole('article', { name: '人物 女店员' })).toBeInTheDocument()
    await userEvent.type(screen.getByLabelText('搜索人物'), '哥哥')
    expect(screen.getByText('共 3 个人物，匹配 1 个')).toBeInTheDocument()
    expect(screen.queryByRole('article', { name: '人物 女店员' })).not.toBeInTheDocument()
  })

  it('保存姓名、别名和说明并携带并发版本', async () => {
    renderPage()
    const card = within(await screen.findByRole('article', { name: '人物 浅村悠太' }))
    await userEvent.clear(card.getByLabelText('姓名'))
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
    expect(within(select).queryByRole('option', { name: '浅村悠太' })).not.toBeInTheDocument()
    expect(within(select).queryByRole('option', { name: '女店员' })).not.toBeInTheDocument()
    await userEvent.selectOptions(select, 'u1')
    expect(card.getByText('目标说明：男主角')).toBeInTheDocument()
    await userEvent.click(card.getByRole('button', { name: '合并人物…' }))
    expect(api.mergeBookCharacter).not.toHaveBeenCalled()
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
