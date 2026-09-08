import clsx from 'clsx'
import type { ReactNode } from 'react'

export type Tone = 'default' | 'orange' | 'steel' | 'ok' | 'amber' | 'red'

const VALUE_TONE: Record<Tone, string> = {
  default: 'text-text', orange: 'text-orange', steel: 'text-steel', ok: 'text-ok', amber: 'text-amber', red: 'text-red',
}

interface Props {
  label: string
  value: ReactNode
  unit?: string
  delta?: string
  deltaTone?: Tone
  tone?: Tone
  hint?: string
  className?: string
  size?: 'sm' | 'md'
}

export function StatTile({ label, value, unit, delta, deltaTone = 'default', tone = 'default', hint, className, size = 'md' }: Props) {
  return (
    <div className={clsx('panel px-3.5 py-3 flex flex-col gap-1 min-w-0', className)}>
      <div className="label truncate">{label}</div>
      <div className="flex items-baseline gap-1.5 min-w-0">
        <span className={clsx('font-mono tabular-nums leading-none truncate', size === 'md' ? 'text-[26px]' : 'text-[19px]', VALUE_TONE[tone])}>{value}</span>
        {unit && <span className="font-mono text-[11px] text-muted">{unit}</span>}
        {delta && <span className={clsx('font-mono text-[11px] ml-auto', VALUE_TONE[deltaTone])}>{delta}</span>}
      </div>
      {hint && <div className="font-mono text-[10.5px] text-dim truncate">{hint}</div>}
    </div>
  )
}
