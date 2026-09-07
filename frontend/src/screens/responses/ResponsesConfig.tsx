import { useState } from 'react'
import type { ModelSlot, ResponsesConfig } from '../../lib/types'
import { Button, Field, IconButton, ModelPicker, NumberInput, Panel, Segmented, Select, Slider, Textarea, Toggle } from '../../components'

type Set = (p: Partial<ResponsesConfig>) => void
const newSlot = (): ModelSlot => ({ slug: 'openai/gpt-4.1', provider_order: [], allow_fallbacks: true, temperature: 0.7, max_tokens: 2048, weight: 1 })

export function EnsembleEditor({ cfg, onChange }: { cfg: ResponsesConfig; onChange: Set }) {
  const [adding, setAdding] = useState(false)
  const [pending, setPending] = useState<ModelSlot>(newSlot())
  const setSlot = (i: number, s: ModelSlot) => onChange({ ensemble: cfg.ensemble.map((x, k) => (k === i ? s : x)) })
  const totalW = cfg.ensemble.reduce((a, s) => a + s.weight, 0)
  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center justify-between">
        <span className="label">selection</span>
        <Segmented options={[{ key: 'round-robin', label: 'Round-robin' }, { key: 'weighted', label: 'Weighted' }]} value={cfg.selection} onChange={(selection) => onChange({ selection })} />
      </div>
      {cfg.ensemble.map((s, i) => (
        <div key={i} className="rounded-btn border border-line bg-bg/40 p-2.5 flex flex-col gap-2">
          <div className="flex items-start gap-2">
            <ModelPicker value={s} onChange={(v) => setSlot(i, v)} className="flex-1" />
            <IconButton icon="trash" label="Remove model" size="sm" disabled={cfg.ensemble.length === 1} onClick={() => onChange({ ensemble: cfg.ensemble.filter((_, k) => k !== i) })} />
          </div>
          {cfg.selection === 'weighted' && <Slider label={`weight · ${totalW > 0 ? ((s.weight / totalW) * 100).toFixed(0) : 0}% of traffic`} value={s.weight} min={0} max={5} step={0.1} format={(v) => v.toFixed(1)} onChange={(w) => setSlot(i, { ...s, weight: w })} />}
        </div>
      ))}
      {adding ? (
        <div className="rounded-btn border border-cyan/40 p-2.5 flex flex-col gap-2">
          <ModelPicker label="add teacher" value={pending} onChange={setPending} compact />
          <div className="flex gap-2 justify-end"><Button size="sm" onClick={() => setAdding(false)}>Cancel</Button><Button size="sm" variant="primary" icon="plus" onClick={() => { onChange({ ensemble: [...cfg.ensemble, pending] }); setAdding(false); setPending(newSlot()) }}>Add</Button></div>
        </div>
      ) : <Button size="sm" variant="outline" icon="plus" onClick={() => setAdding(true)} className="self-start">Add model</Button>}
      <div className="grid grid-cols-2 gap-3">
        <Slider label="temperature" value={cfg.temperature} min={0} max={2} step={0.05} format={(v) => v.toFixed(2)} onChange={(temperature) => onChange({ temperature })} />
        <Field label="max tokens"><NumberInput value={cfg.max_tokens} min={128} max={32000} step={128} onChange={(max_tokens) => onChange({ max_tokens })} /></Field>
      </div>
      <Toggle checked={cfg.reasoning_tags} onChange={(reasoning_tags) => onChange({ reasoning_tags })} label={<span>Wrap reasoning in <span className="font-mono text-cyan">&lt;think&gt;</span> tags</span>} hint="Kept as a `reasoning` field on GRPO export" />
    </div>
  )
}

export function SystemPromptPanel({ cfg, onChange }: { cfg: ResponsesConfig; onChange: Set }) {
  return (
    <Panel title="System prompt" kana="指示">
      <div className="flex flex-col gap-3">
        <Textarea value={cfg.system_prompt} onChange={(e) => onChange({ system_prompt: e.target.value })} className="!min-h-[96px]" />
        <div className="grid grid-cols-[1fr_auto] gap-3 items-end">
          <Field label="policy">
            <Select value={cfg.system_prompt_policy} onChange={(e) => onChange({ system_prompt_policy: e.target.value as ResponsesConfig['system_prompt_policy'] })}>
              <option value="always">always include</option><option value="never">never include</option><option value="random">random share of rows</option>
            </Select>
          </Field>
          {cfg.system_prompt_policy === 'random' && <div className="w-[130px]"><Slider label="share" value={cfg.system_prompt_random_pct} min={0} max={100} format={(v) => `${v}%`} onChange={(system_prompt_random_pct) => onChange({ system_prompt_random_pct })} /></div>}
        </div>
      </div>
    </Panel>
  )
}

export function MultiTurnPanel({ cfg, onChange }: { cfg: ResponsesConfig; onChange: Set }) {
  return (
    <Panel title="Multi-turn" kana="対話" actions={<Toggle size="sm" checked={cfg.multi_turn} onChange={(multi_turn) => onChange({ multi_turn })} />}>
      <div className={cfg.multi_turn ? 'flex flex-col gap-3' : 'flex flex-col gap-3 opacity-40 pointer-events-none'}>
        <ModelPicker label="simulated user model" value={cfg.simulated_user_model} onChange={(simulated_user_model) => onChange({ simulated_user_model })} compact />
        <div className="grid grid-cols-3 gap-3">
          <Field label="turns min"><NumberInput value={cfg.turns_min} min={2} max={cfg.turns_max} onChange={(turns_min) => onChange({ turns_min })} /></Field>
          <Field label="turns max"><NumberInput value={cfg.turns_max} min={cfg.turns_min} max={4} onChange={(turns_max) => onChange({ turns_max })} /></Field>
          <Field label="user mood">
            <Select value={cfg.user_mood} onChange={(e) => onChange({ user_mood: e.target.value as ResponsesConfig['user_mood'] })}>
              <option value="cooperative">cooperative</option><option value="confused">confused</option><option value="hostile">hostile</option>
            </Select>
          </Field>
        </div>
      </div>
    </Panel>
  )
}
