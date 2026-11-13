import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useMemo, useRef, useState } from 'react'
import type { CSSProperties, ReactNode } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'

import { editBookCharacter, fetchCharacterDirectory, mergeBookCharacter, setCharacterColor } from '../api/characters'
import { fetchBook, fetchChapters, queryKeys } from '../api/books'
import type { CharacterDirectoryOut } from '../api/types'
import { useBatchProgress } from '../components/BatchProcessor'
import { CharacterAutoMerge } from '../components/CharacterAutoMerge'
import { CollapsibleBlock } from '../components/CollapsibleBlock'
import { ListPagination, PaginatedItems, useListPagination } from '../components/ListPagination'
import { DisabledHint } from '../components/DisabledHint'
import { colorForIndex } from '../styles/palette'
import { useGeneralSettings } from '../settings/preferences'

const COLOR_NAMES = ['蓝色', '橙色', '绿色', '红色', '紫色', '青色', '金黄色', '粉色',
  '棕色', '墨绿色', '靛蓝色', '酒红色', '橄榄绿', '灰蓝色', '灰紫色', '深灰色']

export function characterPageLayout(width: number) {
  const columns = Math.max(1, Math.min(3, Math.floor((width + 12) / 332)))
  return { columns, pageSize: 6 }
}

interface CharacterDraft {
  base: CharacterDirectoryOut
  name: string; aliases: string; description: string; color: number | null
  targetId: string; targetVersion?: number; confirmMerge: boolean; open: boolean
  pending: boolean; message: string; error: string
}
function newDraft(item: CharacterDirectoryOut): CharacterDraft {
  return { base: item, name: item.name, aliases: (item.aliases ?? []).join('、'),
    description: item.description ?? '', color: item.preferred_color_index ?? null,
    targetId: '', confirmMerge: false, open: false, pending: false, message: '', error: '' }
}
function hasEdits(d: CharacterDraft) {
  return d.name !== d.base.name || d.aliases !== (d.base.aliases ?? []).join('、') ||
    d.description !== (d.base.description ?? '') || d.color !== (d.base.preferred_color_index ?? null)
}
function keepDraft(d: CharacterDraft) { return hasEdits(d) || d.pending || Boolean(d.targetId) || d.confirmMerge }

function CharacterPages({ items, retained, revealedId, renderItem, scope }: {
  items: CharacterDirectoryOut[]; retained: CharacterDirectoryOut[]; revealedId: string | null
  renderItem: (item: CharacterDirectoryOut) => ReactNode; scope: string
}) {
  const container = useRef<HTMLDivElement>(null)
  const [layout, setLayout] = useState(() => characterPageLayout(window.innerWidth - 48))
  useEffect(() => {
    const measure = () => {
      const rect = container.current?.getBoundingClientRect()
      const next = characterPageLayout(rect?.width || window.innerWidth - 48)
      setLayout(old => old.columns === next.columns && old.pageSize === next.pageSize ? old : next)
    }
    measure()
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(measure)
    if (container.current) observer?.observe(container.current)
    window.addEventListener('resize', measure)
    return () => { observer?.disconnect(); window.removeEventListener('resize', measure) }
  }, [])
  const pagination = useListPagination(items.length, layout.pageSize, scope)
  const visible = items.slice(pagination.page * layout.pageSize, (pagination.page + 1) * layout.pageSize)
  const visibleIds = new Set(visible.map(item => item.character_id))
  const mounted = [...visible, ...retained.filter(item => !visibleIds.has(item.character_id))]
  return <div ref={container} className="ndr-character-directory" style={{ '--ndr-character-columns': layout.columns } as CSSProperties}>
    <div className="ndr-character-list">{mounted.map(item => <div key={item.character_id} hidden={!visibleIds.has(item.character_id) && revealedId !== item.character_id}>{renderItem(item)}</div>)}</div>
    <ListPagination key={scope} label="人物资料" pagination={pagination} />
  </div>
}

