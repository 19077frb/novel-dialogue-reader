import type { ChapterOut } from '../api/types'

export interface ChapterNavigationProps {
  chapters: ChapterOut[]
  activeChapterId: string | null
  onSelect: (chapter: ChapterOut) => void
}

export function ChapterNavigation({ chapters, activeChapterId, onSelect }: ChapterNavigationProps) {
  return (
    <nav className="ndr-chapter-nav" aria-label="章节导航">
      <h2>目录</h2>
      {chapters.length === 0 ? (
        <p className="hint">这本书还没有章节。</p>
      ) : (
        <ol>
          {chapters.map((chapter) => {
            const active = chapter.id === activeChapterId
            return (
              <li key={chapter.id}>
                <button
                  type="button"
                  className={active ? 'ndr-chapter active' : 'ndr-chapter'}
                  aria-current={active ? 'true' : undefined}
                  onClick={() => onSelect(chapter)}
                >
                  <span className="ndr-chapter-title">{chapter.title ?? `第 ${chapter.ordinal + 1} 节`}</span>
                  <span className="ndr-chapter-range">
                    {chapter.start_cp}–{chapter.end_cp}
                  </span>
                </button>
              </li>
            )
          })}
        </ol>
      )}
    </nav>
  )
}