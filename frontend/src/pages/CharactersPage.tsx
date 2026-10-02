import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'

import { editBookCharacter, fetchCharacterDirectory, mergeBookCharacter } from '../api/characters'
import { fetchBook, queryKeys } from '../api/books'
import type { CharacterDirectoryOut } from '../api/types'
import { useBatchProgress } from '../components/BatchProcessor'
import { CharacterAutoMerge } from '../components/CharacterAutoMerge'

function CharacterEditor({ item, targets, bookId, onSaved, disabled }: {
  item: CharacterDirectoryOut
  targets: CharacterDirectoryOut[]
  bookId: string
  onSaved: () => Promise<void>
  disabled: boolean
}) {
  const [name, setName] = useState(item.name)
  const [aliases, setAliases] = useState((item.aliases ?? []).join('、'))
  const [description, setDescription] = useState(item.description ?? '')
  const [targetId, setTargetId] = useState('')
  const [confirmMerge, setConfirmMerge] = useState(false)
  const [message, setMessage] = useState('')
  const target = targets.find((row) => row.character_id === targetId)
  const save = useMutation({
    mutationFn: () => editBookCharacter(bookId, item.character_id, {
      name: name.trim(), aliases: aliases.split(/[、，,\n]/).map((value) => value.trim()).filter(Boolean),
      description, expected_version: item.version ?? 1,
    }),
    onSuccess: async () => { await onSaved(); setMessage('人物资料已保存。') },
  })
  const merge = useMutation({
    mutationFn: () => mergeBookCharacter(bookId, item.character_id, {
      target_character_id: targetId, expected_version: item.version ?? 1,
      expected_target_version: target?.version ?? 1,
    }),
    onSuccess: onSaved,
  })
  const busy = disabled || save.isPending || merge.isPending
  const error = save.error ?? merge.error
  return <article className="ndr-character-card" aria-label={`人物 ${item.name}`}>
    <h3>{item.name}</h3>
    <p className="hint">{item.kind === 'speaker' ? '尚未关联全书人物；保存后纳入全书人物表。' :
      ({ manual: '已人工确认', automatic: '批量自动确认（未经人工复核）',
        legacy: '已确认（旧记录未区分来源）', imported: '导入恢复的人物',
        model: '模型识别人物' }[item.confirmation_source ?? (item.user_confirmed ? 'legacy' : 'model')])}</p>
    <label>姓名<input value={name} maxLength={128} onChange={(e) => setName(e.target.value)} disabled={busy} /></label>
    <label>别名（用、分隔）<input value={aliases} onChange={(e) => setAliases(e.target.value)} disabled={busy} /></label>
    <label>说明<textarea value={description} maxLength={512} rows={3} onChange={(e) => setDescription(e.target.value)} disabled={busy} /></label>
    <button type="button" className="ndr-primary" disabled={busy || !name.trim()} onClick={() => { setMessage(''); save.mutate() }}>保存人物资料</button>
    {!name.trim() && <p className="hint">请填写人物姓名后再保存。</p>}
    <label>合并到全书人物<select value={targetId} disabled={busy} onChange={(e) => { setTargetId(e.target.value); setConfirmMerge(false) }}>
      <option value="">请选择合并目标</option>
      {targets.map((row) => <option key={row.character_id} value={row.character_id} title={row.description}>{row.name}</option>)}
    </select></label>
    {target && <p className="hint">目标说明：{target.description || '暂无说明'}</p>}
    {!target && <p className="hint">请选择合并目标后再合并。</p>}
    {!confirmMerge ? <button type="button" disabled={busy || !target} onClick={() => setConfirmMerge(true)}>合并人物…</button> :
      <div role="alert">
        <p>将“{item.name}”合并到“{target?.name}”？全部已有对白和章节名单将改为目标人物；原姓名及别名保留为别名，使用目标说明。此操作无法自动撤销，未保存的编辑不会应用。</p>
        <button type="button" className="ndr-danger" disabled={busy} onClick={() => merge.mutate()}>确认合并</button>
        <button type="button" disabled={busy} onClick={() => setConfirmMerge(false)}>取消</button>
      </div>}
    {error && <p role="alert" className="status-error">{error instanceof Error ? error.message : '操作失败，请重新读取后重试'}</p>}
    {message && <p role="status">{message}</p>}
  </article>
}

