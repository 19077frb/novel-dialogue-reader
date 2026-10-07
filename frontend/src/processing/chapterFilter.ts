export interface ChapterFilter {
  chapterFilterEnabled: boolean
  chapterFilterMode: 'exact' | 'contains'
  chapterFilterTerms: string[]
}

export function defaultChapterFilter(): ChapterFilter {
  return { chapterFilterEnabled: false, chapterFilterMode: 'contains',
    chapterFilterTerms: ['封面', '书名', '制作信息', '版权信息', '简介', '彩页', '目录', '插画', '插图'] }
}

export function normalizeChapterFilter(value: Partial<ChapterFilter>): ChapterFilter {
  return { chapterFilterEnabled: value.chapterFilterEnabled === true,
    chapterFilterMode: value.chapterFilterMode === 'exact' ? 'exact' : 'contains',
    chapterFilterTerms: Array.isArray(value.chapterFilterTerms)
      ? [...new Set(value.chapterFilterTerms.filter((term): term is string => typeof term === 'string').map(term => term.trim()).filter(Boolean))]
      : defaultChapterFilter().chapterFilterTerms }
}

export function chapterFilterReason(title: string | null | undefined, filter: ChapterFilter): string | null {
  if (!filter.chapterFilterEnabled || !title?.trim()) return null
  const name = title.trim()
  const term = filter.chapterFilterTerms.find(value => value.trim() && (filter.chapterFilterMode === 'exact'
    ? name === value.trim() : name.includes(value.trim())))
  return term ? `自动跳过处理：章节名${filter.chapterFilterMode === 'exact' ? '完全匹配' : '包含'}“${term.trim()}”。可在通用设置调整过滤名单。` : null
}
