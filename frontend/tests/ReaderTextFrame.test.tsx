import { act, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { ReaderTextFrame } from '../src/components/ReaderTextFrame'
import { ReaderWorkspace } from '../src/components/ReaderWorkspace'
import { LEGACY_READER_TEXT_WIDTH_KEY, readReaderTextWidth, READER_TEXT_WIDTH_KEY, useReaderLayoutStore } from '../src/stores/readerLayoutStore'

let frames: Map<number, FrameRequestCallback>
let frameId = 0
let trackWidth: number
let onTrackResize: () => void
const rect = (top: number, width = 1000, height = 400) => ({ top, bottom: top + height, left: 0, right: width,
  width, height, x: 0, y: top, toJSON: () => ({}) })
function flushFrame() {
  act(() => { const pending = [...frames.values()]; frames.clear(); pending.forEach(callback => callback(0)) })
}
function setup() {
  return render(<ReaderTextFrame><div className="card ndr-reader-main"><section className="ndr-reader-content" data-testid="reading">
    <div data-node-id="first">一段足够长的正文</div><textarea aria-label="草稿" defaultValue="保留的草稿" />
  </section></div></ReaderTextFrame>)
}
beforeEach(() => {
  localStorage.clear()
  useReaderLayoutStore.setState({ textWidth: 1000, textWidthStorageError: false, sidebarOpen: true, sidebarSize: 25, tabs: {} })
  trackWidth = 1000
  onTrackResize = () => {}
  Object.defineProperty(window, 'innerWidth', { configurable: true, value: 1440 })
  frames = new Map()
  vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => { frames.set(++frameId, callback); return frameId })
  vi.stubGlobal('cancelAnimationFrame', (id: number) => frames.delete(id))
  vi.stubGlobal('PointerEvent', class extends MouseEvent {
    pointerId: number
    constructor(type: string, init: PointerEventInit) { super(type, init); this.pointerId = init.pointerId ?? 1 }
  })
  vi.stubGlobal('ResizeObserver', class {
    constructor(private callback: () => void) {}
    observe(target: HTMLElement) { if (target.classList.contains('ndr-reader-text-track')) onTrackResize = this.callback }
    unobserve() {}
    disconnect() {}
  })
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
    if (this.classList.contains('ndr-reader-text-track')) return rect(0, trackWidth)
    if (this.classList.contains('ndr-reader-text-frame')) return rect(0, this.style.width === '100%' ? trackWidth : parseFloat(this.style.width))
    if (this.classList.contains('ndr-reader-content')) return rect(100)
    if (this.dataset.nodeId) {
      const frame = this.closest<HTMLElement>('.ndr-reader-text-frame')!
      const section = this.closest<HTMLElement>('.ndr-reader-content')!
      return rect(200 + (1000 - frame.getBoundingClientRect().width) / 2 - section.scrollTop)
    }
    return rect(0)
  })
})
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); localStorage.clear() })

it.each(['left', 'right'] as const)('%s 外侧拖动居中调宽，松手才记忆且保持可见节点及DOM', side => {
  setup()
  const handle = screen.getByTestId(`reader-text-${side}-handle`)
  const section = screen.getByTestId('reading')
  const node = section.querySelector<HTMLElement>('[data-node-id]')!
  const draft = screen.getByLabelText('草稿')
  section.scrollTop = 120
  const top = node.getBoundingClientRect().top
  const write = vi.spyOn(Storage.prototype, 'setItem')
  const start = side === 'left' ? 0 : 1000
  fireEvent.pointerDown(handle, { pointerId: 1, button: 0, clientX: start })
  fireEvent.pointerMove(handle, { pointerId: 1, clientX: side === 'left' ? 100 : 900 })
  expect(screen.getByTestId('reader-text-frame')).toHaveStyle({ width: '800px' })
  expect(section.scrollTop).toBe(220)
  expect(node.getBoundingClientRect().top).toBe(top)
  expect(section).toHaveAttribute('data-layout-adjusting', 'true')
  expect(write).not.toHaveBeenCalled()
  fireEvent.pointerUp(handle, { pointerId: 1 })
  expect(JSON.parse(localStorage.getItem(READER_TEXT_WIDTH_KEY)!)).toBe(800)
  expect(section).toHaveAttribute('data-layout-adjusting', 'true')
  flushFrame()
  expect(section).not.toHaveAttribute('data-layout-adjusting')
  expect(screen.getByTestId('reading')).toBe(section)
  expect(screen.getByLabelText('草稿')).toBe(draft)
  expect(draft).toHaveValue('保留的草稿')
})

