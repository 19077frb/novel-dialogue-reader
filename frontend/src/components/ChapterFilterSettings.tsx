import type { ChapterFilter } from '../processing/chapterFilter'
import { defaultChapterFilter } from '../processing/chapterFilter'
import { CollapsibleBlock } from './CollapsibleBlock'

export function ChapterFilterSettings({ value, onChange }: { value: ChapterFilter; onChange: (patch: Partial<ChapterFilter>) => void }) {
  return <section className="card" aria-label="章节处理过滤名单">
    <h3>章节处理过滤名单</h3>
    <label><input type="checkbox" checked={value.chapterFilterEnabled}
      onChange={event => onChange({ chapterFilterEnabled: event.target.checked })} />按章节名自动跳过处理</label>
    <p className="hint">默认关闭。保存后，新单章、批量和自动处理会跳过匹配的章节，仍可正常阅读。已启动的任务保持原设置。</p>
    <label className="ndr-field">匹配方式
      <select value={value.chapterFilterMode} onChange={event => onChange({ chapterFilterMode: event.target.value as ChapterFilter['chapterFilterMode'] })}>
        <option value="exact">完全匹配</option><option value="contains">包含匹配</option>
      </select>
    </label>
    <p className="hint">按目录显示的完整章节名匹配。完全匹配：“封面”只匹配“封面”；包含匹配：也匹配“第一卷 · 封面”。</p>
    <CollapsibleBlock title="编辑过滤名单" summary={`共 ${value.chapterFilterTerms.filter(term => term.trim()).length} 项`}>
      <label className="ndr-field">过滤名单（每行一项）
        <textarea rows={8} value={value.chapterFilterTerms.join('\n')}
          onChange={event => onChange({ chapterFilterTerms: event.target.value.split('\n') })} />
      </label>
      <p className="hint">添加或删除一行即可修改名单，空行会忽略。要处理被跳过的章节，可删除对应项或关闭本功能后保存；强制重做也会遵守名单。</p>
    </CollapsibleBlock>
    <button onClick={() => {
      if (window.confirm('恢复过滤名单默认值并关闭过滤？其他设置不变，保存后生效。')) onChange(defaultChapterFilter())
    }}>恢复过滤名单默认值</button>
  </section>
}
