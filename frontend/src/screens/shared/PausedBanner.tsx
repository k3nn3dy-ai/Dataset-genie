import { useQueryClient } from '@tanstack/react-query'
import { useCancelRun, useResumeRun, useRun } from '../../lib/queries'
import { runKey, useStore } from '../../app/store'
import { Banner, Button } from '../../components'

interface Props { projectId: string; stage: number; runId: string }

/** Shown when the stage's latest run is paused (interrupted). Resume re-subscribes the RunMonitor to the same run. */
export function PausedBanner({ projectId, stage, runId }: Props) {
  const run = useRun(runId)
  const resume = useResumeRun()
  const cancel = useCancelRun()
  const qc = useQueryClient()
  const setActiveRun = useStore((s) => s.setActiveRun)
  const activeRuns = useStore((s) => s.activeRuns)
  if (activeRuns[runKey(projectId, stage)] === runId) return null // already live in the monitor
  const invalidate = () => void qc.invalidateQueries({ predicate: (q) => Array.isArray(q.queryKey) && (q.queryKey[1] === projectId || q.queryKey[0] === 'projects') })
  const done = run.data?.done ?? 0
  const total = run.data?.total ?? 0
  return (
    <Banner tone="amber" icon="pause" className="items-center">
      <div className="flex items-center gap-3 flex-wrap">
        <span>Run paused (interrupted) — <span className="font-mono text-amber">{done}/{total}</span> done{run.data?.error_message ? <span className="text-muted"> · {run.data.error_message}</span> : null}</span>
        <span className="flex-1" />
        <Button size="sm" variant="primary" icon="play" loading={resume.isPending} data-testid="resume-run"
          onClick={() => resume.mutate(runId, { onSuccess: () => { setActiveRun(projectId, stage, runId); invalidate() } })}>Resume</Button>
        <Button size="sm" variant="danger" icon="stop" loading={cancel.isPending}
          onClick={() => cancel.mutate(runId, { onSuccess: invalidate })}>Cancel</Button>
      </div>
    </Banner>
  )
}
