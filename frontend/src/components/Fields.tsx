import clsx from 'clsx'
import type { InputHTMLAttributes, ReactNode, SelectHTMLAttributes, TextareaHTMLAttributes } from 'react'
import { Icon } from './Icon'

export function Label({ children, hint, className, htmlFor }: { children: ReactNode; hint?: ReactNode; className?: string; htmlFor?: string }) {
  return (
    <label htmlFor={htmlFor} className={clsx('flex items-baseline justify-between gap-2', className)}>
      <span className="label">{children}</span>
      {hint && <span className="font-mono text-[10px] text-dim">{hint}</span>}
    </label>
  )
}

export function Field({ label, hint, children, className }: { label: ReactNode; hint?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <div className={clsx('flex flex-col gap-1.5', className)}>
      <Label hint={hint}>{label}</Label>
      {children}
    </div>
  )
}

interface InputProps extends InputHTMLAttributes<HTMLInputElement> { mono?: boolean; invalid?: boolean }
export function Input({ className, mono = true, invalid, ...rest }: InputProps) {
  return <input className={clsx('field w-full h-9', !mono && '!font-ui !text-[14px]', invalid && '!border-red/70', className)} {...rest} />
}

export function Textarea({ className, mono = false, invalid, ...rest }: TextareaHTMLAttributes<HTMLTextAreaElement> & { mono?: boolean; invalid?: boolean }) {
  return <textarea className={clsx('field w-full min-h-[84px] resize-y leading-relaxed', !mono && '!font-ui !text-[14px]', invalid && '!border-red/70', className)} {...rest} />
}

export function Select({ className, children, ...rest }: SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <div className={clsx('relative', className)}>
      <select className="field w-full h-9 appearance-none pr-8 cursor-pointer" {...rest}>{children}</select>
      <Icon name="chevron" size={12} className="absolute right-2.5 top-1/2 -translate-y-1/2 rotate-90 text-muted pointer-events-none" />
    </div>
  )
}

/** Compact numeric input with mono digits and optional unit suffix. */
export function NumberInput({ value, onChange, min, max, step = 1, unit, className, disabled }: { value: number; onChange: (v: number) => void; min?: number; max?: number; step?: number; unit?: string; className?: string; disabled?: boolean }) {
  return (
    <div className={clsx('relative', className)}>
      <input
        type="number" value={value} min={min} max={max} step={step} disabled={disabled}
        onChange={(e) => onChange(Number(e.target.value))}
        className={clsx('field w-full h-9 tabular-nums', unit && 'pr-10')}
      />
      {unit && <span className="absolute right-2.5 top-1/2 -translate-y-1/2 font-mono text-[10px] text-dim">{unit}</span>}
    </div>
  )
}

/** Segmented control (e.g. Private / Public). */
export function Segmented<K extends string>({ options, value, onChange, className }: { options: { key: K; label: string }[]; value: K; onChange: (k: K) => void; className?: string }) {
  return (
    <div className={clsx('inline-flex rounded-btn border border-line2 bg-bg/60 p-0.5', className)} role="radiogroup">
      {options.map((o) => (
        <button
          key={o.key} type="button" role="radio" aria-checked={o.key === value} onClick={() => onChange(o.key)}
          className={clsx('h-7 px-3 rounded-[6px] font-display font-bold uppercase text-[11px] tracking-[.08em] transition-colors focus-ring', o.key === value ? 'bg-cyan text-bg' : 'text-muted hover:text-text')}
        >
          {o.label}
        </button>
      ))}
    </div>
  )
}

/** Radio list with description. */
export function RadioGroup<K extends string>({ options, value, onChange, className }: { options: { key: K; label: string; hint?: string }[]; value: K; onChange: (k: K) => void; className?: string }) {
  return (
    <div className={clsx('flex flex-col gap-1.5', className)} role="radiogroup">
      {options.map((o) => {
        const on = o.key === value
        return (
          <button
            key={o.key} type="button" role="radio" aria-checked={on} onClick={() => onChange(o.key)}
            className={clsx('flex items-start gap-3 text-left px-3 py-2 rounded-btn border transition-colors focus-ring', on ? 'border-cyan/50 bg-cyan/10' : 'border-line hover:border-line2')}
          >
            <span className={clsx('mt-[3px] w-3.5 h-3.5 rounded-full border shrink-0 flex items-center justify-center', on ? 'border-cyan' : 'border-line2')}>
              {on && <span className="w-1.5 h-1.5 rounded-full bg-cyan shadow-glow" />}
            </span>
            <span className="flex flex-col">
              <span className="text-[14px] font-semibold leading-tight">{o.label}</span>
              {o.hint && <span className="text-[12px] text-muted leading-tight mt-0.5">{o.hint}</span>}
            </span>
          </button>
        )
      })}
    </div>
  )
}