function CharacterEditor({ item, targets, bookId, onSaved, disabled, visibleFromCp, draft, updateDraft, colorOwners, removed }: {
  removed: boolean
  draft?: CharacterDraft
  updateDraft: (update: (previous: CharacterDraft) => CharacterDraft) => void
  colorOwners: Map<number, CharacterDirectoryOut>
  item: CharacterDirectoryOut
  targets: CharacterDirectoryOut[]
  bookId: string
  onSaved: () => Promise<void>
  disabled: boolean
  visibleFromCp: number | null
}) {
  const d = draft ?? newDraft(item)
  const { name, aliases, description, color, targetId, confirmMerge, message } = d
  const patch = (values: Partial<CharacterDraft>) => updateDraft(previous => ({ ...previous, ...values }))
  const target = targets.find(row => row.character_id === targetId && row.character_id !== item.character_id)
  const begin = () => patch({ pending: true, error: '', message: '' })
  const failed = (error: Error) => patch({ pending: false, error: error.message })
  const completed = async (result: CharacterDirectoryOut, metadata = false) => {
    try { await onSaved() }
    finally { patch({ base: result,
      ...(metadata ? { name: result.name, aliases: (result.aliases ?? []).join('、'), description: result.description ?? '' } : { color: result.preferred_color_index ?? null }),
      pending: false, error: '', message: '人物资料已保存。' }) }
  }
  const save = useMutation({
    mutationFn: () => editBookCharacter(bookId, item.character_id, {
      name: name.trim(), aliases: aliases.split(/[、，,\n]/).map((value) => value.trim()).filter(Boolean),
      description, expected_version: d.base.version ?? 1,
      ...(visibleFromCp == null ? {} : { visible_from_cp: visibleFromCp }),
    }),
    onMutate: begin, onError: failed, onSuccess: result => completed(result, true),
  })
  const merge = useMutation({
    mutationFn: () => mergeBookCharacter(bookId, item.character_id, {
      target_character_id: targetId, expected_version: d.base.version ?? 1,
      expected_target_version: d.targetVersion ?? target?.version ?? 1,
      ...(visibleFromCp == null ? {} : { visible_from_cp: visibleFromCp }),
    }),
    onMutate: begin, onError: failed,
    onSuccess: async result => { try { await onSaved() } finally { updateDraft(() => newDraft(result)) } },
  })
  const saveColor = useMutation({
    mutationFn: () => setCharacterColor(bookId, item.character_id, { color_index: color, expected_version: d.base.version ?? 1 }),
    onMutate: begin, onError: failed, onSuccess: result => completed(result),
  })
  const busy = removed || disabled || d.pending || save.isPending || merge.isPending || saveColor.isPending
  const lockReason = removed ? '该人物已不在最新列表中，请核对合并结果；保留的修改不会自动应用。' : disabled ? '本书任务或合并决定正在执行，请等待结束或先停止任务后再编辑人物。' : d.pending || save.isPending || merge.isPending || saveColor.isPending ? '正在保存人物资料或合并人物，请等待完成。' : undefined
  const dirty = name !== d.base.name || aliases !== (d.base.aliases ?? []).join('、') || description !== (d.base.description ?? '')
  const colorReason = lockReason ?? (item.kind === 'speaker' ? '请先保存人物资料，纳入全书人物后再设置颜色。' : dirty ? '有未保存的人物资料，请先保存，避免改色后丢失编辑。' : color === (item.preferred_color_index ?? null) ? '颜色设置没有变化。' : undefined)
  const error = d.error
  const colorChoices = Array.from({ length: 32 }, (_, index) => index)
  const reserved = new Set(colorOwners.keys())
  for (let index = 32, added = 0; added < 16; index++) {
    if (!reserved.has(index)) { colorChoices.push(index); added++ }
  }
  if (color != null && !colorChoices.includes(color)) colorChoices.push(color)
  if (item.color_index != null && item.color_index >= 32 && !colorChoices.includes(item.color_index)) colorChoices.push(item.color_index)
  return <article className="ndr-character-card" aria-label={`人物 ${item.name}`}>
    <h3 title={item.name} style={{ color: colorForIndex(item.color_index) }}>{item.name}</h3>
    {!disabled && <DisabledHint reason={lockReason} />}
    {item.chapter_count != null && item.dialogue_count != null && <p className="hint">
      出现 {item.chapter_count} 章 · {item.dialogue_count} 句对白
    </p>}
    <p className="hint">{item.kind === 'speaker' ? '尚未关联全书人物；保存后纳入全书人物表。' :
      ({ manual: '已人工确认', automatic: '批量自动确认（未经人工复核）',
        legacy: '已确认（旧记录未区分来源）', imported: '导入恢复的人物',
        model: '模型识别人物' }[item.confirmation_source ?? (item.user_confirmed ? 'legacy' : 'model')])}</p>
    <p className="ndr-character-alias-summary hint" title={(item.aliases ?? []).join('、')}>别名：{item.aliases?.join('、') || '暂无'}</p>
    <p className="ndr-character-description" title={item.description || undefined}>{item.description || '暂无人物说明'}</p>
    {hasEdits(d) && <p className="hint" role="status">有未保存的修改</p>}
    {(item.version ?? 1) > (d.base.version ?? 1) && <p role="alert" className="hint">人物资料已更新，当前草稿仍保留原版本；保存时会核对版本。
      <button type="button" disabled={busy} title={busy ? lockReason : undefined} onClick={() => updateDraft(previous => ({ ...newDraft(item), open: previous.open }))}>放弃草稿并使用最新资料</button>
    </p>}
    <CollapsibleBlock title="编辑与合并" open={d.open} onOpenChange={open => patch({ open })}>
    {(d.open || keepDraft(d)) && <>
    <div className="ndr-character-editor-fields">
    <label className="ndr-field">人物颜色<select value={color ?? ''} disabled={busy || item.kind === 'speaker'}
      title={lockReason ?? (item.kind === 'speaker' ? '请先保存人物资料，纳入全书人物后再设置颜色。' : undefined)}
      onChange={event => patch({ color: event.target.value === '' ? null : Number(event.target.value) })}>
      <option value="">自动分配{item.color_index != null ? `（当前色号 ${item.color_index + 1}）` : ''}</option>
      {colorChoices.map(index => {
        const owner = colorOwners.get(index)
        const otherOwner = owner?.character_id !== item.character_id ? owner : undefined
        return <option key={index} value={index} disabled={Boolean(otherOwner)}>{`${COLOR_NAMES[index] ?? '扩展色'} · 色号 ${index + 1}${otherOwner ? `（${otherOwner.name}已指定）` : ''}`}</option>
      })}
    </select></label>
    <p style={{ color: colorForIndex(color ?? item.color_index) }}>颜色预览：〔{item.name}〕「你好。」</p>
    {color == null && item.preferred_color_index != null && <p className="hint">恢复自动分配后的颜色以保存结果为准。</p>}
    <button type="button" title={colorReason} disabled={Boolean(colorReason)} onClick={() => saveColor.mutate()}>保存颜色</button>
    {!disabled && colorReason !== lockReason && <DisabledHint reason={colorReason} />}
    <label className="ndr-field">姓名<input title={lockReason} value={name} maxLength={128} onChange={(e) => patch({ name: e.target.value })} disabled={busy} /></label>
    <label className="ndr-field">别名（用、分隔）<input title={lockReason} value={aliases} onChange={(e) => patch({ aliases: e.target.value })} disabled={busy} /></label>
    <label className="ndr-field">说明<textarea title={lockReason} value={description} maxLength={512} rows={3} onChange={(e) => patch({ description: e.target.value })} disabled={busy} /></label>
    <button title={lockReason ?? (!name.trim() ? '请先填写人物姓名。' : undefined)} type="button" className="ndr-primary" disabled={busy || !name.trim()} onClick={() => save.mutate()}>保存人物资料</button>
    {!name.trim() && <p className="hint">请填写人物姓名后再保存。</p>}
    <label className="ndr-field">合并到全书人物<select title={lockReason} value={targetId} disabled={busy} onChange={(e) => patch({ targetId: e.target.value, targetVersion: targets.find(row => row.character_id === e.target.value)?.version ?? 1, confirmMerge: false })}>
      <option value="">请选择合并目标</option>
      {targets.filter(row => row.character_id !== item.character_id).map((row) => <option key={row.character_id} value={row.character_id} title={row.description}>{row.name}</option>)}
    </select></label>
    {target && <p className="hint">目标说明：{target.description || '暂无说明'}</p>}
    {!target && <p className="hint">请选择合并目标后再合并。</p>}
    {!confirmMerge ? <button title={lockReason ?? (!target ? '请先选择另一个全书人物作为合并目标。' : undefined)} type="button" disabled={busy || !target} onClick={() => patch({ confirmMerge: true })}>合并人物…</button> :
      <div role="alert">
        <p>将“{item.name}”合并到“{target?.name}”？全部已有对白和章节名单将改为目标人物；原姓名及别名保留为别名，使用目标说明。此操作无法自动撤销，未保存的编辑不会应用。</p>
        <button title={lockReason ?? (!target ? '合并目标已不在最新列表中，请重新选择目标。' : undefined)} type="button" className="ndr-danger" disabled={busy || !target} onClick={() => merge.mutate()}>确认合并</button>
        <button title={lockReason} type="button" disabled={busy} onClick={() => patch({ confirmMerge: false })}>取消</button>
      </div>}
    </div>
    </>}
    </CollapsibleBlock>
    {error && <p role="alert" className="status-error">{error}</p>}
    {message && <p role="status">{message}</p>}
  </article>
}

