import { useState } from 'react'
import { useParams } from 'react-router-dom'
import type { FilterConfig } from '../../lib/types'
import type { FilterRuleSummary } from '../../lib/viewtypes'
import { useFilterSummary, useRestoreRows, useRows, useRunFilter } from '../../lib/queries'
import { StageScreen } from '../shared/StageScreen'
import { useConfigSection } from '../shared/useConfigSection'
import { Button, Chip, EmptyState, Field, IdCell, Input, MonoTable, NumberInput, Panel, Slider, Spinner, StatTile, Tabs, Toggle, toneFor } from '../../components'
import { truncate } from '../../lib/format'

const RULE_KEYS: FilterRuleSummary['name'][] = ['exact_dup', 'near_dup', 'refusal', 'pii', 'length', 'language']
const RULE_LABEL: Record<FilterRuleSummary['name'], string> = { exact_dup: 'Exact duplicate', near_dup: 'Near duplicate', refusal: 'Refusal → bucket', pii: 'PII', length: 'Length bounds', language: 'Language' }

export function FilterScreen() {
  const { projectId } = useParams()
  const { draft, setDraft, loading, projectError, refetch } = useConfigSection(projectId, 'filters')
  const summary = useFilterSummary(projectId)
  const rows = useRows(projectId, { page_size: 1 })
  const restore = useRestoreRows(projectId)
  const runFilter = useRunFilter(projectId)
  const [tab, setTab] = useState<'removed' | 'refusals'>('removed')
  const [sel, setSel] = useState<Set<string>>(new Set())
  const s = summary.data
  const removedFor = (k: FilterRuleSummary['name']) => s?.rules.find((r) => r.name === k)?.removed ?? 0
  const blocked = rows.data && rows.data.total === 0 ? { title: 'No rows to filter', body: 'Filters run over teacher responses. Run stage 03 first.', stage: 3 as const } : null
  const list = tab === 'removed' ? s?.removed ?? [] : s?.refusals ?? []

  const config = draft ? (
    <>
      <Panel title="Rules" kana="規則" actions={<span className="font-mono text-[10px] text-dim">toggle = instant recount</span>}>
        <div className="flex flex-col gap-3">
          {RULE_KEYS.map((k) => (
            <div key={k} className="flex items-center justify-between gap-3">
              <Toggle checked={draft[k]} onChange={(v) => setDraft({ [k]: v } as Partial<FilterConfig>)} label={RULE_LABEL[k]} hint={k === 'refusal' ? 'kept in a bucket, never deleted' : k === 'pii' ? 'emails · public IPv4 · UK NI numbers' : k === 'near_dup' ? `cosine ≥ ${draft.near_dup_threshold}` : k === 'length' ? `${draft.min_chars}–${draft.max_chars} chars` : k === 'language' ? `expected ${draft.expected_language}` : 'sha256 of normalised text'} />
              <span className={`font-mono text-[13px] tabular-nums ${removedFor(k) > 0 ? (k === 'refusal' ? 'text-amber' : 'text-red') : 'text-dim'}`}>{draft[k] ? `−${removedFor(k)}` : 'off'}</span>
            </div>
          ))}
        </div>
      </Panel>
      <Panel title="Parameters" kana="設定">
        <div className="flex flex-col gap-4">
          <Slider label="near-dup cosine" value={draft.near_dup_threshold} min={0.8} max={0.99} step={0.005} format={(v) => v.toFixed(3)} tone="amber" onChange={(near_dup_threshold) => setDraft({ near_dup_threshold })} />
          <div className="grid grid-cols-2 gap-3">
            <Field label="min chars"><NumberInput value={draft.min_chars} min={0} max={5000} step={10} onChange={(min_chars) => setDraft({ min_chars })} /></Field>
            <Field label="max chars"><NumberInput value={draft.max_chars} min={100} max={100000} step={500} onChange={(max_chars) => setDraft({ max_chars })} /></Field>
            <Field label="expected language"><Input value={draft.expected_language} onChange={(e) => setDraft({ expected_language: e.target.value })} /></Field>
            <Field label="embedding model"><Input value={draft.embedding_model} onChange={(e) => setDraft({ embedding_model: e.target.value })} /></Field>
          </div>
        </div>
      </Panel>
      {s && (
        <div className="grid grid-cols-3 gap-2">
          <StatTile size="sm" label="kept" value={s.kept} tone="acid" />
          <StatTile size="sm" label="removed" value={s.removed.length} tone="red" />
          <StatTile size="sm" label="refusals" value={s.refusals.length} tone="amber" />
        </div>
      )}
    </>
  ) : <Spinner />

  const results = summary.isLoading ? <Spinner /> : !s ? null : s.removed.length + s.refusals.length === 0 ? (
    <EmptyState title="Nothing filtered yet" kana="未濾過" body="Run the filters to compute removals. Every removal keeps a reason and can be restored." />
  ) : (
    <div className="flex flex-col gap-3">
      <div className="flex items-center justify-between gap-3">
        <Tabs size="sm" value={tab} onChange={(t) => { setTab(t); setSel(new Set()) }} tabs={[{ key: 'removed', label: 'Removed', count: s.removed.length }, { key: 'refusals', label: 'Refusals bucket', count: s.refusals.length }]} className="flex-1" />
        {sel.size > 0 && <Button size="sm" variant="primary" icon="refresh" loading={restore.isPending} onClick={() => restore.mutate([...sel], { onSuccess: () => setSel(new Set()) })}>Restore {sel.size}</Button>}
      </div>
      {tab === 'refusals' && <div className="font-mono text-[10.5px] text-amber/80">Refusals are kept, not deleted: useful as negative examples, or restore any the detector got wrong.</div>}
      <MonoTable rows={list} rowKey={(r) => r.metadata.id} selectable selected={sel} onSelectedChange={setSel} maxHeight="60vh"
        columns={[
          { key: 'id', header: 'id', render: (r) => <IdCell id={r.metadata.id} />, sortValue: (r) => r.metadata.id },
          { key: 'reason', header: 'reason', render: (r) => { const rule = r.status === 'refusal' ? 'refusal' : (r.filter_reason ?? 'filtered').split(':')[0]; const detail = r.status === 'refusal' ? 'short-answer heuristic' : (r.filter_reason ?? '').split(':').slice(1).join(':').trim(); return <span className="flex items-center gap-2"><Chip tone={toneFor(rule)}>{rule}</Chip><span className="text-muted">{truncate(detail, 46)}</span></span> }, sortValue: (r) => r.filter_reason ?? r.status },
          { key: 'excerpt', header: 'excerpt', render: (r) => <span className="text-text/75">{truncate((r.messages.find((m) => m.role === 'assistant')?.content ?? '').replace(/\*\*/g, ''), 48)}</span> },
          { key: 'act', header: '', align: 'right', render: (r) => <Button size="sm" variant="outline" icon="refresh" onClick={() => restore.mutate([r.metadata.id])}>Restore</Button> },
        ]} />
    </div>
  )

  return (
    <StageScreen stage={6} params={{ ...(draft ?? {}) }} config={config} results={results} resultsCount={(s?.removed.length ?? 0) + (s?.refusals.length ?? 0)} blocked={blocked} loading={loading} error={projectError ?? summary.error} onRetry={() => { void refetch(); void summary.refetch() }}
      runLabel="Run filters" extraActions={draft && <Button variant="outline" icon="filter" loading={runFilter.isPending} onClick={() => runFilter.mutate(draft)}>Recount</Button>}
      idleHint="filters run synchronously except near-dup embeddings"
      subtitle="Pure rule functions over rows. Every removal records a reason; refusals go to a bucket, never the bin. Restore is one click." />
  )
}
