import { afterEach, expect, it, vi } from 'vitest'
import { LIBRARY_ORDER_KEY, mergeLibraryOrder, orderLibraryBooks, useLibraryLayoutStore, validLibraryOrder } from '../src/stores/libraryLayoutStore'

afterEach(() => { vi.restoreAllMocks(); useLibraryLayoutStore.setState({ order: [] }); localStorage.removeItem(LIBRARY_ORDER_KEY) })

it('validates bounded unique IDs and ignores invalid storage shapes', () => {
  expect(validLibraryOrder({ order: ['a'] })).toEqual([])
  expect(validLibraryOrder(['a', 1, null, '', 'a', 'b', 'x'.repeat(201)])).toEqual(['a', 'b'])
  expect(validLibraryOrder(Array.from({ length: 2100 }, (_, i) => String(i)))).toHaveLength(2000)
})

it('updates only visible IDs, retaining other pages and accommodating new/deleted books', () => {
  expect(mergeLibraryOrder(['a', 'unloaded', 'b'], ['b', 'a', 'new'])).toEqual(['b', 'unloaded', 'a', 'new'])
  expect(orderLibraryBooks([{ id: 'new' }, { id: 'b' }, { id: 'a' }], ['a', 'deleted', 'b'])).toEqual([{ id: 'a' }, { id: 'b' }, { id: 'new' }])
})

it('persists only IDs and remains usable when local storage rejects writes', () => {
  expect(useLibraryLayoutStore.getState().saveOrder(['b', 'a'])).toBe(true)
  expect(JSON.parse(localStorage.getItem(LIBRARY_ORDER_KEY)!)).toEqual(['b', 'a'])
  vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('quota') })
  expect(useLibraryLayoutStore.getState().saveOrder(['a', 'b'])).toBe(false)
  expect(useLibraryLayoutStore.getState().order).toEqual(['a', 'b'])
})
