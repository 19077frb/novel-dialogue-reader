import type { HTMLAttributes } from 'react'
import { cn } from '../../lib/utils'

/** Nova's compact outline badge, backed by the shared ndr design tokens. */
export function Badge({ className, ...props }: HTMLAttributes<HTMLSpanElement>) {
  return <span data-slot="badge" className={cn('ndr-badge', className)} {...props} />
}
