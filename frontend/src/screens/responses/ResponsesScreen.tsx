import { useState } from 'react'
import { useParams } from 'react-router-dom'
import type { RowItem } from '../../lib/viewtypes'
import { usePrompts, useRefusalMatrix, useRows } from '../../lib/queries'
import { StageScreen } from '../shared/StageScreen'
import { useConfigSection } from '../shared/useConfigSection'
import { Chip, EmptyState, IdCell, Modal, MonoTable, TextCell, Panel, Spinner, StatTile, toneFor } from '../../components'
import { EnsembleEditor, MultiTurnPanel, SystemPromptPanel } from './ResponsesConfig'
import { RefusalHeatmap } from './RefusalHeatmap'
import { Conversation } from '../shared/Conversation'

export function ResponsesScreen() {
  const { projectId } = useParams()
  const { draft, setDraft, loading, projectError, refetch } = useConfigSection(projectId, 'responses')
  const prompts = usePrompts(projectId, {})
  const rows = useRows(projectId, { page_size: 500 })
  const matrix = useRefusalMatrix(projectId)
  const [open, setOpen] = useState<RowItem | null>(null)
  const items = rows.data?.items ?? []
  const refusals = items.filter((r) => r.status === 'refusal').length
  const multiTurn = items.filter((r) => r.messages.filter((m) => m.role === 'assistant').length > 1).length
  const blocked = prompts.data && prompts.data.total === 0 ? { title: 'No prompts yet', body: 'The teacher answers prompts. Generate prompts in stage 02 first.', stage: 2 as const } : null

  const config = draft ? (
    <>
      <Panel title="Teacher ensemble" kana="教師"><EnsembleEditor cfg={draft} onChange={setDraft} /></Panel>
      <SystemPromptPanel cfg={draft} onChange={setDraft} />
      <MultiTurnPanel cfg={draft} onChange={setDraft} />
    </>
  ) : <Spinner />

  const results = rows.isLoading ? <Spinner /> : items.length === 0 ? (
    <EmptyState title="No responses yet" kana="未生成" body="Run stage 03 to have the teacher ensemble answer every prompt. Refusals are detected and bucketed, never deleted." />
  ) : (
    <div className="flex flex-col gap-3">
      <div className="grid grid-cols-4 gap-2">
        <StatTile size="sm" label="responses" value={items.length} tone="cyan" />
        <StatTile size="sm" label="refusals" value={refusals} tone={refusals > 0 ? 'amber' : 'default'} hint={`${((refusals / items.length) * 100).toFixed(1)}%`} />
        <StatTile size="sm" label="multi-turn" value={multiTurn} hint={`${((multiTurn / items.length) * 100).toFixed(0)}% of rows`} />
        <StatTile size="sm" label="models" value={new Set(items.map((r) => r.metadata.models.responses)).size} />
      </div>
      <MonoTable
        rows={items} rowKey={(r) => r.metadata.id} onRowClick={setOpen} maxHeight="380px" activeKey={open?.metadata.id}
        rowTone={(r) => (r.status === 'refusal' ? 'amber' : 'default')}
        columns={[
          { key: 'id', header: 'id', render: (r) => <IdCell id={r.metadata.id} />, sortValue: (r) => r.metadata.id },
          { key: 'model', header: 'teacher', render: (r) => <span className="text-muted">{r.metadata.models.responses.split('/')[1]}</span>, sortValue: (r) => r.metadata.models.responses },
          { key: 'turns', header: 'turns', align: 'right', render: (r) => r.messages.filter((m) => m.role === 'assistant').length, sortValue: (r) => r.messages.length },
          { key: 'excerpt', header: 'assistant excerpt', render: (r) => <TextCell max={240} className="text-text/80" text={(r.messages.find((m) => m.role === 'assistant')?.content ?? '').replace(/\*\*/g, '').replace(/\n+/g, ' ')} /> },
          { key: 'status', header: 'status', render: (r) => <Chip tone={toneFor(r.status)}>{r.status}</Chip>, sortValue: (r) => r.status },
        ]}
      />
      <Panel title="Refusal rate · model × topic" kana="拒否" padded={false} bodyClassName="p-3">
        {matrix.data ? <RefusalHeatmap cells={matrix.data} /> : <Spinner />}
      </Panel>
      <Modal open={!!open} onClose={() => setOpen(null)} title={open?.metadata.id ?? ''} kana="会話" width="lg">
        {open && <Conversation messages={open.messages} />}
      </Modal>
    </div>
  )

  return (
    <StageScreen stage={3} params={{ ...(draft ?? {}) }} config={config} results={results} resultsCount={items.length} blocked={blocked} loading={loading} error={projectError ?? rows.error} onRetry={() => { void refetch(); void rows.refetch() }}
      subtitle="Each prompt is answered by a teacher picked from the ensemble. Optional simulated-user follow-ups and <think> reasoning tags." />
  )
}
