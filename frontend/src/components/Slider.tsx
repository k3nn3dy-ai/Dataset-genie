import clsx from 'clsx'
import type { CSSProperties } from 'react'

interface Props {
  label?: string
  value: number
  onChange: (v: number) => void
  min?: number
  max?: number
  step?: number
  format?: (v: number) => string
  className?: string
  disabled?: boolean
  tone?: 'cyan' | 'magenta' | 'amber'
}

export function Slider({ label, value, onChange, min = 0, max = 100, step = 1, format, className, disabled, tone = 'cyan' }: Props) {
  const fill = ((value - min) / (max - min)) * 100
  const colour = tone === 'cyan' ? '#00f0ff' : tone === 'magenta' ? '#ff2bd6' : '#ffb020'
  const style = { '--fill': `${fill}%`, background: `linear-gradient(90deg, ${colour} ${fill}%, #28404c ${fill}%)` } as CSSProperties
  return (
    <div className={clsx('flex flex-col gap-1.5', disabled && 'opacity-40', className)}>
      {(label || format) && (
        <div className="flex items-center justify-between gap-2">
          {label && <span className="label">{label}</span>}
          <span className="font-mono text-[12px] text-cyan tabular-nums">{format ? format(value) : value}</span>
        </div>
      )}
      <input
        type="range" className="slider w-full" min={min} max={max} step={step} value={value} disabled={disabled}
        onChange={(e) => onChange(Number(e.target.value))} style={style} aria-label={label}
      />
    </div>
  )
}
