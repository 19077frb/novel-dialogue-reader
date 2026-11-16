import { create } from 'zustand'

export const LIBRARY_ORDER_KEY = 'ndr.library.order.v1'
const MAX_IDS = 2000
export function validLibraryOrder(value: unknown): string[] {
  if (!Array.isArray(value)) return []
  return [...new Set(value.filter((id): id is string => typeof id === 'string' && id.length > 0 && id.length <= 200))].slice(0, MAX_IDS)
}
function readOrder(): string[] {
  try {
    const raw = localStorage.getItem(LIBRARY_ORDER_KEY)
    return raw && raw.length < 500_000 ? validLibraryOrder(JSON.parse(raw)) : []
  } catch { return [] }
}

/** Reorder only the loaded subset; retain positions and IDs from other pages. */
export function mergeLibraryOrder(previous: string[], current: string[]): string[] {
  const visible = validLibraryOrder(current)
  const visibleSet = new Set(visible)
  let next = 0
  const merged = previous.map(id => visibleSet.has(id) ? visible[next++] : id)
  return [...merged, ...visible.slice(next)].slice(0, MAX_IDS)
}

export function orderLibraryBooks<T extends { id: string }>(books: T[], order: string[]): T[] {
  const rank = new Map(order.map((id, index) => [id, index]))
  return [...books].sort((a, b) => (rank.get(a.id) ?? Infinity) - (rank.get(b.id) ?? Infinity))
}

interface LibraryLayoutState {
  order: string[]
  saveOrder: (ids: string[]) => boolean
}
export const useLibraryLayoutStore = create<LibraryLayoutState>((set, get) => ({
  order: readOrder(),
  saveOrder: ids => {
    const order = mergeLibraryOrder(get().order, ids)
    set({ order })
    try { localStorage.setItem(LIBRARY_ORDER_KEY, JSON.stringify(order)); return true }
    catch { return false }
  },
}))
