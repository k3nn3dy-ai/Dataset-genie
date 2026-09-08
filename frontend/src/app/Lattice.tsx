import { useEffect, useRef } from 'react'
import { mulberry32 } from '../lib/rng'

// Neural lattice backdrop for the main column: seeded nodes, 3-nearest edges, halos, ringed nodes, slow pulse,
// drifting JSONL/training fragments and dashed bus lines. rAF at ~30fps; pauses when hidden; honours reduced motion.
const FRAGMENTS = [
  '{"messages":[…', '"role":"assistant"', 'loss 0.8123', 'epoch 2/3', 'train_on_responses_only', '"role":"user"', 'lr 2e-4', 'grad_norm 0.61',
  '{"prompt":[…],"chosen":[…]', 'step 1840/2400', '"tool_calls":[{', 'eval_loss 0.7719', 'metadata.id', '<think>…</think>', 'ANSWER: 42', 'usage.cost 0.0041',
  '"content":"Diagnosis:', 'cosine 0.93 ≥ 0.92', 'refusal=false', 'stratify_by=leaf', 'seed 42', 'max_seq_len 4096', 'lora_r 16', 'packing=true',
]

interface Node { x: number; y: number; r: number; c: 'orange' | 'steel'; ring: boolean; seed: number }
interface Column { x: number; y: number; speed: number; items: string[]; alpha: number }

export function Lattice({ seed = 4242, className }: { seed?: number; className?: string }) {
  const ref = useRef<HTMLCanvasElement>(null)
  useEffect(() => {
    const canvas = ref.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return
    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    let nodes: Node[] = []
    let edges: [number, number][] = []
    let cols: Column[] = []
    let buses: { y: number; x0: number; x1: number; phase: number }[] = []
    let W = 0
    let H = 0
    let dpr = 1

    const build = () => {
      const rect = canvas.parentElement?.getBoundingClientRect()
      W = Math.max(300, Math.floor(rect?.width ?? window.innerWidth))
      H = Math.max(300, Math.floor(rect?.height ?? window.innerHeight))
      dpr = Math.min(2, window.devicePixelRatio || 1)
      canvas.width = W * dpr
      canvas.height = H * dpr
      canvas.style.width = `${W}px`
      canvas.style.height = `${H}px`
      const rnd = mulberry32(seed)
      const n = 70 + Math.floor(rnd() * 41) // 70–110
      nodes = Array.from({ length: n }, () => ({ x: rnd() * W, y: rnd() * H, r: 1.2 + rnd() * 1.8, c: rnd() < 0.72 ? 'orange' : 'steel', ring: false, seed: rnd() * Math.PI * 2 }))
      const ringCount = 8
      for (let i = 0; i < ringCount; i++) nodes[Math.floor(rnd() * n)].ring = true
      edges = []
      const seen = new Set<string>()
      nodes.forEach((a, i) => {
        const near = nodes.map((b, j) => ({ j, d: (a.x - b.x) ** 2 + (a.y - b.y) ** 2 })).filter((o) => o.j !== i).sort((p, q) => p.d - q.d).slice(0, 3)
        for (const o of near) { const k = i < o.j ? `${i}-${o.j}` : `${o.j}-${i}`; if (!seen.has(k)) { seen.add(k); edges.push([i, o.j]) } }
      })
      const colCount = 6 + Math.floor(rnd() * 5) // 6–10
      cols = Array.from({ length: colCount }, (_, i) => ({ x: (W / colCount) * i + rnd() * (W / colCount) * 0.6 + 20, y: rnd() * H, speed: 6 + rnd() * 10, alpha: 0.12 + rnd() * 0.16, items: Array.from({ length: 6 + Math.floor(rnd() * 6) }, () => FRAGMENTS[Math.floor(rnd() * FRAGMENTS.length)]) }))
      buses = Array.from({ length: 4 }, () => ({ y: 60 + rnd() * (H - 120), x0: rnd() * W * 0.3, x1: W * 0.6 + rnd() * W * 0.4, phase: rnd() * 40 }))
    }

    let raf = 0
    let last = 0
    let t0 = performance.now()
    const draw = (now: number) => {
      raf = requestAnimationFrame(draw)
      if (document.hidden) return
      if (now - last < 1000 / 30) return
      last = now
      const t = reduced ? 0 : (now - t0) / 1000
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
      ctx.clearRect(0, 0, W, H)

      // bus lines
      ctx.lineWidth = 1
      for (const b of buses) {
        ctx.setLineDash([6, 10])
        ctx.lineDashOffset = -(t * 18 + b.phase)
        ctx.strokeStyle = 'rgba(255,106,26,.14)'
        ctx.beginPath(); ctx.moveTo(b.x0, b.y); ctx.lineTo(b.x1, b.y); ctx.stroke()
      }
      ctx.setLineDash([])

      // edges
      ctx.strokeStyle = 'rgba(255,106,26,.13)'
      ctx.beginPath()
      for (const [i, j] of edges) { ctx.moveTo(nodes[i].x, nodes[i].y); ctx.lineTo(nodes[j].x, nodes[j].y) }
      ctx.stroke()

      // fragments
      ctx.font = '11px "Share Tech Mono", monospace'
      ctx.textBaseline = 'top'
      for (const c of cols) {
        if (!reduced) c.y += c.speed / 30
        const span = c.items.length * 22
        if (c.y > H) c.y = -span
        c.items.forEach((s, k) => {
          const y = c.y + k * 22
          if (y < -20 || y > H) return
          ctx.fillStyle = k % 5 === 0 ? `rgba(179,179,179,${c.alpha})` : `rgba(255,154,92,${c.alpha})`
          ctx.fillText(s, c.x, y)
        })
      }

      // nodes with halos
      for (const nd of nodes) {
        const pulse = 0.65 + 0.35 * Math.sin(t * 0.6 + nd.seed)
        const col = nd.c === 'orange' ? '255,106,26' : '179,179,179'
        ctx.shadowBlur = 14 * pulse
        ctx.shadowColor = `rgba(${col},.8)`
        ctx.fillStyle = `rgba(${col},${0.55 + 0.45 * pulse})`
        ctx.beginPath(); ctx.arc(nd.x, nd.y, nd.r * (0.85 + 0.3 * pulse), 0, Math.PI * 2); ctx.fill()
        if (nd.ring) {
          ctx.shadowBlur = 0
          ctx.strokeStyle = `rgba(${col},${0.35 + 0.35 * pulse})`
          ctx.lineWidth = 1
          ctx.beginPath(); ctx.arc(nd.x, nd.y, 7 + 2 * pulse, 0, Math.PI * 2); ctx.stroke()
        }
      }
      ctx.shadowBlur = 0
      if (reduced) cancelAnimationFrame(raf)
    }

    build()
    t0 = performance.now()
    raf = requestAnimationFrame(draw)
    const ro = new ResizeObserver(() => { build(); if (reduced) draw(performance.now()) })
    if (canvas.parentElement) ro.observe(canvas.parentElement)
    const onVis = () => { if (!document.hidden) { last = 0 } }
    document.addEventListener('visibilitychange', onVis)
    return () => { cancelAnimationFrame(raf); ro.disconnect(); document.removeEventListener('visibilitychange', onVis) }
  }, [seed])

  return (
    <div aria-hidden className={className} style={{ maskImage: 'linear-gradient(to bottom, transparent 0, rgba(0,0,0,.35) 150px, black 260px)', WebkitMaskImage: 'linear-gradient(to bottom, transparent 0, rgba(0,0,0,.35) 150px, black 260px)' }}>
      <canvas ref={ref} className="block" />
    </div>
  )
}
