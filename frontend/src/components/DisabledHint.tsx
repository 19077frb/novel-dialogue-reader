import { Info } from 'lucide-react'
import '../styles/components.css'

/** Explain the real blocker without changing whether an operation is allowed. */
export function DisabledHint({ reason }: { reason?: string | null | false }) {
  return reason ? <p className="hint ndr-hint-inline" role="status"><Info size={15} aria-hidden="true" /><span>{reason}</span></p> : null
}

export function disabledHint(reason?: string | null | false) {
  return { title: reason || undefined, 'aria-description': reason || undefined }
}
