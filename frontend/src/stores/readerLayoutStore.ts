import { create } from 'zustand'

type ReaderTab = 'chapters' | 'bookmarks'
export const READER_TEXT_WIDTH_KEY = 'ndr:reader-text-width:px:v1'
export const LEGACY_READER_TEXT_WIDTH_KEY = 'ndr:reader-text-width:v1'
export const MIN_READER_TEXT_WIDTH = 320

export function readReaderTextWidth(): number | null {
  try {
    const value: unknown = JSON.parse(localStorage.getItem(READER_TEXT_WIDTH_KEY) ?? 'null')
    return typeof value === 'number' && Number.isFinite(value) && value >= MIN_READER_TEXT_WIDTH ? value : null
  } catch { return null }
}

export function initialReaderTextWidth(available: number): number {
  try {
    const ratio: unknown = JSON.parse(localStorage.getItem(LEGACY_READER_TEXT_WIDTH_KEY) ?? 'null')
    if (typeof ratio === 'number' && Number.isFinite(ratio) && ratio >= 40 && ratio <= 100) {
      return Math.max(MIN_READER_TEXT_WIDTH, available * ratio / 100)
    }
  } catch { /* The current viewport provides the initial width. */ }
  return Math.max(MIN_READER_TEXT_WIDTH, available)
}

/** Only the text width is remembered locally; navigation stays session-only. */
export const useReaderLayoutStore = create<{
  sidebarOpen: boolean
  sidebarSize: number
  tabs: Record<string, ReaderTab>
  textWidth: number | null
  textWidthStorageError: boolean
  rememberTextWidth: (width: number) => void
  toggleSidebar: () => void
  setSidebarSize: (size: number) => void
  setTab: (bookId: string, tab: ReaderTab) => void
}>((set) => ({
  sidebarOpen: true,
  sidebarSize: 25,
  tabs: {},
  textWidth: readReaderTextWidth(),
  textWidthStorageError: false,
  rememberTextWidth: width => {
    if (!Number.isFinite(width)) return
    const textWidth = Math.max(MIN_READER_TEXT_WIDTH, width)
    let textWidthStorageError = false
    try { localStorage.setItem(READER_TEXT_WIDTH_KEY, JSON.stringify(textWidth)) }
    catch { textWidthStorageError = true }
    set({ textWidth, textWidthStorageError })
  },
  toggleSidebar: () => set(state => ({ sidebarOpen: !state.sidebarOpen })),
  setSidebarSize: size => {
    if (!Number.isFinite(size)) return
    const sidebarSize = Math.max(18, Math.min(40, size))
    set(state => state.sidebarSize === sidebarSize ? state : { sidebarSize })
  },
  setTab: (bookId, tab) => set(state => ({ tabs: { ...state.tabs, [bookId]: tab } })),
}))