it.each(['pointerCancel', 'lostPointerCapture', 'escape'] as const)('%s 恢复原宽度并清理调整标记', cancellation => {
  setup()
  const handle = screen.getByTestId('reader-text-right-handle')
  const release = vi.fn()
  handle.hasPointerCapture = () => true
  handle.releasePointerCapture = release
  fireEvent.pointerDown(handle, { pointerId: 1, button: 0, clientX: 1000 })
  fireEvent.pointerMove(handle, { pointerId: 1, clientX: 900 })
  if (cancellation === 'escape') fireEvent.keyDown(handle, { key: 'Escape' })
  else fireEvent[cancellation](handle, { pointerId: 1 })
  flushFrame()
  expect(screen.getByTestId('reader-text-frame')).toHaveStyle({ width: '1000px' })
  expect(screen.getByTestId('reader-text-frame')).toHaveAttribute('data-resizing', 'false')
  expect(screen.getByTestId('reading')).not.toHaveAttribute('data-layout-adjusting')
  expect(localStorage.getItem(READER_TEXT_WIDTH_KEY)).toBeNull()
  expect(release).toHaveBeenCalledWith(1)
})

it('键盘方向、Home/End和双击共用限制及完成记忆，左边界方向一致', () => {
  setup()
  const left = screen.getByRole('separator', { name: '调整正文左边界' })
  fireEvent.keyDown(left, { key: 'ArrowRight' })
  expect(left).toHaveAttribute('aria-valuenow', '984')
  expect(localStorage.getItem(READER_TEXT_WIDTH_KEY)).toBeNull()
  fireEvent.keyUp(left, { key: 'ArrowRight' })
  expect(readReaderTextWidth()).toBe(984)
  fireEvent.keyDown(left, { key: 'Home' }); fireEvent.keyUp(left, { key: 'Home' })
  expect(readReaderTextWidth()).toBe(320)
  fireEvent.keyDown(left, { key: 'End' }); fireEvent.keyUp(left, { key: 'End' })
  expect(readReaderTextWidth()).toBe(1000)
  fireEvent.keyDown(left, { key: 'ArrowRight', shiftKey: true }); fireEvent.keyUp(left, { key: 'ArrowRight' })
  expect(readReaderTextWidth()).toBe(936)
  fireEvent.doubleClick(left)
  expect(readReaderTextWidth()).toBe(1000)
  flushFrame()
  expect(screen.getByTestId('reading')).not.toHaveAttribute('data-layout-adjusting')
})

it('窄屏取消在途拖动且保留桌面记忆，不接受新的调宽操作', () => {
  useReaderLayoutStore.setState({ textWidth: 800 })
  setup()
  const handle = screen.getByTestId('reader-text-right-handle')
  fireEvent.pointerDown(handle, { pointerId: 1, button: 0, clientX: 1000 })
  fireEvent.pointerMove(handle, { pointerId: 1, clientX: 900 })
  Object.defineProperty(window, 'innerWidth', { configurable: true, value: 800 })
  fireEvent.resize(window)
  flushFrame()
  expect(screen.getByTestId('reader-text-frame')).toHaveStyle({ width: '1000px' })
  expect(screen.getByTestId('reader-text-frame')).toHaveAttribute('data-resizing', 'false')
  fireEvent.keyDown(handle, { key: 'Home' }); fireEvent.keyUp(handle, { key: 'Home' })
  expect(useReaderLayoutStore.getState().textWidth).toBe(800)
  expect(localStorage.getItem(READER_TEXT_WIDTH_KEY)).toBeNull()
  Object.defineProperty(window, 'innerWidth', { configurable: true, value: 1440 })
  fireEvent.resize(window)
  expect(screen.getByTestId('reader-text-frame')).toHaveStyle({ width: '800px' })
})

