import clsx from 'clsx'
import { useState, type ReactNode } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { Header } from '../../app/Header'
import { runKey, useStore } from '../../app/store'
import { STAGES, type StageNumber } from '../../lib/types'
import { useEstimate, useRunStage, useSummary } from '../../lib/queries'
import { pad2 } from '../../lib/format'
import { Button, EmptyState, ErrorState, EstimateModal, RunMonitor, Spinner } from '../../components'
import { ApiError } from '../../lib/api'
import type { RunStatus } from '../../lib/types'
import { useQueryClient } from '@tanstack/react-query'

interface Props {
  stage: StageNumber
  config: ReactNode
  results: ReactNode
  resultsCount?: number
  /** Params sent to estimate/run. */
  params: Record<string, unknown>
  /** Stage-specific run button label; omit RUN button entirely with `noRun`. */
  runLabel?: string
  noRun?: boolean
  extraActions?: ReactNode
  /** When the stage's inputs are missing (e.g. no taxonomy yet). */
  blocked?: { title: string; body: ReactNode; stage?: StageNumber } | null
  loading?: boolean
  error?: unknown
  onRetry?: () => void
  subtitle?: ReactNode
  configWidth?: number
  idleHint?: string
}

/** Two-column stage screen: header + estimate→run flow + config (left) + RunMonitor with results (right). */
/** Turn a failed POST /stages/{n}/run into a user-facing message + action. */
export function describeRunError(e: unknown): { status: number; message: string; kind: 'nokey' | 'overcap' | 'nothing' | 'other' } | null {
  if (!e) return null
  const status = e instanceof ApiError ? e.status : 0
  const d = e instanceof ApiError ? e.detail : null
  const message = typeof d === 'string' ? d : d && typeof d === 'object' && 'message' in d ? String((d as { message: unknown }).message) : e instanceof Error ? e.message : 'Run failed to start'
  const low = message.toLowerCase()
  const kind = status === 409 ? 'overcap' : low.includes('key') ? 'nokey' : low.includes('nothing') ? 'nothing' : 'other'
  return { status, message, kind }
}

export function StageScreen({ stage, config, results, resultsCount, params, runLabel = 'Run stage', noRun, extraActions, blocked, loading, error, onRetry, subtitle, configWidth = 400, idleHint }: Props) {
  const { projectId } = useParams()
  const meta = STAGES[stage - 1]
  const summary = useSummary(projectId)
  const navigate = useNavigate()
  const qc = useQueryClient()
  const activeRuns = useStore((s) => s.activeRuns)
  const setActiveRun = useStore((s) => s.setActiveRun)
  const runId = projectId ? activeRuns[runKey(projectId, stage)] ?? null : null
  const estimate = useEstimate(projectId, stage)
  const run = useRunStage(projectId, stage)
  const [estOpen, setEstOpen] = useState(false)

  const openEstimate = () => { setEstOpen(true); estimate.mutate(params) }
  const confirmRun = (force = false) => {
    run.mutate(force ? { ...params, force: true } : params, {
      onSuccess: ({ run_id }) => { if (projectId) setActiveRun(projectId, stage, run_id); setEstOpen(false) },
    })
  }
  const onFinished = (_status: RunStatus) => {
    if (projectId) void qc.invalidateQueries({ predicate: (q) => Array.isArray(q.queryKey) && q.queryKey[1] === projectId })
  }

  const header = (
    <Header
      kicker={`STAGE ${pad2(stage)} · ${meta.kicker}`} kana={meta.kana} title={meta.title} numeral={pad2(stage)} subtitle={subtitle}
      actions={(
        <>
          {extraActions}
          {!noRun && <Button variant="primary" size="lg" icon="play" onClick={openEstimate} disabled={!projectId || !!blocked || loading} loading={run.isPending} data-testid="run-stage">{runLabel}</Button>}
        </>
      )}
    />
  )

  if (!projectId) {
    return (
      <>
        {header}
        <EmptyState icon="folder" title="No project selected" kana="未選択" body="Open a project from the Projects list to configure and run this stage." action={{ label: 'Go to projects', onClick: () => navigate('/'), icon: 'folder' }} />
      </>
    )
  }
  if (error) return <>{header}<ErrorState error={error} onRetry={onRetry} /></>
  if (summary.error) return <>{header}<ErrorState title="Project failed to load" error={summary.error} onRetry={() => summary.refetch()} /></>

  return (
    <>
      {header}
      {loading && !blocked ? <Spinner className="mb-4" /> : null}
      <div className="grid gap-5 items-start" style={{ gridTemplateColumns: `${configWidth}px minmax(0,1fr)` }}>
        <div className={clsx('flex flex-col gap-4 min-w-0', loading && 'opacity-60 pointer-events-none')}>{config}</div>
        <div className="flex flex-col gap-4 min-w-0">
          {blocked ? (
            <EmptyState icon="warning" title={blocked.title} body={blocked.body} action={blocked.stage ? { label: `Go to stage ${pad2(blocked.stage)}`, onClick: () => navigate(`/p/${projectId}/${blocked.stage}`), icon: 'arrowRight' } : undefined} />
          ) : (
            <RunMonitor runId={runId} results={results} resultsCount={resultsCount} onFinished={onFinished} idleHint={idleHint ?? `no live run · showing stored results for stage ${pad2(stage)}`} />
          )}
        </div>
      </div>
      <EstimateModal
        open={estOpen} onClose={() => setEstOpen(false)} onConfirm={confirmRun} estimate={estimate.data} loading={estimate.isPending} error={estimate.error}
        spend={summary.data?.spend_usd ?? 0} cap={summary.data?.cap_usd ?? 15} stageTitle={`${pad2(stage)} ${meta.title}`} running={run.isPending}
        runError={describeRunError(run.error)} onForce={() => confirmRun(true)} onSettings={() => navigate('/settings')}
      />
    </>
  )
}
