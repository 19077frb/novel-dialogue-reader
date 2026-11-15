import { useId, useState } from 'react'
import type { ReactNode } from 'react'
import { ChevronDown } from 'lucide-react'
import { motion, useReducedMotion } from 'motion/react'
import { Button } from './ui/button'
import '../styles/components.css'

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
  const reducedMotion = useReducedMotion()
  const toggle = () => {
    setLocalOpen(!expanded)
    onOpenChange?.(!expanded)
  }
  return <div className="ndr-collapsible-block">
    <div className="ndr-collapsible-heading">
      <h3 id={`${id}-heading`}>{title}</h3>
      <Button type="button" size="icon" className="ndr-collapse-toggle" aria-expanded={expanded} aria-controls={`${id}-content`}
        aria-label={`${expanded ? '收起' : '展开'}${title}`} title={`${expanded ? '收起' : '展开'}${title}`} onClick={toggle}>
        <motion.span className="ndr-icon" aria-hidden="true" animate={{ rotate: expanded ? 180 : 0 }}
          transition={{ duration: reducedMotion ? 0 : 0.16 }}><ChevronDown size={18} aria-hidden="true" /></motion.span>
      </Button>
    </div>
    {summary != null && <div className="ndr-collapsible-summary">{summary}</div>}
    <div id={`${id}-content`} aria-labelledby={`${id}-heading`} hidden={!expanded}>
      {children}
    </div>
  </div>
}