it('长段加宽后高度缩小，仍保留原可见段的一行', () => {
  useReaderLayoutStore.setState({ textWidth: 400 })
  setup()
  const section = screen.getByTestId('reading')
  const frame = screen.getByTestId('reader-text-frame')
  const node = section.querySelector<HTMLElement>('[data-node-id]')!
  vi.spyOn(node, 'getBoundingClientRect').mockImplementation(() => rect(100 - section.scrollTop, 1000,
    parseFloat(frame.style.width) === 1000 ? 300 : 1000))
  section.scrollTop = 500
  const handle = screen.getByTestId('reader-text-right-handle')
  fireEvent.keyDown(handle, { key: 'End' }); fireEvent.keyUp(handle, { key: 'End' })
  flushFrame()
  expect(node.getBoundingClientRect().bottom).toBeGreaterThan(section.getBoundingClientRect().top)
  expect(section.scrollTop).toBeLessThan(500)
})

it('末行部分可见时微调宽度保留原偏移', () => {
  setup()
  const section = screen.getByTestId('reading')
  const node = section.querySelector<HTMLElement>('[data-node-id]')!
  vi.spyOn(node, 'getBoundingClientRect').mockImplementation(() => rect(100 - section.scrollTop, 1000, 60))
  section.scrollTop = 50
  const handle = screen.getByTestId('reader-text-right-handle')
  fireEvent.keyDown(handle, { key: 'ArrowLeft' }); fireEvent.keyUp(handle, { key: 'ArrowLeft' })
  flushFrame()
  expect(section.scrollTop).toBe(50)
})

it('存储失败仍保留会话宽度并提示，恢复存储后下一次完成操作记忆', () => {
  setup()
  const write = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('不可写') })
  const handle = screen.getByTestId('reader-text-right-handle')
  fireEvent.keyDown(handle, { key: 'Home' }); fireEvent.keyUp(handle, { key: 'Home' })
  expect(useReaderLayoutStore.getState().textWidth).toBe(320)
  expect(screen.getByRole('status')).toHaveTextContent('当前会话已应用正文宽度')
  write.mockRestore()
  fireEvent.keyDown(handle, { key: 'End' }); fireEvent.keyUp(handle, { key: 'End' })
  expect(readReaderTextWidth()).toBe(1000)
  expect(screen.queryByRole('status')).not.toBeInTheDocument()
})

it('卸载时清理调整标记及待执行帧，正文节点被替换时结束也可清理', () => {
  const page = setup()
  const section = screen.getByTestId('reading')
  const handle = screen.getByTestId('reader-text-right-handle')
  fireEvent.keyDown(handle, { key: 'Home' })
  fireEvent.keyUp(handle, { key: 'Home' })
  section.querySelector('[data-node-id]')!.remove()
  page.unmount()
  expect(section).not.toHaveAttribute('data-layout-adjusting')
  expect(frames.size).toBe(0)
})

it.each([null, 'null', 'false', '"800"', 'NaN', '319', '-1', '[]', '{}'])('无效像素记忆 %s 等待首次宽屏测量', raw => {
  if (raw !== null) localStorage.setItem(READER_TEXT_WIDTH_KEY, raw)
  expect(readReaderTextWidth()).toBeNull()
})

