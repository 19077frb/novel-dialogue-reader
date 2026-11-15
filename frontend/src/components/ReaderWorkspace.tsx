import type { ReactNode } from 'react'
import { useId } from 'react'
import { GripVertical, PanelLeftClose, PanelLeftOpen } from 'lucide-react'
import { Panel, PanelGroup, PanelResizeHandle } from 'react-resizable-panels'

import { useReaderLayoutStore } from '../stores/readerLayoutStore'
import '../styles/reader-layout.css'

/** Keep both panels mounted across resizing, hiding, and viewport changes. */
export function ReaderWorkspace({ navigation, children, chapterTitle }: {
  navigation: ReactNode
  children: ReactNode
  chapterTitle?: string | null
}) {
  const sidebarOpen = useReaderLayoutStore(state => state.sidebarOpen)
  const sidebarSize = useReaderLayoutStore(state => state.sidebarSize)
  const toggleSidebar = useReaderLayoutStore(state => state.toggleSidebar)
  const setSidebarSize = useReaderLayoutStore(state => state.setSidebarSize)
  const navigationId = useId()
  return <div className="ndr-reader-workspace" data-navigation-open={sidebarOpen}>
    <div className="ndr-reader-workspace-toolbar">
      <button type="button" onClick={toggleSidebar} aria-expanded={sidebarOpen}
        aria-controls={navigationId} title={sidebarOpen ? '隐藏目录与书签，保留当前阅读位置。' : '显示目录与书签。'}>
        {sidebarOpen ? <PanelLeftClose size={16} aria-hidden="true" /> : <PanelLeftOpen size={16} aria-hidden="true" />}
        {sidebarOpen ? '隐藏阅读导航' : '显示阅读导航'}
      </button>
      <span className="hint ndr-reader-current-chapter">{chapterTitle ?? '阅读正文'}</span>
      <span className="hint ndr-reader-resize-hint">拖动分隔线调整宽度</span>
    </div>
    <PanelGroup direction="horizontal" className="ndr-reader-panels" onLayout={sizes => {
      if (sidebarOpen && sizes[0] !== undefined) setSidebarSize(sizes[0])
    }}>
      <Panel id="navigation" order={1} defaultSize={sidebarSize} minSize={18} maxSize={40}
        className="ndr-reader-navigation-panel">
        <div id={navigationId} hidden={!sidebarOpen}>{navigation}</div>
      </Panel>
      <PanelResizeHandle className="ndr-reader-resize-handle" disabled={!sidebarOpen} aria-label="调整阅读导航宽度">
        <GripVertical size={14} aria-hidden="true" />
      </PanelResizeHandle>
      <Panel id="document" order={2} defaultSize={100 - sidebarSize} minSize={60} className="ndr-reader-document-panel">{children}</Panel>
    </PanelGroup>
  </div>
}
