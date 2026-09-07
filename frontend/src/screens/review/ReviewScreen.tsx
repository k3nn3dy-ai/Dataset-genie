import { useMemo, useState } from 'react'
import { useParams } from 'react-router-dom'
import type { RowItem } from '../../lib/viewtypes'
import { useBulkRows, useRows, useTaxonomy } from '../../lib/queries'
import { StageScreen } from '../shared/StageScreen'
import { Button, Chip, EmptyState, IdCell, Input, MonoTable, TextCell, Panel, Select, Slider, Spinner, StatTile, toneFor } from '../../components'
import { RowDrawer } from './RowDrawer'
import { flattenLeaves } from '../../lib/mock/project'
import { truncate } from '../../lib/format'

const STATUSES = ['all', 'accepted', 'edited', 'flagged', 'refusal', 'filtered', 'draft'] as const
const FLAGS = ['low_score', 'refusal', 'pii', 'near_dup', 'edited', 'flagged', 'adversarial']

export function ReviewScreen() {
  const { projectId } = useParams()
  const [q, setQ] = useState('')
  const [status, setStatus] = useState<string>('all')
  const [minScore, setMinScore] = useState(0)
  const [flags, setFlags] = useState<string[]>([])
  const [leaf, setLeaf] = useState('')
  const [sel, setSel] = useState<Set<string>>(new Set())
  const [openId, setOpenId] = useState<string | null>(null)
  const tax = useTaxonomy(projectId)
  const leaves = useMemo(() => (tax.data ? flattenLeaves(tax.data) : []), [tax.data])
  const query = useMemo(() => ({ q: q || undefined, status: status === 'all' ? undefined : status, min_score: minScore > 0 ? minScore : undefined, flags: flags.length ? flags : undefined, leaf: leaf || undefined, page_size: 500 }), [q, status, minScore, flags, leaf])
  const rows = useRows(projectId, query)
  const all = useRows(projectId, { page_size: 1 })
  const bulk = useBulkRows(projectId)
  const items = rows.data?.items ?? []
  const blocked = all.data && all.data.total === 0 ? { title: 'Nothing to review', body: 'Review works over teacher responses. Run stage 03 first.', stage: 3 as const } : null
  const counts = useMemo(() => ({ accepted: items.filter((r) => r.status === 'accepted' || r.status === 'edited').length, flagged: items.filter((r) => r.status === 'flagged').length, low: items.filter((r) => r.metadata.flags.includes('low_score')).length }), [items])

  const config = (
    <>
      <Panel title="Search & filters" kana="検索">
        <div className="flex flex-col gap-4">
          <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="search id, prompt, response…" />
          <div>
            <span className="label block mb-1.5">status</span>
            <div className="flex flex-wrap gap-1">{STATUSES.map((s) => <Chip key={s} tone={status === s ? 'cyan' : 'dim'} active={status === s} onClick={() => setStatus(s)}>{s}</Chip>)}</div>
          </div>
          <Slider label="min score" value={minScore} min={0} max={5} step={0.1} format={(v) => (v === 0 ? 'any' : v.toFixed(1))} tone="amber" onChange={setMinScore} />
          <div>
            <span className="label block mb-1.5">flags</span>
            <div className="flex flex-wrap gap-1">{FLAGS.map((f) => <Chip key={f} tone={flags.includes(f) ? toneFor(f) : 'dim'} active={flags.includes(f)} onClick={() => setFlags((x) => (x.includes(f) ? x.filter((y) => y !== f) : [...x, f]))}>{f}</Chip>)}</div>
          </div>
          <div><span className="label block mb-1.5">leaf</span>
            <Select value={leaf} onChange={(e) => setLeaf(e.target.value)}><option value="">all leaves</option>{leaves.map((l) => <option key={l.id} value={l.id}>{l.path.join(' / ')}</option>)}</Select>
          </div>
          {(q || status !== 'all' || minScore > 0 || flags.length || leaf) ? <Button size="sm" variant="ghost" icon="x" onClick={() => { setQ(''); setStatus('all'); setMinScore(0); setFlags([]); setLeaf('') }}>Clear filters</Button> : null}
        </div>
      </Panel>
      <div className="grid grid-cols-3 gap-2">
        <StatTile size="sm" label="matching" value={items.length} tone="cyan" />
        <StatTile size="sm" label="accepted" value={counts.accepted} tone="acid" />
        <StatTile size="sm" label="flagged" value={counts.flagged} tone="amber" hint={`${counts.low} low_score`} />
      </div>
    </>
  )

  const results = rows.isLoading ? <Spinner /> : items.length === 0 && !q && status === 'all' ? (
    <EmptyState title="No rows" kana="空" body="No rows match. Adjust the filters on the left." />
  ) : (
    <div className="flex flex-col gap-2">
      {sel.size > 0 && (
        <div className="panel !bg-surface2 flex items-center gap-2 px-3 h-10 border-cyan/40">
          <span className="font-mono text-[11px] text-cyan">{sel.size} selected</span>
          <span className="flex-1" />
          <Button size="sm" variant="primary" icon="check" loading={bulk.isPending} onClick={() => bulk.mutate({ ids: [...sel], action: 'accept' }, { onSuccess: () => setSel(new Set()) })}>Accept</Button>
          <Button size="sm" variant="outline" icon="flag" loading={bulk.isPending} onClick={() => bulk.mutate({ ids: [...sel], action: 'flag' }, { onSuccess: () => setSel(new Set()) })}>Flag</Button>
          <Button size="sm" variant="ghost" icon="x" onClick={() => setSel(new Set())}>Clear</Button>
        </div>
      )}
      <MonoTable rows={items} rowKey={(r) => r.metadata.id} selectable selected={sel} onSelectedChange={setSel} onRowClick={(r) => setOpenId(r.metadata.id)} activeKey={openId} maxHeight="calc(100vh - 340px)"
        rowTone={(r) => (r.status === 'refusal' || r.status === 'flagged' ? 'amber' : r.status === 'filtered' ? 'dim' : 'default')}
        columns={[
          { key: 'id', header: 'id', render: (r: RowItem) => <IdCell id={r.metadata.id} max={150} />, sortValue: (r) => r.metadata.id },
          { key: 'leaf', header: 'leaf', render: (r) => <span className="text-muted">{truncate(r.metadata.leaf_path.at(-1) ?? '', 18)}</span>, sortValue: (r) => r.metadata.leaf_path.at(-1) ?? '' },
          { key: 'prompt', header: 'prompt excerpt', render: (r) => <TextCell max={140} className="text-text/80" text={(r.messages.find((m) => m.role === 'user')?.content ?? '').replace(/\n+/g, ' ')} /> },
          { key: 'score', header: 'score', align: 'right', width: '60px', render: (r) => <span className={(r.metadata.judge?.score ?? 5) < 3 ? 'text-amber' : 'text-acid'}>{r.metadata.judge?.score.toFixed(1) ?? '—'}</span>, sortValue: (r) => r.metadata.judge?.score ?? -1 },
          { key: 'status', header: 'status', width: '90px', render: (r) => <Chip tone={toneFor(r.status)}>{r.status}</Chip>, sortValue: (r) => r.status },
          { key: 'flags', header: 'flags', render: (r) => <div className="flex gap-1">{r.metadata.flags.slice(0, 2).map((f) => <Chip key={f} tone={toneFor(f)}>{f}</Chip>)}{r.metadata.flags.length > 2 && <Chip tone="dim" title={r.metadata.flags.slice(2).join(', ')}>+{r.metadata.flags.length - 2}</Chip>}</div> },
        ]} />
      <RowDrawer projectId={projectId} rowId={openId} onClose={() => setOpenId(null)} onNav={(dir) => { const i = items.findIndex((r) => r.metadata.id === openId); const n = items[i + dir]; if (n) setOpenId(n.metadata.id) }} />
    </div>
  )

  return (
    <StageScreen stage={7} params={{}} noRun config={config} results={results} resultsCount={items.length} blocked={blocked} error={rows.error} onRetry={() => rows.refetch()} configWidth={360}
      idleHint="no model calls in this stage — the run is you" subtitle="Human in the loop. Search, inspect the full conversation and judge rationale, then accept, edit inline or flag." />
  )
}
