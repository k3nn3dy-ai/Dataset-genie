import { useMemo, useState } from 'react'
import { useParams } from 'react-router-dom'
import type { Pair, PreferencesConfig } from '../../lib/types'
import { usePairs, useRows } from '../../lib/queries'
import { StageScreen } from '../shared/StageScreen'
import { useConfigSection } from '../shared/useConfigSection'
import { Chip, EmptyState, IconButton, IdCell, Input, ModelPicker, MonoTable, Panel, RadioGroup, Slider, Spinner, StatTile, toneFor, Button } from '../../components'
import { PairDiff } from './PairDiff'
import { usd } from '../../lib/format'

export function RejectedScreen() {
  const { projectId } = useParams()
  const { draft, setDraft, loading, projectError, refetch, project } = useConfigSection(projectId, 'preferences')
  // Mirrors backend preferences.ELIGIBLE_STATUSES: stage 03 writes rows as `draft`; `accepted` is only set by an explicit review action.
  const rows = useRows(projectId, { status: 'draft,accepted,edited', page_size: 2000 })
  const pairs = usePairs(projectId)
  const [active, setActive] = useState<Pair | null>(null)
  const eligible = rows.data?.total ?? 0
  const items = pairs.data?.items ?? []
  const current = active ?? items[0] ?? null
  const flawTotal = draft?.flaws.reduce((a, f) => a + f.weight, 0) ?? 0
  const estCost = useMemo(() => eligible * (draft?.strategy === 'weaker' ? 0.0012 : 0.0085), [eligible, draft?.strategy])
  const blocked = rows.data && eligible === 0 ? { title: 'No eligible rows', body: 'Rejected responses are manufactured from generated rows. Run stage 03 first.', stage: 3 as const } : null
  const notDpo = project && !project.data_types.includes('dpo')

  const config = draft ? (
    <>
      <Panel title="Strategy" kana="戦略">
        <RadioGroup value={draft.strategy} onChange={(strategy) => setDraft({ strategy })} options={[
          { key: 'corruptor', label: 'Corruptor', hint: 'Same teacher, instructed to inject exactly one flaw from the weighted list' },
          { key: 'weaker', label: 'Weaker model', hint: 'A cheaper model answers the same prompt' },
          { key: 'hightemp', label: 'High temperature', hint: 'Teacher at temperature ≥ 1.2' },
        ]} />
        {draft.strategy === 'weaker' && <div className="mt-3"><ModelPicker label="weaker model" value={draft.weaker_model} onChange={(weaker_model) => setDraft({ weaker_model })} /></div>}
        {draft.strategy === 'hightemp' && <div className="mt-3"><Slider label="temperature" value={draft.hightemp_temperature} min={1.2} max={2} step={0.05} format={(v) => v.toFixed(2)} tone="magenta" onChange={(hightemp_temperature) => setDraft({ hightemp_temperature })} /></div>}
      </Panel>
      <Panel title="Flaw list" kana="欠陥" actions={<span className="font-mono text-[10px] text-dim">weights {flawTotal}</span>}>
        <div className={draft.strategy === 'corruptor' ? 'flex flex-col gap-3' : 'flex flex-col gap-3 opacity-40 pointer-events-none'}>
          {draft.flaws.map((f, i) => (
            <div key={i} className="rounded-btn border border-line bg-bg/40 p-2.5 flex flex-col gap-2">
              <div className="flex items-center gap-2">
                <Input value={f.name} onChange={(e) => setDraft({ flaws: draft.flaws.map((x, k) => (k === i ? { ...x, name: e.target.value } : x)) })} className="!h-7 flex-1" />
                <span className="font-mono text-[11px] text-magenta tabular-nums w-12 text-right">{flawTotal ? ((f.weight / flawTotal) * 100).toFixed(0) : 0}%</span>
                <IconButton icon="trash" label="Remove flaw" size="sm" onClick={() => setDraft({ flaws: draft.flaws.filter((_, k) => k !== i) })} />
              </div>
              <Input mono={false} value={f.instruction} onChange={(e) => setDraft({ flaws: draft.flaws.map((x, k) => (k === i ? { ...x, instruction: e.target.value } : x)) })} className="!h-7 !text-[12.5px]" />
              <Slider value={f.weight} min={0} max={100} tone="magenta" onChange={(w) => setDraft({ flaws: draft.flaws.map((x, k) => (k === i ? { ...x, weight: w } : x)) })} />
            </div>
          ))}
          <Button size="sm" variant="outline" icon="plus" className="self-start" onClick={() => setDraft({ flaws: [...draft.flaws, { name: 'new_flaw', weight: 10, instruction: 'Describe the flaw to inject.' }] })}>Add flaw</Button>
        </div>
      </Panel>
      <div className="grid grid-cols-2 gap-3">
        <StatTile label="eligible pairs" value={eligible} tone="cyan" hint="eligible rows" />
        <StatTile label="est. cost" value={usd(estCost)} tone="magenta" hint={`${draft.strategy} · ${eligible} calls`} />
      </div>
    </>
  ) : <Spinner />

  const results = pairs.isLoading ? <Spinner /> : items.length === 0 ? (
    <EmptyState title="No pairs yet" kana="未生成" body={notDpo ? 'This project has no DPO data type; pairs are optional.' : 'Run stage 04 to manufacture a rejected response for every accepted row.'} />
  ) : (
    <div className="flex flex-col gap-3">
      <div className="grid grid-cols-4 gap-2">
        <StatTile size="sm" label="pairs" value={items.length} tone="cyan" />
        <StatTile size="sm" label="ties (judged)" value={items.filter((p) => p.metadata.judge?.verdict === 'tie').length} tone="magenta" hint="excluded from DPO" />
        <StatTile size="sm" label="strategy" value={<span className="text-[15px]">{items[0]?.metadata.strategy}</span>} />
        <StatTile size="sm" label="flaws used" value={new Set(items.map((p) => p.metadata.flaw)).size} />
      </div>
      <MonoTable rows={items} rowKey={(p) => p.metadata.id} onRowClick={setActive} activeKey={current?.metadata.id} maxHeight="220px"
        columns={[
          { key: 'id', header: 'id', render: (p) => <IdCell id={p.metadata.id} />, sortValue: (p) => p.metadata.id },
          { key: 'flaw', header: 'flaw', render: (p) => <Chip tone="magenta">{p.metadata.flaw}</Chip>, sortValue: (p) => p.metadata.flaw ?? '' },
          { key: 'leaf', header: 'leaf', render: (p) => <span className="text-muted">{p.metadata.leaf_path.at(-1)}</span> },
          { key: 'verdict', header: 'verdict', render: (p) => <Chip tone={toneFor(p.metadata.judge?.verdict ?? 'dim')}>{p.metadata.judge?.verdict ?? 'unjudged'}</Chip>, sortValue: (p) => p.metadata.judge?.verdict ?? '' },
        ]} />
      {current && <PairDiff pair={current} />}
    </div>
  )

  return (
    <StageScreen stage={4} params={{ ...(draft ?? ({} as PreferencesConfig)) }} config={config} results={results} resultsCount={items.length} blocked={blocked} loading={loading} error={projectError ?? pairs.error} onRetry={() => { void refetch(); void pairs.refetch() }}
      subtitle="For each accepted row, manufacture a plausible-but-worse response. Pairs feed DPO / ORPO export; ties are excluded." />
  )
}