export default function CharactersPage() {
  const { bookId = '' } = useParams<{ bookId: string }>()
  const [searchParams] = useSearchParams()
  const chapterId = searchParams.get('chapterId')
  const chapterQuery = chapterId ? `?chapterId=${encodeURIComponent(chapterId)}` : ''
  const queryClient = useQueryClient()
  const [search, setSearch] = useState('')
  const [message, setMessage] = useState('')
  const batchProgress = useBatchProgress(bookId)
  const [autoMergeBusy, setAutoMergeBusy] = useState(false)
  const book = useQuery({
    queryKey: queryKeys.book(bookId),
    queryFn: ({ signal }) => fetchBook(bookId, signal),
    enabled: Boolean(bookId),
  })
  const directory = useQuery({
    queryKey: ['character-directory', bookId],
    queryFn: ({ signal }) => fetchCharacterDirectory(bookId, signal),
    enabled: Boolean(bookId),
  })
  const saved = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['character-directory', bookId] }),
      queryClient.invalidateQueries({ queryKey: ['book-characters', bookId] }),
      queryClient.invalidateQueries({ queryKey: ['character-roster', bookId] }),
      queryClient.invalidateQueries({ queryKey: ['annotations', bookId] }),
      queryClient.invalidateQueries({ queryKey: ['review-items', bookId] }),
      queryClient.invalidateQueries({ queryKey: ['review-item'] }),
      queryClient.invalidateQueries({ queryKey: ['usage', bookId] }),
    ])
    setMessage('人物修改已保存。')
  }
  const entries = directory.data ?? []
  const term = search.trim().toLocaleLowerCase()
  const filtered = entries.filter((item) => [item.name, ...(item.aliases ?? []), item.description]
    .some((value) => value?.toLocaleLowerCase().includes(term)))
  return <div className="ndr-page ndr-characters">
    <header className="ndr-reader-header card ndr-page-header">
      <div>
        <h2>全书人物{book.data?.title ? `：${book.data.title}` : ''}</h2>
        <p className="hint">汇总当前书籍版本已识别的人物，可能包含后文剧透。</p>
      </div>
      <nav className="ndr-book-nav" aria-label="本书导航">
        <Link to={`/books/${bookId}/read${chapterQuery}`}>去阅读</Link>
        <Link to={`/books/${bookId}/preview${chapterQuery}`}>预览与处理</Link>
        <Link to={`/books/${bookId}/review`}>待确认队列</Link>
        <Link to="/library">返回书架</Link>
      </nav>
    </header>
    <CharacterAutoMerge key={bookId} bookId={bookId} versionId={book.data?.active_version_id} count={entries.length}
      disabled={batchProgress.running} onBusyChange={setAutoMergeBusy} onSaved={saved} />
    <section className="card">
      <p className="hint">汇总当前书籍版本已识别的人物（包括未发言人物），可能包含后文剧透。修改会影响已有对白、后续人物识别和导出，不改原文或章节完成状态。请先停止本书处理任务再编辑。</p>
      {batchProgress.running && <p role="status">批量处理正在运行，停止后可修改人物。</p>}
      {message && <p role="status">{message}</p>}
      <div className="ndr-toolbar">
        <label className="ndr-field">搜索人物<input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="姓名、别名或说明" /></label>
        <button type="button" disabled={directory.isFetching} onClick={() => void directory.refetch()}>重新读取</button>
      </div>
      {directory.isPending && <p>正在读取全书人物…</p>}
      {directory.isError && <p role="alert" className="status-error">人物读取失败：{directory.error.message}</p>}
      {directory.data && <p>共 {entries.length} 个人物{term ? `，匹配 ${filtered.length} 个` : ''}</p>}
      {!directory.isPending && !directory.isError && !entries.length && <p>尚未识别人物，请先在预览与处理中分析人物。</p>}
      <div className="ndr-character-list">
        {filtered.map((item) => <CharacterEditor key={`${item.character_id}:${item.version}`} item={item} bookId={bookId}
          disabled={batchProgress.running || autoMergeBusy}
          targets={entries.filter((row) => row.kind !== 'speaker' && row.character_id !== item.character_id)} onSaved={saved} />)}
      </div>
    </section>
  </div>
}
