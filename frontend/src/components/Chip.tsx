import clsx from 'clsx'
import type { ReactNode } from 'react'
import { Icon } from './Icon'

export type ChipTone = 'default' | 'cyan' | 'magenta' | 'acid' | 'amber' | 'red' | 'dim'

const TONE: Record<ChipTone, string> = {
  default: 'bg-surface2 text-text border-line2',
  cyan: 'bg-cyan/15 text-cyan border-cyan/40',
  magenta: 'bg-magenta/15 text-magenta border-magenta/40',
  acid: 'bg-acid/15 text-acid border-acid/40',
  amber: 'bg-amber/15 text-amber border-amber/40',
  red: 'bg-red/15 text-red border-red/40',
  dim: 'bg-transparent text-dim border-line',
}

interface Props { children: ReactNode; tone?: ChipTone; className?: string; onClick?: () => void; onRemove?: () => void; active?: boolean; title?: string }

export function Chip({ children, tone = 'default', className, onClick, onRemove, active, title }: Props) {
  const Tag = onClick ? 'button' : 'span'
  return (
    <Tag
      type={onClick ? 'button' : undefined} onClick={onClick} title={title}
      className={clsx(
        'inline-flex items-center gap-1 h-[20px] px-1.5 rounded-chip border font-mono text-[10.5px] tracking-[.06em] uppercase whitespace-nowrap leading-none',
        TONE[tone], onClick && 'cursor-pointer hover:brightness-125 focus-ring', active && 'ring-1 ring-cyan/70', className,
      )}
    >
      {children}
      {onRemove && (
        <span role="button" aria-label="remove" onClick={(e) => { e.stopPropagation(); onRemove() }} className="ml-0.5 -mr-0.5 opacity-70 hover:opacity-100 cursor-pointer">
          <Icon name="x" size={9} />
        </span>
      )}
    </Tag>
  )
}

/** Map a flag / status string to a chip tone. */
export function toneFor(v: string): ChipTone {
  switch (v) {
    case 'accepted': case 'done': case 'ok': case 'kept': return 'acid'
    case 'refusal': case 'flagged': case 'pii': case 'warn': return 'amber'
    case 'filtered': case 'error': case 'failed': case 'low_score': case 'budget_stop': return 'red'
    case 'edited': case 'running': case 'calling': return 'cyan'
    case 'near_dup': case 'exact_dup': case 'tie': case 'adversarial': return 'magenta'
    case 'easy': return 'acid'
    case 'medium': return 'cyan'
    case 'hard': return 'magenta'
    default: return 'default'
  }
}
