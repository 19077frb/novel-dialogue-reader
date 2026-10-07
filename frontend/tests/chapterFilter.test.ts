import { beforeEach, expect, it } from 'vitest'
import { chapterFilterReason, defaultChapterFilter, normalizeChapterFilter } from '../src/processing/chapterFilter'
import { getGeneralSettings, SETTINGS_KEY, updateGeneralSettings } from '../src/settings/preferences'

beforeEach(() => localStorage.clear())

it('defaults off, including previously saved settings without filter fields', () => {
  expect(chapterFilterReason('封面', defaultChapterFilter())).toBeNull()
  localStorage.setItem(SETTINGS_KEY, JSON.stringify({ fontSize: 20 }))
  expect(getGeneralSettings()).toMatchObject({ fontSize: 20, chapterFilterEnabled: false, chapterFilterMode: 'contains' })
  expect(getGeneralSettings().chapterFilterTerms).toContain('彩页')
})

it('matches complete titles or literal substrings without regular expressions', () => {
  const filter = { ...defaultChapterFilter(), chapterFilterEnabled: true }
  expect(chapterFilterReason(' 第一卷 · 封面 ', filter)).toContain('包含“封面”')
  expect(chapterFilterReason('第一卷 · 封面', { ...filter, chapterFilterMode: 'exact' })).toBeNull()
  expect(chapterFilterReason('封面', { ...filter, chapterFilterMode: 'exact' })).toContain('完全匹配')
  expect(chapterFilterReason('第一章', filter)).toBeNull()
  expect(chapterFilterReason(null, filter)).toBeNull()
  expect(chapterFilterReason('正文', { ...filter, chapterFilterTerms: ['', ' ', '.*'] })).toBeNull()
})

it('normalizes duplicates and blanks and saves one shared list', () => {
  expect(normalizeChapterFilter({ chapterFilterTerms: ['封面', ' 封面 ', '', '  ', '目录'] }).chapterFilterTerms).toEqual(['封面', '目录'])
  updateGeneralSettings({ chapterFilterEnabled: true, chapterFilterMode: 'exact', chapterFilterTerms: [' 封面 ', '', '封面'] })
  expect(getGeneralSettings()).toMatchObject({ chapterFilterEnabled: true, chapterFilterMode: 'exact', chapterFilterTerms: ['封面'] })
})
