import { useId, useState } from 'react'
import type { ReactNode } from 'react'

/** Keep content mounted: collapsing must not reset edits, selections or task polling. */
export function CollapsibleBlock({ title, summary, children, defaultOpen = true, open, onOpenChange }: {
  title: string
  summary?: ReactNode
  children: ReactNode
  defaultOpen?: boolean
  open?: boolean
  onOpenChange?: (open: boolean) => void
}) {
  const [localOpen, setLocalOpen] = useState(defaultOpen)
  const expanded = open ?? localOpen
  const id = useId()
  const toggle = () => {
    setLocalOpen(!expanded)
    onOpenChange?.(!expanded)
  }
  return <div className="ndr-collapsible-block">
    <div className="ndr-collapsible-heading">
      <h3 id={`${id}-heading`}>{title}</h3>
      <button type="button" aria-expanded={expanded} aria-controls={`${id}-content`}
        aria-label={`${expanded ? '收起' : '展开'}${title}`} onClick={toggle}>
        {expanded ? '收起' : '展开'}
      </button>
    </div>
    {summary != null && <div className="ndr-collapsible-summary">{summary}</div>}
    <div id={`${id}-content`} aria-labelledby={`${id}-heading`} hidden={!expanded}>
      {children}
    </div>
  </div>
}
