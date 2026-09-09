import { useMemo } from 'react'
import { mulberry32 } from '../lib/rng'

// Procedural tower-block skyline with scattered neon window dots. Seeded so it never flickers between renders.
const WINDOW_COLOURS = ['#22e35a', '#b3b3b3', '#ffa040', '#6ff59a']

export function Skyline({ seed = 1337, width = 1920, height = 260 }: { seed?: number; width?: number; height?: number }) {
  const { blocks, windows } = useMemo(() => {
    const rnd = mulberry32(seed)
    const count = 40 + Math.floor(rnd() * 21) // 40–60
    const blocks: { x: number; y: number; w: number; h: number; shade: number }[] = []
    const windows: { x: number; y: number; c: string; o: number }[] = []
    let x = -20
    for (let i = 0; i < count && x < width + 40; i++) {
      const w = 26 + rnd() * 70
      const h = 50 + rnd() * (height - 70) * (0.45 + rnd() * 0.55)
      const y = height - h
      const shade = 0.55 + rnd() * 0.45
      blocks.push({ x, y, w, h, shade })
      // windows: 6% density over a 6×9 grid
      const cols = Math.max(1, Math.floor(w / 9))
      const rows = Math.max(1, Math.floor(h / 11))
      for (let r = 0; r < rows; r++) {
        for (let c = 0; c < cols; c++) {
          if (rnd() < 0.06) windows.push({ x: x + 4 + c * 9, y: y + 6 + r * 11, c: WINDOW_COLOURS[Math.floor(rnd() * WINDOW_COLOURS.length)], o: 0.35 + rnd() * 0.65 })
        }
      }
      x += w - (rnd() * 8) // slight overlap
    }
    return { blocks, windows }
  }, [seed, width, height])

  return (
    <svg viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="xMidYMax slice" className="absolute inset-x-0 bottom-0 w-full h-[260px]" aria-hidden>
      <defs>
        <linearGradient id="sky-fade" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="#0a0a0a" stopOpacity="0" />
          <stop offset="1" stopColor="#0a0a0a" stopOpacity="1" />
        </linearGradient>
        <filter id="win-glow" x="-100%" y="-100%" width="300%" height="300%"><feGaussianBlur stdDeviation="1.2" /></filter>
      </defs>
      {blocks.map((b, i) => (
        <rect key={i} x={b.x} y={b.y} width={b.w} height={b.h} fill={`rgba(14,14,14,${b.shade})`} stroke="rgba(38,38,38,.6)" strokeWidth={1} />
      ))}
      {windows.map((w, i) => (
        <g key={i}>
          <rect x={w.x - 1} y={w.y - 1} width={4} height={4} fill={w.c} opacity={w.o * 0.5} filter="url(#win-glow)" />
          <rect x={w.x} y={w.y} width={2} height={2} fill={w.c} opacity={w.o} />
        </g>
      ))}
      <rect x={0} y={height - 40} width={width} height={40} fill="url(#sky-fade)" opacity={0.7} />
    </svg>
  )
}
