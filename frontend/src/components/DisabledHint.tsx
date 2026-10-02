/** Explain the real blocker without changing whether an operation is allowed. */
export function DisabledHint({ reason }: { reason?: string | null | false }) {
  return reason ? <p className="hint" role="status">{reason}</p> : null
}

export function disabledHint(reason?: string | null | false) {
  return { title: reason || undefined, 'aria-description': reason || undefined }
}
