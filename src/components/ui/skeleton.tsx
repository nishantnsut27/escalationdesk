import type { HTMLAttributes } from 'react'
import { cn } from '../../lib/utils'

/** Decorative placeholder; its loading region owns the accessible status. */
export function Skeleton({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return <div {...props} aria-hidden="true" className={cn('skeleton', className)} />
}
