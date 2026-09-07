import clsx from 'clsx'
import type { ReactNode } from 'react'

interface Props { checked: boolean; onChange: (v: boolean) => void; label?: ReactNode; hint?: ReactNode; disabled?: boolean; className?: string; size?: 'sm' | 'md'; tone?: 'cyan' | 'acid' | 'amber' }

const TONE = { cyan: 'bg-cyan shadow-glow', acid: 'bg-acid shadow-[0_0_14px_rgba(182,255,46,.5)]', amber: 'bg-amber shadow-[0_0_14px_rgba(255,176,32,.5)]' }

export function Toggle({ checked, onChange, label, hint, disabled, className, size = 'md', tone = 'cyan' }: Props) {
  const w = size === 'md' ? 'w-10 h-[22px]' : 'w-8 h-[18px]'
  const knob = size === 'md' ? 'w-4 h-4' : 'w-3 h-3'
  const shift = size === 'md' ? 'translate-x-[20px]' : 'translate-x-[15px]'
  return (
    <label className={clsx('flex items-center gap-3 select-none', disabled ? 'opacity-40 cursor-not-allowed' : 'cursor-pointer', className)}>
      <button
        type="button" role="switch" aria-checked={checked} disabled={disabled} onClick={() => onChange(!checked)}
        className={clsx('relative rounded-full border transition-colors shrink-0 focus-ring', w, checked ? clsx('border-transparent', TONE[tone]) : 'bg-bg/70 border-line2')}
      >
        <span className={clsx('absolute top-[2px] left-[2px] rounded-full transition-transform', knob, checked ? clsx('bg-bg', shift) : 'bg-muted')} />
      </button>
      {(label || hint) && (
        <span className="flex flex-col min-w-0">
          {label && <span className="text-[14px] font-semibold leading-tight">{label}</span>}
          {hint && <span className="text-[12px] text-muted leading-tight">{hint}</span>}
        </span>
      )}
    </label>
  )
}
