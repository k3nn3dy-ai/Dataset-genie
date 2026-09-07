import { useQueryClient } from '@tanstack/react-query'
import { useCancelRun, useResumeRun, useRun } from '../../lib/queries'
import { runKey, useStore } from '../../app/store'
import { Banner, Button } from '../../components'
import type { RunWithPartial } from '../../lib/viewtypes'

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
  const partial = (run.data as RunWithPartial | undefined)?.partial ?? 0
  const go = (force: boolean) => resume.mutate({ runId, force }, { onSuccess: () => { setActiveRun(projectId, stage, runId); invalidate() } })
  return (
    <Banner tone="amber" icon="pause" className="items-center">
      <div className="flex items-center gap-3 flex-wrap">
        <span>Run paused (interrupted) — <span className="font-mono text-amber">{done}/{total}</span> done{partial > 0 && <span className="text-muted"> · <span className="font-mono text-amber">{partial}</span> partial item{partial === 1 ? '' : 's'} will be re-billed if forced</span>}{run.data?.error_message ? <span className="text-muted"> · {run.data.error_message}</span> : null}</span>
        <span className="flex-1" />
        <Button size="sm" variant="primary" icon="play" loading={resume.isPending && resume.variables?.force !== true} data-testid="resume-run" onClick={() => go(false)}>Resume</Button>
        {partial > 0 && <Button size="sm" variant="outline" icon="refresh" loading={resume.isPending && resume.variables?.force === true} data-testid="resume-run-force" title={`Re-run ${partial} partial item(s); their earlier calls are billed again`} onClick={() => go(true)}>Resume incl. partial</Button>}
        <Button size="sm" variant="danger" icon="stop" loading={cancel.isPending}
          onClick={() => cancel.mutate(runId, { onSuccess: invalidate })}>Cancel</Button>
      </div>
    </Banner>
  )
}
