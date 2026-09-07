import clsx from 'clsx'
import type { ReactNode } from 'react'
import { Kicker } from '../components/Kicker'
import { GhostNumeral } from '../components/GhostNumeral'

interface Props {
  kicker: string
  kana?: string
  title: string
  numeral?: string
  actions?: ReactNode
  subtitle?: ReactNode
  testId?: string
  className?: string
}

/** Screen header: kicker + katakana, glitching h1, ghost numeral top-right, action slot. */
export function Header({ kicker, kana, title, numeral, actions, subtitle, testId = 'screen-title', className }: Props) {
  return (
    <header className={clsx('relative flex items-end justify-between gap-6 min-h-[112px] pb-5 mb-6 border-b border-line/80', className)}>
      {numeral && <GhostNumeral value={numeral} className="absolute -top-3 right-0 z-0" />}
      <div className="relative z-10 flex flex-col gap-2 min-w-0">
        <Kicker text={kicker} kana={kana} />
        <h1 data-testid={testId} className="glitch font-display font-bold uppercase text-[40px] leading-[0.95] tracking-[.01em] text-text">{title}</h1>
        {subtitle && <div className="text-muted text-[14px] mt-1 max-w-[640px]">{subtitle}</div>}
      </div>
      {actions && <div className="relative z-10 flex items-center gap-2 shrink-0 pb-1">{actions}</div>}
    </header>
  )
}
