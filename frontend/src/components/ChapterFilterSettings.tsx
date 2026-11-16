import type { ChapterFilter } from '../processing/chapterFilter'
import { defaultChapterFilter } from '../processing/chapterFilter'
import { CollapsibleBlock } from './CollapsibleBlock'

export function ChapterFilterSettings({ value, onChange }: { value: ChapterFilter; onChange: (patch: Partial<ChapterFilter>) => void }) {
  return <section className="card" aria-label="章节处理过滤名单">
    <h3>章节处理过滤名单</h3>
    <label><input type="checkbox" checked={value.chapterFilterEnabled}
      onChange={event => onChange({ chapterFilterEnabled: event.target.checked })} />按章节名自动跳过处理</label>
    <p className="hint">按目录中的章节名跳过处理，适用于新建单章、批量和自动任务。</p>
    <div hidden={!value.chapterFilterEnabled}>
    <label className="ndr-field">匹配方式
      <select value={value.chapterFilterMode} onChange={event => onChange({ chapterFilterMode: event.target.value as ChapterFilter['chapterFilterMode'] })}>
        <option value="exact">完全匹配</option><option value="contains">包含匹配</option>
      </select>
    </label>
    <p className="hint">{value.chapterFilterMode === 'exact' ? '完全匹配整条章节名，如“封面”。' : '章节名包含任一关键词时跳过，如“封面”也匹配“第一卷 · 封面”。'}</p>
    <CollapsibleBlock title="编辑过滤名单" summary={`共 ${value.chapterFilterTerms.filter(term => term.trim()).length} 项`}>
      <label className="ndr-field">过滤名单（每行一项）
        <textarea rows={5} value={value.chapterFilterTerms.join('\n')}
          onChange={event => onChange({ chapterFilterTerms: event.target.value.split('\n') })} />
      </label>
      <p className="hint">每行一项，空行忽略；强制重做也遵守名单。删除匹配项或关闭过滤并保存，即可处理相应章节。</p>
    </CollapsibleBlock>
    </div>
    <button onClick={() => {
      if (window.confirm('恢复过滤名单默认值并关闭过滤？其他设置不变，保存后生效。')) onChange(defaultChapterFilter())
    }}>恢复过滤名单默认值</button>
  </section>
}
