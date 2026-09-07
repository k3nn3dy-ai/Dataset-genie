import clsx from 'clsx'
import { useState } from 'react'

/** Judge score histogram: 10 bins over 0–5, cyan bars, amber threshold marker. */
export function Histogram({ bins, threshold, height = 140, className, mean }: { bins: number[]; threshold?: number; height?: number; className?: string; mean?: number }) {
  const [hover, setHover] = useState<number | null>(null)
  const max = Math.max(1, ...bins)
  const n = bins.length
  const W = 400
  const H = height
  const padB = 22
  const padT = 10
  const bw = W / n
  const x = (v: number) => (v / 5) * W
  return (
    <div className={clsx('relative w-full', className)}>
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full h-auto block" role="img" aria-label="Score histogram">
        {[0.25, 0.5, 0.75, 1].map((g) => (
          <line key={g} x1={0} x2={W} y1={padT + (H - padT - padB) * (1 - g)} y2={padT + (H - padT - padB) * (1 - g)} stroke="#1c2a33" strokeWidth={1} />
        ))}
        {bins.map((v, i) => {
          const h = ((H - padT - padB) * v) / max
          const below = threshold !== undefined && (i + 1) * (5 / n) <= threshold
          return (
            <g key={i} onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)}>
              <rect x={i * bw} y={padT} width={bw} height={H - padT - padB} fill="transparent" />
              <rect
                x={i * bw + 3} y={H - padB - h} width={bw - 6} height={h} rx={2}
                fill={below ? 'rgba(0,240,255,.28)' : '#00f0ff'} opacity={hover === null || hover === i ? 1 : 0.55}
                style={{ filter: hover === i ? 'drop-shadow(0 0 6px rgba(0,240,255,.7))' : undefined }}
              />
            </g>
          )
        })}
        {threshold !== undefined && (
          <g>
            <line x1={x(threshold)} x2={x(threshold)} y1={padT - 4} y2={H - padB + 4} stroke="#ffb020" strokeWidth={1.5} strokeDasharray="4 3" />
            <text x={x(threshold) + 4} y={padT + 8} fill="#ffb020" fontFamily="Share Tech Mono" fontSize={10}>THR {threshold.toFixed(1)}</text>
          </g>
        )}
        {mean !== undefined && (
          <g>
            <line x1={x(mean)} x2={x(mean)} y1={padT} y2={H - padB} stroke="#ff2bd6" strokeWidth={1} />
            <text x={x(mean) + 4} y={padT + 22} fill="#ff2bd6" fontFamily="Share Tech Mono" fontSize={10} textAnchor={mean > 4 ? 'end' : 'start'} dx={mean > 4 ? -8 : 0}>MEAN {mean.toFixed(2)}</text>
          </g>
        )}
        {[0, 1, 2, 3, 4, 5].map((t) => (
          <text key={t} x={x(t)} y={H - 6} fill="#7e95a1" fontFamily="Share Tech Mono" fontSize={10} textAnchor={t === 0 ? 'start' : t === 5 ? 'end' : 'middle'}>{t}</text>
        ))}
      </svg>
      {hover !== null && (
        <div className="absolute top-0 right-0 panel px-2 py-1 font-mono text-[10.5px] text-text pointer-events-none">
          {(hover * (5 / n)).toFixed(1)}–{((hover + 1) * (5 / n)).toFixed(1)} · <span className="text-cyan">{bins[hover]}</span> rows
        </div>
      )}
    </div>
  )
}
