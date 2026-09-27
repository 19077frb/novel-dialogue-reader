/** 导出范围选择（T15B）：整本或指定章节。 */
import type { ChapterOut } from '../api/types'

export interface ExportScopePickerProps {
  chapters: ChapterOut[]
  scope: 'book' | 'chapters'
  selectedChapterIds: string[]
  onScopeChange: (scope: 'book' | 'chapters') => void
  onSelectionChange: (chapterIds: string[]) => void
}

export function ExportScopePicker({
  chapters,
  scope,
  selectedChapterIds,
  onScopeChange,
  onSelectionChange,
}: ExportScopePickerProps) {
  const toggle = (chapterId: string) => {
    onSelectionChange(
      selectedChapterIds.includes(chapterId)
        ? selectedChapterIds.filter((item) => item !== chapterId)
        : [...selectedChapterIds, chapterId],
    )
  }

  return (
    <fieldset className="ndr-export-scope" data-testid="export-scope">
      <legend>导出范围</legend>
      <div className="ndr-radio-row">
        <label>
          <input
            type="radio"
            name="export-scope"
            checked={scope === 'book'}
            onChange={() => onScopeChange('book')}
            data-testid="export-scope-book"
          />
          整本
        </label>
        <label>
          <input
            type="radio"
            name="export-scope"
            checked={scope === 'chapters'}
            onChange={() => onScopeChange('chapters')}
            data-testid="export-scope-chapters"
          />
          指定章节
        </label>
      </div>
      {scope === 'chapters' && (
        <ul className="ndr-export-chapters" data-testid="export-chapter-list">
          {chapters.map((chapter) => (
            <li key={chapter.id}>
              <label>
                <input
                  type="checkbox"
                  checked={selectedChapterIds.includes(chapter.id)}
                  onChange={() => toggle(chapter.id)}
                  data-testid={`export-chapter-${chapter.ordinal}`}
                />
                {chapter.ordinal + 1}. {chapter.title}（{chapter.start_cp} – {chapter.end_cp}）
              </label>
            </li>
          ))}
        </ul>
      )}
      {scope === 'chapters' && selectedChapterIds.length === 0 && (
        <p className="hint" data-testid="export-scope-empty">
          还没有选章节：请至少勾选一章，否则无法生成。
        </p>
      )}
    </fieldset>
  )
}