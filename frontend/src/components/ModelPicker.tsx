import clsx from 'clsx'
import { useEffect, useMemo, useRef, useState } from 'react'
import type { ModelInfo, ModelSlot } from '../lib/types'
import { useModels } from '../lib/queries'
import { contextK } from '../lib/format'
import { Icon } from './Icon'
import { Input, NumberInput, Select } from './Fields'
import { Slider } from './Slider'
import { Toggle } from './Toggle'

const PROVIDERS = ['', 'anthropic', 'openai', 'google', 'together', 'fireworks', 'deepinfra', 'groq', 'azure', 'amazon-bedrock']

interface Props {
  value: ModelSlot
  onChange: (v: ModelSlot) => void
  label?: string
  compact?: boolean // hide temperature / max_tokens / provider editing
  className?: string
  warn?: string | null
}

export function ModelPicker({ value, onChange, label, compact, className, warn }: Props) {
  const [open, setOpen] = useState(false)
  const [q, setQ] = useState('')
  const [adv, setAdv] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  const models = useModels('')
  const list = useMemo(() => {
    const n = q.trim().toLowerCase()
    const all = models.data ?? []
    return n ? all.filter((m) => m.id.toLowerCase().includes(n) || m.name.toLowerCase().includes(n)) : all
  }, [models.data, q])
  const current = models.data?.find((m) => m.id === value.slug)

  useEffect(() => {
    if (!open) return
    const onDoc = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false) }
    document.addEventListener('mousedown', onDoc)
    return () => document.removeEventListener('mousedown', onDoc)
  }, [open])

  const pickModel = (m: ModelInfo) => { onChange({ ...value, slug: m.id }); setOpen(false); setQ('') }

  return (
    <div ref={ref} className={clsx('flex flex-col gap-1.5 min-w-0', className)}>
      {label && <span className="label">{label}</span>}
      <div className="relative">
        <button
          type="button" onClick={() => setOpen((o) => !o)}
          className={clsx('field w-full h-10 flex items-center gap-2.5 text-left', open && '!border-cyan/60', warn && '!border-amber/60')}
        >
          <span className="w-6 h-6 rounded-[5px] bg-surface2 border border-line2 flex items-center justify-center text-cyan shrink-0"><Icon name="sparkle" size={12} /></span>
          <span className="flex flex-col min-w-0 flex-1 leading-tight">
            <span className="text-[12.5px] text-text truncate">{value.slug || 'Select a model'}</span>
            {current && <span className="text-[10px] text-muted truncate">{current.name} · ${current.prompt_price_per_m}/${current.completion_price_per_m} per 1M · {contextK(current.context_length)} ctx</span>}
          </span>
          <Icon name="chevron" size={12} className={clsx('text-muted transition-transform', open ? '-rotate-90' : 'rotate-90')} />
        </button>
        {open && (
          <div className="absolute z-40 left-0 right-0 mt-1 panel !bg-surface2 shadow-[0_18px_40px_rgba(0,0,0,.6)] overflow-hidden modal-in">
            <div className="p-2 border-b border-line flex items-center gap-2">
              <Icon name="search" size={13} className="text-muted" />
              <input autoFocus value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search slug or name…" className="bg-transparent outline-none flex-1 font-mono text-[12px] placeholder:text-dim" />
              <span className="font-mono text-[10px] text-dim">{list.length}</span>
            </div>
            <div className="max-h-[260px] overflow-y-auto">
              <div className="grid grid-cols-[1fr_auto_auto_auto] gap-x-3 px-3 py-1 label !text-[9.5px] sticky top-0 bg-surface2">
                <span>model</span><span className="text-right">in $/1M</span><span className="text-right">out $/1M</span><span className="text-right">ctx</span>
              </div>
              {list.map((m) => (
                <button
                  key={m.id} type="button" onClick={() => pickModel(m)}
                  className={clsx('w-full grid grid-cols-[1fr_auto_auto_auto] gap-x-3 items-center px-3 py-1.5 text-left hover:bg-cyan/10 border-t border-line/50', m.id === value.slug && 'bg-cyan/10')}
                >
                  <span className="flex flex-col min-w-0 leading-tight">
                    <span className="font-mono text-[12px] truncate">{m.id}</span>
                    <span className="text-[11px] text-muted truncate">{m.name}{!m.supports_json_schema && <span className="text-dim"> · no json</span>}{!m.supports_tools && <span className="text-dim"> · no tools</span>}</span>
                  </span>
                  <span className="font-mono text-[11px] text-cyan tabular-nums text-right">{m.prompt_price_per_m.toFixed(2)}</span>
                  <span className="font-mono text-[11px] text-magenta tabular-nums text-right">{m.completion_price_per_m.toFixed(2)}</span>
                  <span className="font-mono text-[11px] text-muted tabular-nums text-right">{contextK(m.context_length)}</span>
                </button>
              ))}
              {list.length === 0 && <div className="px-3 py-6 text-center text-dim font-mono text-[11px]">{(models.data ?? []).length === 0 ? 'Catalogue empty — set an OpenRouter key in Settings, then Refresh catalogue. You can still type a slug below.' : 'No models match'}</div>}
            </div>
          </div>
        )}
      </div>
      {warn && <div className="flex items-center gap-1.5 text-amber text-[12px]"><Icon name="warning" size={12} />{warn}</div>}
      {compact && (models.data ?? []).length === 0 && <div className="flex items-center gap-2"><span className="label whitespace-nowrap">slug</span><Input value={value.slug} onChange={(e) => onChange({ ...value, slug: e.target.value })} placeholder="vendor/model" className="!h-7" /></div>}
      {!compact && (
        <div className="flex flex-col gap-2">
          <button type="button" onClick={() => setAdv((a) => !a)} className="self-start flex items-center gap-1 label hover:text-text">
            <Icon name="chevron" size={9} className={clsx('transition-transform', adv && 'rotate-90')} />
            temp {value.temperature} · max {value.max_tokens}{value.provider_order.length ? ` · pin ${value.provider_order[0]}` : ''}
          </button>
          {adv && (
            <div className="grid grid-cols-2 gap-3 p-3 rounded-btn border border-line bg-bg/40">
              <Slider label="temperature" value={value.temperature} min={0} max={2} step={0.05} onChange={(t) => onChange({ ...value, temperature: t })} format={(v) => v.toFixed(2)} className="col-span-2" />
              <div className="flex flex-col gap-1.5"><span className="label">max tokens</span><NumberInput value={value.max_tokens} min={64} max={32000} step={64} onChange={(v) => onChange({ ...value, max_tokens: v })} /></div>
              <div className="flex flex-col gap-1.5"><span className="label">pin provider</span>
                <Select value={value.provider_order[0] ?? ''} onChange={(e) => onChange({ ...value, provider_order: e.target.value ? [e.target.value] : [] })}>
                  {PROVIDERS.map((p) => <option key={p} value={p}>{p || 'auto (OpenRouter)'}</option>)}
                </Select>
              </div>
              <Toggle size="sm" checked={value.allow_fallbacks} onChange={(v) => onChange({ ...value, allow_fallbacks: v })} label="Allow fallback providers" className="col-span-2" />
              <div className="col-span-2 flex flex-col gap-1.5"><span className="label">custom slug</span><Input value={value.slug} onChange={(e) => onChange({ ...value, slug: e.target.value })} placeholder="vendor/model" /></div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
