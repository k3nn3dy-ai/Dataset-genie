import clsx from 'clsx'
import type { ReactNode } from 'react'
import { Icon, type IconName } from './Icon'
import { Button } from './Button'

interface EmptyProps { icon?: IconName; title: string; body?: ReactNode; action?: { label: string; onClick: () => void; icon?: IconName }; className?: string; kana?: string }

export function EmptyState({ icon = 'sparkle', title, body, action, className, kana }: EmptyProps) {
  return (
    <div className={clsx('panel flex flex-col items-center justify-center text-center gap-3 px-8 py-14 border-dashed', className)}>
      <div className="w-12 h-12 rounded-card border border-cyan/40 flex items-center justify-center text-cyan shadow-[0_0_20px_rgba(0,240,255,.15)]">
        <Icon name={icon} size={22} />
      </div>
      <div>
        <div className="font-display font-bold uppercase text-[15px] tracking-[.06em]">{title}</div>
        {kana && <div className="font-mono text-[10px] text-dim mt-0.5">{kana}</div>}
      </div>
      {body && <p className="text-muted text-[13.5px] max-w-[380px] leading-relaxed">{body}</p>}
      {action && <Button variant="primary" icon={action.icon ?? 'play'} onClick={action.onClick} className="mt-1">{action.label}</Button>}
    </div>
  )
}

export function ErrorState({ title = 'Something failed', error, onRetry, className }: { title?: string; error?: unknown; onRetry?: () => void; className?: string }) {
  const msg = error instanceof Error ? error.message : typeof error === 'string' ? error : 'Unknown error'
  return (
    <div className={clsx('panel border-red/40 flex flex-col items-center text-center gap-3 px-8 py-12', className)} role="alert">
      <div className="w-12 h-12 rounded-card border border-red/50 flex items-center justify-center text-red">
        <Icon name="warning" size={22} />
      </div>
      <div className="font-display font-bold uppercase text-[15px] tracking-[.06em] text-red">{title}</div>
      <pre className="font-mono text-[11.5px] text-muted whitespace-pre-wrap max-w-[520px]">{msg}</pre>
      {onRetry && <Button variant="outline" icon="refresh" onClick={onRetry}>Retry</Button>}
    </div>
  )
}

export function Spinner({ className, label = 'Loading' }: { className?: string; label?: string }) {
  return (
    <div className={clsx('flex items-center gap-2 text-muted font-mono text-[11px] uppercase tracking-[.14em]', className)}>
      <Icon name="refresh" size={13} className="animate-spin text-cyan" />
      {label}
    </div>
  )
}

/** Inline callout / banner. */
export function Banner({ tone = 'amber', icon, children, className }: { tone?: 'amber' | 'red' | 'cyan' | 'acid'; icon?: IconName; children: ReactNode; className?: string }) {
  const c = { amber: 'border-amber/50 bg-amber/10 text-amber', red: 'border-red/50 bg-red/10 text-red', cyan: 'border-cyan/50 bg-cyan/10 text-cyan', acid: 'border-acid/50 bg-acid/10 text-acid' }[tone]
  return (
    <div className={clsx('flex items-start gap-2.5 rounded-btn border px-3 py-2 text-[13px] leading-snug', c, className)} role="status">
      <Icon name={icon ?? (tone === 'amber' || tone === 'red' ? 'warning' : 'sparkle')} size={14} className="mt-[2px]" />
      <div className="text-text/90">{children}</div>
    </div>
  )
}
