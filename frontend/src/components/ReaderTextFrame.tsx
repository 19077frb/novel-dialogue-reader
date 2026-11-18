import { useEffect, useId, useLayoutEffect, useRef, useState } from 'react'
import type { KeyboardEvent, PointerEvent, ReactNode } from 'react'
import { flushSync } from 'react-dom'
import { initialReaderTextWidth, MIN_READER_TEXT_WIDTH, useReaderLayoutStore } from '../stores/readerLayoutStore'

type Side = 'left' | 'right'
type Gesture = { side: Side; startWidth: number; startX: number; initialWidth: number; pointerId?: number; handle?: HTMLDivElement }
type Anchor = { container: HTMLElement; node: HTMLElement; offset: number }
const widthKeys = ['ArrowLeft', 'ArrowRight', 'Home', 'End']

/** Resize only the outer reading card; its content and scroll container stay mounted. */
export function ReaderTextFrame({ children }: { children: ReactNode }) {
  const rememberedWidth = useReaderLayoutStore(state => state.textWidth)
  const storageError = useReaderLayoutStore(state => state.textWidthStorageError)
  const rememberWidth = useReaderLayoutStore(state => state.rememberTextWidth)
  const [width, setWidth] = useState(rememberedWidth)
  const widthRef = useRef(width)
  const [availableWidth, setAvailableWidth] = useState(0)
  const availableRef = useRef(0)
  const [narrow, setNarrow] = useState(window.innerWidth <= 900)
  const narrowRef = useRef(narrow)
  const [activeSide, setActiveSide] = useState<Side | null>(null)
  const track = useRef<HTMLDivElement>(null)
  const frame = useRef<HTMLDivElement>(null)
  const gesture = useRef<Gesture | null>(null)
  const anchor = useRef<Anchor | null>(null)
  const adjustedContainer = useRef<HTMLElement | null>(null)
  const unlockFrame = useRef<number | null>(null)
  const contentId = useId()
  const minimum = Math.min(MIN_READER_TEXT_WIDTH, availableWidth)
  const effectiveWidth = narrow ? availableWidth : Math.min(availableWidth, Math.max(minimum, width ?? availableWidth))

  useEffect(() => {
    if (!gesture.current && rememberedWidth !== null && rememberedWidth !== widthRef.current) {
      captureAnchor(); widthRef.current = rememberedWidth; setWidth(rememberedWidth)
    }
  }, [rememberedWidth])
  useLayoutEffect(() => {
    const measure = (synchronous = true) => {
      const available = track.current?.getBoundingClientRect().width ?? 0
      const isNarrow = window.innerWidth <= 900
      if (available === availableRef.current && isNarrow === narrowRef.current) return
      // The desktop card keeps its previous pixel width until this measurement,
      // so capture before committing a smaller available width and reflowing text.
      if (availableRef.current > 0) captureAnchor()
      const update = () => {
        if (isNarrow && gesture.current) finish(false)
        availableRef.current = available; narrowRef.current = isNarrow
        if (!isNarrow && available > 0 && widthRef.current === null) {
          widthRef.current = initialReaderTextWidth(available)
          setWidth(widthRef.current); rememberWidth(widthRef.current)
        }
        setAvailableWidth(available); setNarrow(isNarrow)
      }
      if (synchronous) flushSync(update)
      else update()
    }
    measure(false)
    const onResize = () => measure()
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(onResize)
    if (track.current) observer?.observe(track.current)
    window.addEventListener('resize', onResize)
    return () => { observer?.disconnect(); window.removeEventListener('resize', onResize) }
  }, [])

  function captureAnchor() {
    if (unlockFrame.current !== null) cancelAnimationFrame(unlockFrame.current)
    unlockFrame.current = null
    const container = frame.current?.querySelector<HTMLElement>('.ndr-reader-content')
    if (!container) return
    adjustedContainer.current = container
    container.dataset.layoutAdjusting = 'true'
    const bounds = container.getBoundingClientRect()
    const clipTop = Math.max(0, bounds.top)
    const clipBottom = Math.min(window.innerHeight, bounds.bottom)
    const node = Array.from(container.querySelectorAll<HTMLElement>('[data-node-id]')).find(item => {
      const rect = item.getBoundingClientRect()
      return rect.bottom > clipTop && rect.top < clipBottom
    })
    anchor.current = node ? { container, node, offset: node.getBoundingClientRect().top - clipTop } : null
  }
  function restoreAnchor() {
    const current = anchor.current
    if (current?.node.isConnected) {
      const clipTop = Math.max(0, current.container.getBoundingClientRect().top)
      const bounds = current.node.getBoundingClientRect()
      const lineHeight = parseFloat(getComputedStyle(current.node.querySelector('p') ?? current.node).lineHeight) || 24
      const offset = current.offset <= -bounds.height ? Math.min(0, lineHeight - bounds.height) : current.offset
      current.container.scrollTop += bounds.top - clipTop - offset
    }
    anchor.current = null
  }
  useLayoutEffect(() => {
    restoreAnchor()
    if (adjustedContainer.current && !gesture.current) unlockAfterLayout()
  }, [width, availableWidth, narrow])

  function unlockAfterLayout() {
    if (unlockFrame.current !== null) cancelAnimationFrame(unlockFrame.current)
    unlockFrame.current = requestAnimationFrame(() => {
      restoreAnchor()
      if (adjustedContainer.current) delete adjustedContainer.current.dataset.layoutAdjusting
      adjustedContainer.current = null
      unlockFrame.current = null
    })
  }
  useEffect(() => () => {
    if (unlockFrame.current !== null) cancelAnimationFrame(unlockFrame.current)
    if (adjustedContainer.current) delete adjustedContainer.current.dataset.layoutAdjusting
    const current = gesture.current
    gesture.current = null
    if (current?.pointerId !== undefined && current.handle?.hasPointerCapture?.(current.pointerId)) current.handle.releasePointerCapture(current.pointerId)
  }, [])

  function changeWidth(next: number) {
    captureAnchor()
    const available = availableRef.current
    widthRef.current = Math.min(available, Math.max(Math.min(MIN_READER_TEXT_WIDTH, available), next))
    setWidth(widthRef.current)
  }
  function finish(commit: boolean) {
    const current = gesture.current
    if (!current) return
    gesture.current = null
    if (current.pointerId !== undefined && current.handle?.hasPointerCapture?.(current.pointerId)) current.handle.releasePointerCapture(current.pointerId)
    if (commit && widthRef.current !== null) rememberWidth(widthRef.current)
    else { captureAnchor(); widthRef.current = current.initialWidth; setWidth(current.initialWidth) }
    setActiveSide(null)
    unlockAfterLayout()
  }
  function pointerDown(event: PointerEvent<HTMLDivElement>, side: Side) {
    if (event.button !== 0 || window.innerWidth <= 900 || gesture.current || availableRef.current <= 0) return
    event.preventDefault()
    event.currentTarget.focus({ preventScroll: true })
    gesture.current = { side, startX: event.clientX, startWidth: frame.current?.getBoundingClientRect().width ?? 0,
      initialWidth: widthRef.current ?? availableRef.current, pointerId: event.pointerId, handle: event.currentTarget }
    captureAnchor()
    setActiveSide(side)
    event.currentTarget.setPointerCapture?.(event.pointerId)
  }
  function pointerMove(event: PointerEvent<HTMLDivElement>) {
    const current = gesture.current
    const available = track.current?.getBoundingClientRect().width ?? 0
    if (!current || current.pointerId !== event.pointerId || available <= 0) return
    const delta = (event.clientX - current.startX) * (current.side === 'right' ? 2 : -2)
    changeWidth(current.startWidth + delta)
  }
  function keyDown(event: KeyboardEvent<HTMLDivElement>, side: Side) {
    if (event.key === 'Escape') { event.preventDefault(); finish(false); return }
    if (!widthKeys.includes(event.key) || window.innerWidth <= 900 || gesture.current?.pointerId !== undefined || availableRef.current <= 0) return
    event.preventDefault()
    gesture.current ??= { side, startX: 0, startWidth: 0, initialWidth: widthRef.current ?? availableRef.current }
    setActiveSide(side)
    const direction = (event.key === 'ArrowRight' ? 1 : -1) * (side === 'right' ? 1 : -1)
    changeWidth(event.key === 'Home' ? minimum : event.key === 'End' ? availableWidth : effectiveWidth + direction * (event.shiftKey ? 64 : 16))
  }

  return <div className="ndr-reader-text-canvas" data-testid="reader-text-canvas">
    <div className="ndr-reader-text-track" ref={track} data-testid="reader-text-track">
      <div className="ndr-reader-text-frame" ref={frame} style={{ width: availableWidth > 0 ? `${effectiveWidth}px` : '100%' }}
        data-testid="reader-text-frame" data-resizing={activeSide !== null}>
        {(['left', 'right'] as const).map(side => <div key={side} role="separator" tabIndex={0}
          className={`ndr-reader-text-handle ndr-reader-text-handle-${side}`} data-active={activeSide === side}
          data-testid={`reader-text-${side}-handle`} aria-label={side === 'left' ? '调整正文左边界' : '调整正文右边界'}
          aria-orientation="vertical" aria-controls={contentId} aria-valuemin={minimum} aria-valuemax={availableWidth}
          aria-valuenow={effectiveWidth} aria-valuetext={`正文宽度 ${Math.round(effectiveWidth)} 像素`}
          title="拖动调整正文宽度；方向键微调，Home 最窄，End 或双击铺满当前空间。"
          onPointerDown={event => pointerDown(event, side)} onPointerMove={pointerMove}
          onPointerUp={event => {
            if (gesture.current?.pointerId !== event.pointerId) return
            finish(true)
          }}
          onPointerCancel={() => finish(false)} onLostPointerCapture={() => finish(false)}
          onKeyDown={event => keyDown(event, side)} onKeyUp={event => { if (widthKeys.includes(event.key)) finish(true) }}
          onBlur={() => { if (gesture.current?.pointerId === undefined) finish(true) }}
          onDoubleClick={() => {
            if (window.innerWidth <= 900 || availableWidth <= 0) return
            changeWidth(availableWidth); rememberWidth(availableWidth); unlockAfterLayout()
          }} />)}
        <div id={contentId}>{children}</div>
      </div>
    </div>
    {storageError && <p className="hint" role="status">当前会话已应用正文宽度。浏览器存储不可用，恢复后再次调整可记住宽度。</p>}
  </div>
}