export default function CharactersPage() {
  const [settings] = useGeneralSettings()
  const { bookId = '' } = useParams<{ bookId: string }>()
  const [searchParams] = useSearchParams()
  const chapterId = searchParams.get('chapterId')
  const chapterQuery = chapterId ? `?chapterId=${encodeURIComponent(chapterId)}` : ''
  const queryClient = useQueryClient()
  const [search, setSearch] = useState('')
  const [drafts, setDrafts] = useState<Record<string, CharacterDraft>>({})
  const [revealedId, setRevealedId] = useState<string | null>(null)
  const [listOpen, setListOpen] = useState(true)
  const [visibleFromCp, setVisibleFromCp] = useState<number | null>(null)
  const effectiveVisibleFromCp = settings.enableExperimentalFeatures ? visibleFromCp : null
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
    queryFn: ({ signal }) => fetchChapters(bookId, signal), enabled: Boolean(bookId) && settings.enableExperimentalFeatures })
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
  const targets = useMemo(() => entries.filter(row => row.kind !== 'speaker'), [entries])
  const colorOwners = useMemo(() => new Map(targets.filter(row => row.preferred_color_index != null)
    .map(row => [row.preferred_color_index!, row] as const)), [targets])
  const draftKey = (id: string) => `${bookId}:${id}`
  const entryIds = new Set(entries.map(item => item.character_id))
  const retained = Object.entries(drafts).filter(([key, draft]) => key === draftKey(draft.base.character_id) && keepDraft(draft))
    .map(([, draft]) => entries.find(item => item.character_id === draft.base.character_id) ?? draft.base)
  const removedDrafts = retained.filter(item => !entryIds.has(item.character_id))
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
    {settings.enableExperimentalFeatures && <section className="card">
      <label className="ndr-field">本次人物修改从哪一章起可见（初读·试验）
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
    </section>}
    <CharacterAutoMerge key={bookId} bookId={bookId} versionId={book.data?.active_version_id} count={entries.length} visibleFromCp={effectiveVisibleFromCp}
      disabled={batchProgress.running} onBusyChange={setAutoMergeBusy} onSaved={saved} />
    <section className="card">
      <CollapsibleBlock title="使用说明" defaultOpen={false}>
      <p className="hint">汇总当前书籍版本已识别的人物（包括未发言人物），可能包含后文剧透。人工确认只是来源记录，并不表示永远正确；仍可手动修改，或让模型提出更名、合并与说明修正建议，预览接受后才生效。后台是否更新人工姓名与说明由通用设置决定，默认关闭。修改会影响已有对白、后续人物识别和导出，不改原文或章节完成状态。请先停止本书处理任务再编辑。</p>
      <p className="hint">颜色由程序生成，保存不调用模型、不更改人工确认状态。选择“自动分配”可恢复；自动占用的色号会为手动选择让位。改色在所有阅读模式中均生效。</p>
      </CollapsibleBlock>
      {(batchProgress.running || autoMergeBusy) && <p role="status">本书任务或合并决定正在执行，请等待结束或先停止任务后再编辑人物。</p>}
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
      {removedDrafts.length > 0 && <CollapsibleBlock title="已移除人物的未保存修改" summary={`有 ${removedDrafts.length} 个人物已不在最新列表中，保留的修改不会自动应用。`}>
        <PaginatedItems label="已移除人物草稿" pageSize={5} scope={bookId}>
          {removedDrafts.map(item => <div key={item.character_id}>
            <p>“{item.name}”已不在最新人物列表中，请核对合并结果。</p>
            <button type="button" onClick={() => {
              setRevealedId(item.character_id); setListOpen(true)
              setDrafts(previous => ({ ...previous, [draftKey(item.character_id)]: { ...previous[draftKey(item.character_id)], open: true } }))
            }}>查看保留的修改</button>
            <button type="button" disabled={drafts[draftKey(item.character_id)]?.pending}
              title={drafts[draftKey(item.character_id)]?.pending ? '正在保存人物资料或合并人物，请等待完成后再放弃草稿。' : undefined}
              onClick={() => setDrafts(previous => { const next = { ...previous }; delete next[draftKey(item.character_id)]; return next })}>放弃此人物草稿</button>
          </div>)}
        </PaginatedItems>
      </CollapsibleBlock>}
      <CollapsibleBlock title="人物资料列表" open={listOpen} onOpenChange={setListOpen} summary={`当前显示 ${filtered.length} 个人物`}>
      <CharacterPages scope={`${bookId}:${term}`} items={filtered} retained={retained} revealedId={revealedId} renderItem={item =>
        <CharacterEditor item={item} bookId={bookId} removed={!entryIds.has(item.character_id)} draft={drafts[draftKey(item.character_id)]}
          updateDraft={update => setDrafts(previous => ({ ...previous, [draftKey(item.character_id)]: update(previous[draftKey(item.character_id)] ?? newDraft(item)) }))}
          colorOwners={colorOwners} targets={targets} visibleFromCp={effectiveVisibleFromCp}
          disabled={batchProgress.running || autoMergeBusy} onSaved={saved} />
      } />
      </CollapsibleBlock>
    </section>
  </div>
}
