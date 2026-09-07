import clsx from 'clsx'
import { usd } from '../lib/format'

/** Spend / cap bar. acid <70%, amber <90%, red ≥90%. */
export function BudgetBar({ spend, cap, stopAt = 90, className, compact }: { spend: number; cap: number; stopAt?: number; className?: string; compact?: boolean }) {
  const p = cap > 0 ? Math.min(100, (spend / cap) * 100) : 0
  const tone = p >= 90 ? 'red' : p >= 70 ? 'amber' : 'acid'
  const bar = { acid: 'bg-acid shadow-[0_0_10px_rgba(182,255,46,.55)]', amber: 'bg-amber shadow-[0_0_10px_rgba(255,176,32,.55)]', red: 'bg-red shadow-[0_0_10px_rgba(255,59,92,.6)]' }[tone]
  const txt = { acid: 'text-acid', amber: 'text-amber', red: 'text-red' }[tone]
  return (
    <div className={clsx('flex flex-col gap-1.5', className)} title={`Auto-stop at ${stopAt}% of cap`}>
      <div className="flex items-baseline justify-between">
        <span className="label">budget</span>
        <span className="font-mono text-[11px] tabular-nums"><span className={txt}>{usd(spend)}</span><span className="text-dim"> / {usd(cap)}</span></span>
      </div>
      <div className="relative h-1.5 rounded-full bg-bg/80 border border-line overflow-hidden">
        <div className={clsx('h-full rounded-full transition-all duration-500', bar)} style={{ width: `${p}%` }} />
        <div className="absolute top-0 bottom-0 w-px bg-amber/70" style={{ left: `${stopAt}%` }} />
      </div>
      {!compact && <div className="flex justify-between font-mono text-[9.5px] text-dim"><span>{p.toFixed(0)}% used</span><span>stop @ {stopAt}%</span></div>}
    </div>
  )
}
