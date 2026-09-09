import type { Persona } from '../../lib/types'
import { Button, IconButton, Input, Slider } from '../../components'

export function PersonaEditor({ personas, onChange }: { personas: Persona[]; onChange: (p: Persona[]) => void }) {
  const set = (i: number, patch: Partial<Persona>) => onChange(personas.map((p, k) => (k === i ? { ...p, ...patch } : p)))
  const total = personas.reduce((a, p) => a + p.weight, 0)
  return (
    <div className="flex flex-col gap-3">
      {personas.map((p, i) => (
        <div key={i} className="rounded-btn border border-line bg-bg/40 p-2.5 flex flex-col gap-2">
          <div className="flex items-center gap-2">
            <Input mono={false} value={p.name} onChange={(e) => set(i, { name: e.target.value })} placeholder="Persona name" className="!h-8 flex-1" />
            <span className="font-mono text-[12px] text-green tabular-nums w-10 text-right">{p.weight}%</span>
            <IconButton icon="trash" label="Remove persona" size="sm" onClick={() => onChange(personas.filter((_, k) => k !== i))} />
          </div>
          <Input mono={false} value={p.style} onChange={(e) => set(i, { style: e.target.value })} placeholder="style: terse, technical, expects precision" className="!h-8 !text-[13px]" />
          <Slider value={p.weight} min={0} max={100} onChange={(v) => set(i, { weight: v })} />
        </div>
      ))}
      <div className="flex items-center justify-between">
        <Button size="sm" variant="outline" icon="plus" onClick={() => onChange([...personas, { name: 'New persona', style: '', weight: Math.max(0, 100 - total) }])}>Add persona</Button>
        {total !== 100 && <span className="font-mono text-[10.5px] text-amber">weights sum to {total}% — normalised at run time</span>}
      </div>
    </div>
  )
}

/** Style-mix sliders constrained to sum 100: moving one redistributes the remainder proportionally. */
export function StyleMix({ mix, onChange }: { mix: Record<string, number>; onChange: (m: Record<string, number>) => void }) {
  const keys = Object.keys(mix)
  const setOne = (k: string, v: number) => {
    const others = keys.filter((x) => x !== k)
    const rest = 100 - v
    const otherTotal = others.reduce((a, x) => a + mix[x], 0)
    const next: Record<string, number> = { [k]: v }
    let acc = 0
    others.forEach((x, i) => {
      const share = otherTotal > 0 ? Math.round((mix[x] / otherTotal) * rest) : Math.round(rest / others.length)
      next[x] = i === others.length - 1 ? Math.max(0, rest - acc) : share
      acc += next[x]
    })
    onChange(next)
  }
  return (
    <div className="flex flex-col gap-3">
      {keys.map((k) => <Slider key={k} label={k} value={mix[k]} min={0} max={100} format={(v) => `${v}%`} onChange={(v) => setOne(k, v)} />)}
      <div className="h-2 rounded-full overflow-hidden flex border border-line">
        {keys.map((k, i) => <div key={k} style={{ width: `${mix[k]}%` }} className={['bg-green', 'bg-steel', 'bg-ok', 'bg-amber', 'bg-muted'][i % 5]} title={`${k} ${mix[k]}%`} />)}
      </div>
    </div>
  )
}
