import { create } from 'zustand'

type ReaderTab = 'chapters' | 'bookmarks'

/** Session-only presentation state. Reading progress and tasks remain server owned. */
export const useReaderLayoutStore = create<{
  sidebarOpen: boolean
  sidebarSize: number
  tabs: Record<string, ReaderTab>
  toggleSidebar: () => void
  setSidebarSize: (size: number) => void
  setTab: (bookId: string, tab: ReaderTab) => void
}>((set) => ({
  sidebarOpen: true,
  sidebarSize: 25,
  tabs: {},
  toggleSidebar: () => set(state => ({ sidebarOpen: !state.sidebarOpen })),
  setSidebarSize: size => {
    if (!Number.isFinite(size)) return
    const sidebarSize = Math.max(18, Math.min(40, size))
    set(state => state.sidebarSize === sidebarSize ? state : { sidebarSize })
  },
  setTab: (bookId, tab) => set(state => ({ tabs: { ...state.tabs, [bookId]: tab } })),
}))
