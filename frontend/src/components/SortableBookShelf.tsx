import { useState } from 'react'
import { DndContext, KeyboardSensor, PointerSensor, closestCenter, useSensor, useSensors } from '@dnd-kit/core'
import { SortableContext, arrayMove, rectSortingStrategy, sortableKeyboardCoordinates, useSortable } from '@dnd-kit/sortable'
import { ArrowLeft, ArrowRight, GripVertical, ListOrdered, Save, X } from 'lucide-react'
import { motion, useReducedMotion } from 'motion/react'
import type { BookOut } from '../api/types'
import { orderLibraryBooks, useLibraryLayoutStore } from '../stores/libraryLayoutStore'
import { BookCard } from './BookCard'
import { Button } from './ui/button'
import '../styles/library.css'

function SortableBook({ book, editing, index, count, move, onDelete, deleting }: {
  book: BookOut; editing: boolean; index: number; count: number; move: (from: number, to: number) => void
  onDelete: (book: BookOut) => void; deleting: boolean
}) {
  const reducedMotion = useReducedMotion()
  const { attributes, listeners, setNodeRef, setActivatorNodeRef, transform, isDragging } = useSortable({ id: book.id, disabled: !editing, transition: null })
  return <motion.div ref={setNodeRef} className={`ndr-sortable-book${isDragging ? ' ndr-book-dragging' : ''}`}
    initial={false} animate={{ x: transform?.x ?? 0, y: transform?.y ?? 0,
      scaleX: Number.isFinite(transform?.scaleX) ? transform!.scaleX : 1,
      scaleY: Number.isFinite(transform?.scaleY) ? transform!.scaleY : 1 }}
    transition={{ duration: reducedMotion || isDragging ? 0 : .15 }}>
    {editing && <div className="ndr-book-sort-controls">
      <Button ref={setActivatorNodeRef} size="icon" className="ndr-book-drag-handle" {...attributes} {...listeners}
        aria-label={`拖动《${book.title}》调整顺序`} aria-roledescription="可排序书籍" title="按空格开始，方向键移动，空格放下，Escape 取消。"><GripVertical size={16} aria-hidden="true" /></Button>
      <span className="hint">{index + 1} / {count}</span>
      <Button size="icon" aria-label={`前移《${book.title}》`} disabled={index === 0} title={index === 0 ? '已是当前列表第一本。' : '向前移动一本。'} onClick={() => move(index, index - 1)}><ArrowLeft size={15} aria-hidden="true" /></Button>
      <Button size="icon" aria-label={`后移《${book.title}》`} disabled={index === count - 1} title={index === count - 1 ? '已是当前列表最后一本。' : '向后移动一本。'} onClick={() => move(index, index + 1)}><ArrowRight size={15} aria-hidden="true" /></Button>
    </div>}
    <BookCard book={book} onDelete={onDelete} deleting={deleting} />
  </motion.div>
}

export function SortableBookShelf({ books, onDelete, deletingId }: {
  books: BookOut[]; onDelete: (book: BookOut) => void; deletingId?: string
}) {
  const order = useLibraryLayoutStore(state => state.order)
  const saveOrder = useLibraryLayoutStore(state => state.saveOrder)
  const [draft, setDraft] = useState<string[] | null>(null)
  const [notice, setNotice] = useState('')
  const editing = draft !== null
  const ordered = orderLibraryBooks(books, draft ?? order)
  const ids = ordered.map(book => book.id)
  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 6 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }))
  const move = (from: number, to: number) => {
    if (from >= 0 && to >= 0 && to < ids.length) setDraft(arrayMove(ids, from, to))
  }
  const title = (id: string | number) => books.find(book => book.id === id)?.title ?? '书籍'
  return <>
    <div className="ndr-library-toolbar">
      <div><h2>全部书籍 <span className="hint">{books.length} 本</span></h2>
        <p className="hint">{editing ? '拖动手柄或使用前移、后移按钮调整顺序，点击保存后保存在本浏览器。' : '选一本书，继续你的阅读。'}</p></div>
      {editing ? <div className="ndr-form-actions">
        <Button onClick={() => { setDraft(null); setNotice('已取消整理，顺序未保存。') }}><X size={15} aria-hidden="true" />取消</Button>
        <Button variant="primary" onClick={() => { const saved = saveOrder(ids); setDraft(null); setNotice(saved ? '书架顺序已保存在本浏览器。' : '浏览器无法保存顺序，本次打开仍可使用；刷新后可能恢复。') }}><Save size={15} aria-hidden="true" />保存顺序</Button>
      </div> : <Button onClick={() => { setDraft(ids); setNotice('') }}><ListOrdered size={16} aria-hidden="true" />整理书架</Button>}
    </div>
    {notice && <p className="hint" role="status">{notice}</p>}
    <DndContext sensors={sensors} collisionDetection={closestCenter}
      accessibility={{ screenReaderInstructions: { draggable: '按空格开始移动书籍，使用方向键调整位置，再按空格放下，按 Escape 取消本次拖动。' },
        announcements: {
          onDragStart: ({ active }) => `已拿起《${title(active.id)}》。`,
          onDragOver: ({ active, over }) => over ? `《${title(active.id)}》将移到第 ${ids.indexOf(String(over.id)) + 1} 本的位置。` : '已离开可放置区域。',
          onDragEnd: ({ active, over }) => over ? `已调整《${title(active.id)}》的位置，点击保存顺序后生效。` : '已保留当前顺序。',
          onDragCancel: () => '已取消本次拖动。',
        } }}
      onDragEnd={({ active, over }) => { if (editing && over && active.id !== over.id) move(ids.indexOf(String(active.id)), ids.indexOf(String(over.id))) }}>
      <SortableContext items={ids} strategy={rectSortingStrategy}>
        <div className="ndr-book-grid">
          {ordered.map((book, index) => <SortableBook key={book.id} book={book} editing={editing} index={index} count={books.length}
            move={move} onDelete={onDelete} deleting={deletingId === book.id} />)}
        </div>
      </SortableContext>
    </DndContext>
  </>
}
