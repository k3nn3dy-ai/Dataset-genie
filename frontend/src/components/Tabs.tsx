import clsx from 'clsx'
import type { ReactNode } from 'react'

export interface Tab<K extends string = string> { key: K; label: ReactNode; count?: number }

export function Tabs<K extends string>({ tabs, value, onChange, className, size = 'md' }: { tabs: Tab<K>[]; value: K; onChange: (k: K) => void; className?: string; size?: 'sm' | 'md' }) {
  return (
    <div role="tablist" className={clsx('flex items-end gap-1 border-b border-line', className)}>
      {tabs.map((t) => {
        const active = t.key === value
        return (
          <button
            key={t.key} role="tab" type="button" aria-selected={active} onClick={() => onChange(t.key)}
            className={clsx(
              'relative -mb-px px-3 font-ui font-semibold tracking-[-0.01em] transition-colors focus-ring rounded-t-btn',
              size === 'md' ? 'h-9 text-[13px]' : 'h-8 text-[12px]',
              active ? 'text-green' : 'text-muted hover:text-text',
            )}
          >
            <span className="flex items-center gap-1.5">
              {t.label}
              {t.count !== undefined && <span className={clsx('font-ui text-[11px] px-1.5 rounded-chip', active ? 'bg-green/10 text-green' : 'bg-surface2 text-dim')}>{t.count}</span>}
            </span>
            <span className={clsx('absolute left-0 right-0 bottom-0 h-[2px] rounded-full transition-all', active ? 'bg-green' : 'bg-transparent')} />
          </button>
        )
      })}
    </div>
  )
}
