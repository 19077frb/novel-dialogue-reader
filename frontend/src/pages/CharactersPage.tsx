import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'

import { editBookCharacter, fetchCharacterDirectory, mergeBookCharacter, setCharacterColor } from '../api/characters'
import { fetchBook, fetchChapters, queryKeys } from '../api/books'
import type { CharacterDirectoryOut } from '../api/types'
import { useBatchProgress } from '../components/BatchProcessor'
import { CharacterAutoMerge } from '../components/CharacterAutoMerge'
import { CollapsibleBlock } from '../components/CollapsibleBlock'
import { DisabledHint } from '../components/DisabledHint'
import { colorForIndex } from '../styles/palette'

const COLOR_NAMES = ['蓝色', '橙色', '绿色', '红色', '紫色', '青色', '金黄色', '粉色',
  '棕色', '墨绿色', '靛蓝色', '酒红色', '橄榄绿', '灰蓝色', '灰紫色', '深灰色']

function CharacterEditor({ item, targets, bookId, onSaved, disabled, visibleFromCp }: {
  item: CharacterDirectoryOut
  targets: CharacterDirectoryOut[]
  bookId: string
  onSaved: () => Promise<void>
  disabled: boolean
  visibleFromCp: number | null
}) {
  const [name, setName] = useState(item.name)
  const [aliases, setAliases] = useState((item.aliases ?? []).join('、'))
  const [description, setDescription] = useState(item.description ?? '')
  const [color, setColor] = useState<number | null>(item.preferred_color_index ?? null)
  const [targetId, setTargetId] = useState('')
  const [confirmMerge, setConfirmMerge] = useState(false)
  const [message, setMessage] = useState('')
  const target = targets.find((row) => row.character_id === targetId)
  const save = useMutation({
    mutationFn: () => editBookCharacter(bookId, item.character_id, {
      name: name.trim(), aliases: aliases.split(/[、，,\n]/).map((value) => value.trim()).filter(Boolean),
      description, expected_version: item.version ?? 1,
      ...(visibleFromCp == null ? {} : { visible_from_cp: visibleFromCp }),
    }),
    onSuccess: async () => { await onSaved(); setMessage('人物资料已保存。') },
  })
  const merge = useMutation({
    mutationFn: () => mergeBookCharacter(bookId, item.character_id, {
      target_character_id: targetId, expected_version: item.version ?? 1,
      expected_target_version: target?.version ?? 1,
      ...(visibleFromCp == null ? {} : { visible_from_cp: visibleFromCp }),
    }),
    onSuccess: onSaved,
  })
  const saveColor = useMutation({
    mutationFn: () => setCharacterColor(bookId, item.character_id, { color_index: color, expected_version: item.version ?? 1 }),
    onSuccess: onSaved,
  })
  const busy = disabled || save.isPending || merge.isPending || saveColor.isPending
  const lockReason = disabled ? '本书任务或合并决定正在执行，请等待结束或先停止任务后再编辑人物。' : save.isPending || merge.isPending || saveColor.isPending ? '正在保存人物资料或合并人物，请等待完成。' : undefined
  const dirty = name !== item.name || aliases !== (item.aliases ?? []).join('、') || description !== (item.description ?? '')
  const colorReason = lockReason ?? (item.kind === 'speaker' ? '请先保存人物资料，纳入全书人物后再设置颜色。' : dirty ? '有未保存的人物资料，请先保存，避免改色后丢失编辑。' : color === (item.preferred_color_index ?? null) ? '颜色设置没有变化。' : undefined)
  const error = save.error ?? merge.error ?? saveColor.error
  const colorChoices = Array.from({ length: 32 }, (_, index) => index)
  const reserved = new Set(targets.map(row => row.preferred_color_index))
  for (let index = 32, added = 0; added < 16; index++) {
    if (!reserved.has(index)) { colorChoices.push(index); added++ }
  }
  if (color != null && !colorChoices.includes(color)) colorChoices.push(color)
  if (item.color_index != null && item.color_index >= 32 && !colorChoices.includes(item.color_index)) colorChoices.push(item.color_index)
  return <article className="ndr-character-card" aria-label={`人物 ${item.name}`}>
    <h3 style={{ color: colorForIndex(item.color_index) }}>{item.name}</h3>
    <DisabledHint reason={lockReason} />
    {item.chapter_count != null && item.dialogue_count != null && <p className="hint">
      出现 {item.chapter_count} 章 · {item.dialogue_count} 句对白
    </p>}
    <p className="hint">{item.kind === 'speaker' ? '尚未关联全书人物；保存后纳入全书人物表。' :
      ({ manual: '已人工确认', automatic: '批量自动确认（未经人工复核）',
        legacy: '已确认（旧记录未区分来源）', imported: '导入恢复的人物',
        model: '模型识别人物' }[item.confirmation_source ?? (item.user_confirmed ? 'legacy' : 'model')])}</p>
    <label className="ndr-field">人物颜色<select value={color ?? ''} disabled={busy || item.kind === 'speaker'}
      title={lockReason ?? (item.kind === 'speaker' ? '请先保存人物资料，纳入全书人物后再设置颜色。' : undefined)}
      onChange={event => setColor(event.target.value === '' ? null : Number(event.target.value))}>
      <option value="">自动分配{item.color_index != null ? `（当前色号 ${item.color_index + 1}）` : ''}</option>
      {colorChoices.map(index => {
        const owner = targets.find(row => row.preferred_color_index === index)
        return <option key={index} value={index} disabled={Boolean(owner)}>{`${COLOR_NAMES[index] ?? '扩展色'} · 色号 ${index + 1}${owner ? `（${owner.name}已指定）` : ''}`}</option>
      })}
    </select></label>
    <p style={{ color: colorForIndex(color ?? item.color_index) }}>颜色预览：〔{item.name}〕「你好。」</p>
    {color == null && item.preferred_color_index != null && <p className="hint">恢复自动分配后的颜色以保存结果为准。</p>}
    <button type="button" title={colorReason} disabled={Boolean(colorReason)} onClick={() => saveColor.mutate()}>保存颜色</button>
    <DisabledHint reason={colorReason} />
    <p className="hint">颜色由程序生成，保存不调用模型、不更改人工确认状态。选择“自动分配”可恢复；自动占用的色号会为手动选择让位。改色在初读和重读中均生效。</p>
    <label>姓名<input value={name} maxLength={128} onChange={(e) => setName(e.target.value)} disabled={busy} /></label>
    <label>别名（用、分隔）<input value={aliases} onChange={(e) => setAliases(e.target.value)} disabled={busy} /></label>
    <label>说明<textarea value={description} maxLength={512} rows={3} onChange={(e) => setDescription(e.target.value)} disabled={busy} /></label>
    <button title={lockReason ?? (!name.trim() ? '请先填写人物姓名。' : undefined)} type="button" className="ndr-primary" disabled={busy || !name.trim()} onClick={() => { setMessage(''); save.mutate() }}>保存人物资料</button>
    {!name.trim() && <p className="hint">请填写人物姓名后再保存。</p>}
    <label>合并到全书人物<select value={targetId} disabled={busy} onChange={(e) => { setTargetId(e.target.value); setConfirmMerge(false) }}>
      <option value="">请选择合并目标</option>
      {targets.map((row) => <option key={row.character_id} value={row.character_id} title={row.description}>{row.name}</option>)}
    </select></label>
    {target && <p className="hint">目标说明：{target.description || '暂无说明'}</p>}
    {!target && <p className="hint">请选择合并目标后再合并。</p>}
    {!confirmMerge ? <button title={lockReason ?? (!target ? '请先选择另一个全书人物作为合并目标。' : undefined)} type="button" disabled={busy || !target} onClick={() => setConfirmMerge(true)}>合并人物…</button> :
      <div role="alert">
        <p>将“{item.name}”合并到“{target?.name}”？全部已有对白和章节名单将改为目标人物；原姓名及别名保留为别名，使用目标说明。此操作无法自动撤销，未保存的编辑不会应用。</p>
        <button title={lockReason} type="button" className="ndr-danger" disabled={busy} onClick={() => merge.mutate()}>确认合并</button>
        <button title={lockReason} type="button" disabled={busy} onClick={() => setConfirmMerge(false)}>取消</button>
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
  const [visibleFromCp, setVisibleFromCp] = useState<number | null>(null)
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
  const chapters = useQuery({ queryKey: queryKeys.chapters(bookId),
    queryFn: ({ signal }) => fetchChapters(bookId, signal), enabled: Boolean(bookId) })
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
    <section className="card">
      <label className="ndr-field">本次人物修改从哪一章起可见（初读）
        <select value={visibleFromCp ?? ''} disabled={batchProgress.running || autoMergeBusy}
          title={batchProgress.running || autoMergeBusy ? '本书任务或合并决定正在执行，请等待结束或先停止任务，再调整可见章节。' : undefined}
          onChange={event => setVisibleFromCp(event.target.value === '' ? null : Number(event.target.value))}>
          <option value="">全书末尾（默认，避免提前透露身份）</option>
          {(chapters.data ?? []).map(chapter => <option key={chapter.id} value={chapter.end_cp}>
            {chapter.title || `第 ${chapter.ordinal + 1} 章`}结束后
          </option>)}
        </select>
      </label>
      <p className="hint">适用于本次保存资料、手动合并及接受自动合并建议。更早章节保留当时的姓名、说明与不同身份；重读立即显示最终结果。请选择原文已经揭示该信息的章节，不确定时保留默认。</p>
      {chapters.isError && <p role="alert" className="status-error">可见章节读取失败：{chapters.error.message}，可使用全书末尾或重新读取页面。</p>}
    </section>
    <CharacterAutoMerge key={bookId} bookId={bookId} versionId={book.data?.active_version_id} count={entries.length} visibleFromCp={visibleFromCp}
      disabled={batchProgress.running} onBusyChange={setAutoMergeBusy} onSaved={saved} />
    <section className="card">
      <p className="hint">汇总当前书籍版本已识别的人物（包括未发言人物），可能包含后文剧透。人工确认只是来源记录，并不表示永远正确；仍可手动修改，或让模型提出更名、合并与说明修正建议，预览接受后才生效。后台是否更新人工姓名与说明由通用设置决定，默认关闭。修改会影响已有对白、后续人物识别和导出，不改原文或章节完成状态。请先停止本书处理任务再编辑。</p>
      {batchProgress.running && <p role="status">批量处理正在运行，停止后可修改人物。</p>}
      {message && <p role="status">{message}</p>}
      <div className="ndr-toolbar">
        <label className="ndr-field">搜索人物<input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="姓名、别名或说明" /></label>
        <button title={directory.isFetching ? '正在读取人物资料，请等待完成。' : undefined} type="button" disabled={directory.isFetching} onClick={() => void directory.refetch()}>重新读取</button>
      </div>
      {directory.isPending && <p>正在读取全书人物…</p>}
      <p className="hint">按出现章节数从多到少排列，章节数相同时按对白数排列。仅统计已识别章节和当前有效的对白归属，未处理章节不计入。</p>
      {directory.isError && <p role="alert" className="status-error">人物读取失败：{directory.error.message}</p>}
      {directory.data && <p>共 {entries.length} 个人物{term ? `，匹配 ${filtered.length} 个` : ''}</p>}
      {!directory.isPending && !directory.isError && !entries.length && <p>尚未识别人物，请先在预览与处理中分析人物。</p>}
      <CollapsibleBlock title="人物资料列表" summary={`当前显示 ${filtered.length} 个人物`}>
      <div className="ndr-character-list">
        {filtered.map((item) => <CharacterEditor key={`${item.character_id}:${item.version}`} item={item} bookId={bookId}
          visibleFromCp={visibleFromCp}
          disabled={batchProgress.running || autoMergeBusy}
          targets={entries.filter((row) => row.kind !== 'speaker' && row.character_id !== item.character_id)} onSaved={saved} />)}
      </div>
      </CollapsibleBlock>
    </section>
  </div>
}