it('首次宽屏记忆可用像素，导航开关保持实际宽度，手动铺满后按空间夹紧并恢复', () => {
  useReaderLayoutStore.setState({ textWidth: null })
  render(<ReaderWorkspace navigation={<textarea aria-label="书签草稿" defaultValue="待保存" />}>
    <section className="ndr-reader-content" data-testid="reading"><div data-node-id="first">正文</div></section>
  </ReaderWorkspace>)
  const frame = screen.getByTestId('reader-text-frame')
  const track = screen.getByTestId('reader-text-track')
  vi.spyOn(track, 'getBoundingClientRect').mockImplementation(() => rect(0,
    track.closest<HTMLElement>('.ndr-reader-workspace')!.dataset.navigationOpen === 'false' ? 1400 : 1000))
  const section = screen.getByTestId('reading')
  const draft = screen.getByLabelText('书签草稿')
  expect(readReaderTextWidth()).toBe(1000)
  fireEvent.click(screen.getByRole('button', { name: '隐藏阅读导航' }))
  act(onTrackResize)
  expect(frame).toHaveStyle({ width: '1000px' })
  expect(readReaderTextWidth()).toBe(1000)
  const handle = screen.getByTestId('reader-text-right-handle')
  fireEvent.keyDown(handle, { key: 'End' }); fireEvent.keyUp(handle, { key: 'End' })
  expect(readReaderTextWidth()).toBe(1400)
  flushFrame()
  section.scrollTop = 0
  const node = section.querySelector<HTMLElement>('[data-node-id]')!
  vi.spyOn(node, 'getBoundingClientRect').mockImplementation(() => rect(200 + (1400 - frame.getBoundingClientRect().width) / 2 - section.scrollTop))
  const top = node.getBoundingClientRect().top
  fireEvent.click(screen.getByRole('button', { name: '显示阅读导航' }))
  act(onTrackResize)
  expect(frame).toHaveStyle({ width: '1000px' })
  expect(node.getBoundingClientRect().top).toBe(top)
  expect(section).toHaveAttribute('data-layout-adjusting', 'true')
  expect(readReaderTextWidth()).toBe(1400)
  expect(handle).toHaveAttribute('aria-valuemax', '1000')
  expect(handle).toHaveAttribute('aria-valuenow', '1000')
  flushFrame()
  fireEvent.click(screen.getByRole('button', { name: '隐藏阅读导航' }))
  act(onTrackResize)
  expect(frame).toHaveStyle({ width: '1400px' })
  expect(screen.getByTestId('reading')).toBe(section)
  expect(screen.getByLabelText('书签草稿')).toBe(draft)
  expect(draft).toHaveValue('待保存')
})

it('首次窄屏保持全宽且不初始化桌面值，宽屏初始化后缩窄和恢复沿用期望宽度', () => {
  useReaderLayoutStore.setState({ textWidth: null })
  Object.defineProperty(window, 'innerWidth', { configurable: true, value: 800 })
  trackWidth = 700
  setup()
  expect(readReaderTextWidth()).toBeNull()
  expect(useReaderLayoutStore.getState().textWidth).toBeNull()
  expect(screen.getByTestId('reader-text-frame')).toHaveStyle({ width: '700px' })
  Object.defineProperty(window, 'innerWidth', { configurable: true, value: 1440 })
  trackWidth = 1000
  fireEvent.resize(window)
  expect(readReaderTextWidth()).toBe(1000)
  trackWidth = 700
  act(onTrackResize)
  expect(screen.getByTestId('reader-text-frame')).toHaveStyle({ width: '700px' })
  expect(readReaderTextWidth()).toBe(1000)
  trackWidth = 1400
  act(onTrackResize)
  expect(screen.getByTestId('reader-text-frame')).toHaveStyle({ width: '1000px' })
  Object.defineProperty(window, 'innerWidth', { configurable: true, value: 800 })
  trackWidth = 750
  fireEvent.resize(window)
  expect(screen.getByTestId('reader-text-frame')).toHaveStyle({ width: '750px' })
  expect(readReaderTextWidth()).toBe(1000)
})

it('旧比例首次宽屏转换，像素记忆在重载和不同空间下保持', () => {
  localStorage.setItem(LEGACY_READER_TEXT_WIDTH_KEY, '80')
  useReaderLayoutStore.setState({ textWidth: readReaderTextWidth() })
  const page = setup()
  expect(readReaderTextWidth()).toBe(800)
  expect(screen.getByTestId('reader-text-frame')).toHaveStyle({ width: '800px' })
  page.unmount()
  trackWidth = 1200
  useReaderLayoutStore.setState({ textWidth: readReaderTextWidth() })
  setup()
  expect(screen.getByTestId('reader-text-frame')).toHaveStyle({ width: '800px' })
  expect(readReaderTextWidth()).toBe(800)
})

it('父空间小于最小宽度时实际ARIA范围仍一致，取消操作保留原期望值', () => {
  trackWidth = 250
  setup()
  const handle = screen.getByTestId('reader-text-right-handle')
  expect(handle).toHaveAttribute('aria-valuemin', '250')
  expect(handle).toHaveAttribute('aria-valuemax', '250')
  expect(handle).toHaveAttribute('aria-valuenow', '250')
  fireEvent.keyDown(handle, { key: 'Home' })
  fireEvent.keyDown(handle, { key: 'Escape' })
  trackWidth = 1100
  act(onTrackResize)
  expect(screen.getByTestId('reader-text-frame')).toHaveStyle({ width: '1000px' })
})
