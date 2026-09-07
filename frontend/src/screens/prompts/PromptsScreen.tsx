import { useMemo, useState } from 'react'
import { useParams } from 'react-router-dom'
import { usePrompts, useResample, useTaxonomy } from '../../lib/queries'
import { StageScreen } from '../shared/StageScreen'
import { useConfigSection } from '../shared/useConfigSection'
import { Button, Chip, EmptyState, Field, Input, ModelPicker, Panel, Slider, Spinner, StatTile, toneFor } from '../../components'
import { PersonaEditor, StyleMix } from './PromptsConfig'
import { flattenLeaves } from '../../lib/mock/project'
import { num } from '../../lib/format'

export function PromptsScreen() {
  const { projectId } = useParams()
  const { draft, setDraft, loading, projectError, refetch } = useConfigSection(projectId, 'prompts')
  const tax = useTaxonomy(projectId)
  const prompts = usePrompts(projectId, {})
  const resample = useResample(projectId)
  const [q, setQ] = useState('')
  const [leafFilter, setLeafFilter] = useState<string | null>(null)
  const leaves = useMemo(() => (tax.data ? flattenLeaves(tax.data) : []), [tax.data])
  const items = prompts.data?.items ?? []
  const grouped = useMemo(() => {
    const m = new Map<string, typeof items>()
    for (const p of items) {
      if (leafFilter && p.leaf_id !== leafFilter) continue
      if (q && !p.text.toLowerCase().includes(q.toLowerCase())) continue
      const arr = m.get(p.leaf_id) ?? []; arr.push(p); m.set(p.leaf_id, arr)
    }
    return [...m.entries()]
  }, [items, leafFilter, q])
  const adversarial = items.filter((p) => p.adversarial).length
  const blocked = tax.data && tax.data.length === 0 ? { title: 'No taxonomy yet', body: 'Prompts are generated per leaf. Generate or hand-write the taxonomy in stage 01 first.', stage: 1 as const } : null

  const config = draft ? (
    <>
      <Panel title="Prompt model" kana="模型"><ModelPicker value={draft.model} onChange={(m) => setDraft({ model: m })} /></Panel>
      <Panel title="Personas" kana="人物" actions={<span className="font-mono text-[10px] text-dim">weights sum {draft.personas.reduce((a, p) => a + p.weight, 0)}%</span>}>
        <PersonaEditor personas={draft.personas} onChange={(personas) => setDraft({ personas })} />
      </Panel>
      <Panel title="Style mix" kana="文体" actions={<span className="font-mono text-[10px] text-cyan">= 100%</span>}>
        <StyleMix mix={draft.style_mix} onChange={(style_mix) => setDraft({ style_mix })} />
      </Panel>
      <Panel title="Sampling" kana="標本">
        <div className="flex flex-col gap-4">
          <Slider label="temperature" value={draft.temperature} min={0} max={2} step={0.05} format={(v) => v.toFixed(2)} onChange={(v) => setDraft({ temperature: v })} />
          <Slider label="noise · typos, ambiguity, missing context" value={draft.noise_level} min={0} max={1} step={0.01} format={(v) => `${Math.round(v * 100)}%`} onChange={(v) => setDraft({ noise_level: v })} />
          <Slider label="adversarial share" value={draft.adversarial_pct} min={0} max={50} step={0.5} format={(v) => `${v}%`} tone="magenta" onChange={(v) => setDraft({ adversarial_pct: v })} />
          <Slider label="near-dup threshold · cosine" value={draft.near_dup_threshold} min={0.8} max={0.99} step={0.005} format={(v) => v.toFixed(3)} tone="amber" onChange={(v) => setDraft({ near_dup_threshold: v })} />
          <Field label="embedding model"><Input value={draft.embedding_model} onChange={(e) => setDraft({ embedding_model: e.target.value })} /></Field>
        </div>
      </Panel>
    </>
  ) : <Spinner />

  const results = prompts.isLoading ? <Spinner /> : items.length === 0 ? (
    <EmptyState title="No prompts yet" kana="未生成" body="Run stage 02 to sample user prompts for every leaf using the personas and style mix on the left." />
  ) : (
    <div className="flex flex-col gap-3">
      <div className="grid grid-cols-3 gap-2">
        <StatTile size="sm" label="prompts" value={num(items.length)} tone="cyan" />
        <StatTile size="sm" label="leaves covered" value={`${new Set(items.map((p) => p.leaf_id)).size}`} unit={`/ ${leaves.length}`} />
        <StatTile size="sm" label="adversarial" value={adversarial} tone="magenta" hint={`${((adversarial / Math.max(1, items.length)) * 100).toFixed(1)}%`} />
      </div>
      <div className="flex items-center gap-2">
        <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="search prompts…" className="!h-8 max-w-[260px]" />
        <div className="flex gap-1 flex-wrap flex-1">
          <Chip tone={leafFilter ? 'dim' : 'cyan'} onClick={() => setLeafFilter(null)}>all leaves</Chip>
          {leafFilter && <Chip tone="cyan" onRemove={() => setLeafFilter(null)}>{leaves.find((l) => l.id === leafFilter)?.label}</Chip>}
        </div>
      </div>
      <div className="flex flex-col gap-3 max-h-[70vh] overflow-y-auto pr-1">
        {grouped.map(([leafId, ps]) => (
          <div key={leafId} className="rounded-card border border-line bg-bg/40">
            <div className="flex items-center justify-between px-3 h-9 border-b border-line">
              <button type="button" onClick={() => setLeafFilter(leafId)} className="font-mono text-[11px] text-left truncate hover:text-cyan"><span className="text-dim">{ps[0].leaf_path.slice(0, -1).join(' / ')} / </span><span className="text-text">{ps[0].leaf_path.at(-1)}</span></button>
              <div className="flex items-center gap-2 shrink-0">
                <Chip tone={toneFor(ps[0].difficulty)}>{ps[0].difficulty}</Chip>
                <span className="font-mono text-[10px] text-dim">{ps.length}</span>
                <Button size="sm" variant="outline" icon="refresh" loading={resample.isPending && resample.variables === leafId} onClick={() => resample.mutate(leafId)}>Resample</Button>
              </div>
            </div>
            <ul className="hairline">
              {ps.map((p) => (
                <li key={p.id} className="px-3 py-2 flex gap-3">
                  <div className="flex-1 min-w-0">
                    <p className="text-[13px] leading-snug whitespace-pre-wrap line-clamp-3">{p.text}</p>
                    <div className="flex items-center gap-1.5 mt-1.5 font-mono text-[10px] text-dim"><span className="truncate">{p.id}</span></div>
                  </div>
                  <div className="flex flex-col items-end gap-1 shrink-0">
                    <Chip tone="dim">{p.style}</Chip><Chip tone="default">{p.persona}</Chip>{p.adversarial && <Chip tone="magenta">adversarial</Chip>}
                  </div>
                </li>
              ))}
            </ul>
          </div>
        ))}
        {grouped.length === 0 && <div className="font-mono text-[11px] text-dim text-center py-6">No prompts match.</div>}
      </div>
    </div>
  )

  return (
    <StageScreen stage={2} params={{ ...(draft ?? {}) }} config={config} results={results} resultsCount={items.length} blocked={blocked} loading={loading} error={projectError ?? prompts.error} onRetry={() => { void refetch(); void prompts.refetch() }}
      subtitle="Per leaf, one structured call requesting N prompts under a weighted persona and style. A near-dup guard rejects lookalikes and resamples." />
  )
}
