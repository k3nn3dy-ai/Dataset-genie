import clsx from 'clsx'
import type { ReactNode } from 'react'
import { Kicker } from '../components/Kicker'
import { GhostNumeral } from '../components/GhostNumeral'

interface Props {
  kicker: string
  title: string
  numeral?: string
  actions?: ReactNode
  subtitle?: ReactNode
  testId?: string
  className?: string
}

/** Screen header: quiet kicker, title, faint numeral, action slot. */
export function Header({ kicker, title, numeral, actions, subtitle, testId = 'screen-title', className }: Props) {
  return (
    <header className={clsx('relative flex items-end justify-between gap-6 min-h-[72px] pb-5 mb-6 border-b border-line', className)}>
      {numeral && <GhostNumeral value={numeral} size={88} className="absolute -top-2 right-0 z-0" />}
      <div className="relative z-10 flex flex-col gap-1 min-w-0">
        <Kicker text={kicker} />
        <h1 data-testid={testId} className="font-display font-semibold text-[28px] leading-[1.1] tracking-[-0.022em] text-text">{title}</h1>
        {subtitle && <div className="text-muted text-[13.5px] mt-1 max-w-[640px] leading-relaxed">{subtitle}</div>}
      </div>
      {actions && <div className="relative z-10 flex items-center gap-2 shrink-0 pb-1">{actions}</div>}
    </header>
  )
}
