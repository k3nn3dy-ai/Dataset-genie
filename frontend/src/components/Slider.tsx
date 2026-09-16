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
  tone?: 'green' | 'steel' | 'amber'
}

export function Slider({ label, value, onChange, min = 0, max = 100, step = 1, format, className, disabled, tone = 'green' }: Props) {
  const fill = ((value - min) / (max - min)) * 100
  const colour = tone === 'green' ? '#0071E3' : tone === 'steel' ? '#86868B' : '#FF9F0A'
  const style = { '--fill': `${fill}%`, background: `linear-gradient(90deg, ${colour} ${fill}%, #E5E5EA ${fill}%)` } as CSSProperties
  return (
    <div className={clsx('flex flex-col gap-1.5', disabled && 'opacity-40', className)}>
      {(label || format) && (
        <div className="flex items-center justify-between gap-2">
          {label && <span className="label">{label}</span>}
          <span className="font-ui text-[12px] text-green tabular-nums font-medium">{format ? format(value) : value}</span>
        </div>
      )}
      <input
        type="range" className="slider w-full" min={min} max={max} step={step} value={value} disabled={disabled}
        onChange={(e) => onChange(Number(e.target.value))} style={style} aria-label={label}
      />
    </div>
  )
}
