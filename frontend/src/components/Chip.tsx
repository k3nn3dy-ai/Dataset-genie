import clsx from 'clsx'
import type { ReactNode } from 'react'
import { Icon } from './Icon'

export type ChipTone = 'default' | 'green' | 'steel' | 'ok' | 'amber' | 'red' | 'dim'

const TONE: Record<ChipTone, string> = {
  default: 'bg-surface2 text-text border-line2',
  green: 'bg-green/15 text-green border-green/40',
  steel: 'bg-steel/15 text-steel border-steel/40',
  ok: 'bg-ok/15 text-ok border-ok/40',
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
        TONE[tone], onClick && 'cursor-pointer hover:brightness-125 focus-ring', active && 'ring-1 ring-green/70', className,
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
    case 'accepted': case 'done': case 'ok': case 'kept': return 'ok'
    case 'refusal': case 'flagged': case 'pii': case 'warn': return 'amber'
    case 'filtered': case 'error': case 'failed': case 'low_score': case 'budget_stop': return 'red'
    case 'edited': case 'running': case 'calling': return 'green'
    case 'near_dup': case 'exact_dup': case 'tie': case 'adversarial': return 'steel'
    case 'easy': return 'ok'
    case 'medium': return 'green'
    case 'hard': return 'steel'
    default: return 'default'
  }
}
