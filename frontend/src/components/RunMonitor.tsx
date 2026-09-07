import clsx from 'clsx'
import { useEffect, useState, type ReactNode } from 'react'
import type { RunEvent, RunStatus } from '../lib/types'
import type { RunSnapshot } from '../lib/viewtypes'
import { data } from '../lib/data'
import { useCancelRun, useRunLog } from '../lib/queries'
import { fmtTime, pct, usd } from '../lib/format'
import { Panel } from './Panel'
import { Tabs } from './Tabs'
import { StatTile } from './StatTile'
import { Button } from './Button'
import { Chip, toneFor } from './Chip'
import { Icon } from './Icon'

const TERMINAL: RunStatus[] = ['done', 'failed', 'cancelled', 'budget_stop']

export function useRunMonitor(runId: string | null): RunSnapshot | null {
  const [snap, setSnap] = useState<RunSnapshot | null>(null)
  useEffect(() => {
    if (!runId) { setSnap(null); return }
    const init: RunSnapshot = { runId, status: 'running', done: 0, total: 0, rows_per_min: 0, refusals: 0, errors: 0, spend_usd: 0, cap_usd: 0, workers: {}, log: [] }
    setSnap(init)
    const unsub = data.subscribeRun(runId, (ev: RunEvent) => {
      setSnap((s) => {
        if (!s) return s
        switch (ev.type) {
          case 'progress': return { ...s, done: ev.done, total: ev.total, rows_per_min: ev.rows_per_min, refusals: ev.refusals, errors: ev.errors, spend_usd: ev.spend_usd, cap_usd: ev.cap_usd }
          case 'worker': return { ...s, workers: { ...s.workers, [ev.worker_id]: { status: ev.status, target_id: ev.target_id, model: ev.model } } }
          case 'log': return { ...s, log: [...s.log.slice(-299), { level: ev.level, ts: ev.ts, msg: ev.msg }] }
          case 'item': return s
          case 'done': return { ...s, status: ev.status }
        }
      })
    })
    return unsub
  }, [runId])
  return snap
}

interface Props { runId: string | null; results: ReactNode; resultsCount?: number; onFinished?: (status: RunStatus) => void; className?: string; idleHint?: string }

export function RunMonitor({ runId, results, resultsCount, onFinished, className, idleHint }: Props) {
  const snap = useRunMonitor(runId)
  const [tab, setTab] = useState<'results' | 'log'>('results')
  const live = !!snap && !TERMINAL.includes(snap.status)
  const log = useRunLog(runId, live)
  const cancel = useCancelRun()
  useEffect(() => { if (snap && TERMINAL.includes(snap.status)) onFinished?.(snap.status) }, [snap?.status]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { if (runId) setTab('results') }, [runId])

  const p = snap && snap.total > 0 ? (snap.done / snap.total) * 100 : 0
  const refPct = snap && snap.done > 0 ? (snap.refusals / snap.done) * 100 : 0
  const workers = snap ? Object.entries(snap.workers).sort((a, b) => Number(a[0]) - Number(b[0])) : []

  return (
    <Panel
      title="Run monitor" kana="実行" className={className} padded={false}
      actions={snap && (
        <>
          <Chip tone={toneFor(live ? 'running' : snap.status)}>{live && <span className="w-1.5 h-1.5 rounded-full bg-acid pulse-dot" />}{snap.status}</Chip>
          <span className="font-mono text-[10px] text-dim">{snap.runId}</span>
          {live && <Button size="sm" variant="danger" icon="stop" onClick={() => cancel.mutate(snap.runId)} loading={cancel.isPending}>Cancel</Button>}
        </>
      )}
    >
      {snap ? (
        <div className="p-4 flex flex-col gap-3 border-b border-line">
          <div className="grid grid-cols-4 gap-2">
            <StatTile size="sm" label="done / total" value={`${snap.done}`} unit={`/ ${snap.total}`} tone="cyan" />
            <StatTile size="sm" label="rows / min" value={snap.rows_per_min.toFixed(1)} tone="acid" />
            <StatTile size="sm" label="refusal rate" value={pct(refPct, 1)} tone={refPct > 10 ? 'amber' : 'default'} />
            <StatTile size="sm" label="errors" value={snap.errors} tone={snap.errors > 0 ? 'red' : 'default'} hint={`spend ${usd(snap.spend_usd)}`} />
          </div>
          <div className="h-1.5 rounded-full bg-bg/70 border border-line overflow-hidden">
            <div className={clsx('h-full transition-all duration-300', live ? 'bg-acid shadow-[0_0_10px_rgba(182,255,46,.6)]' : snap.status === 'done' ? 'bg-cyan' : 'bg-red')} style={{ width: `${p}%` }} />
          </div>
          <div className="flex items-center gap-2 flex-wrap">
            <span className="label">workers</span>
            {workers.length === 0 && <span className="font-mono text-[10px] text-dim">waiting for pool</span>}
            {workers.map(([id, w]) => (
              <span
                key={id} title={`w${id} · ${w.status}${w.target_id ? ` · ${w.target_id}` : ''}${w.model ? ` · ${w.model}` : ''}`}
                className={clsx('h-4 min-w-[26px] px-1 rounded-chip border font-mono text-[9px] flex items-center justify-center',
                  w.status === 'calling' && 'border-acid/60 text-acid worker-calling',
                  w.status === 'idle' && 'border-line2 text-dim',
                  w.status === 'done' && 'border-cyan/50 text-cyan',
                  w.status === 'error' && 'border-red/60 text-red')}
              >w{id}</span>
            ))}
          </div>
        </div>
      ) : idleHint ? <div className="px-4 py-2.5 border-b border-line font-mono text-[11px] text-dim flex items-center gap-2"><Icon name="terminal" size={12} />{idleHint}</div> : null}
      <Tabs size="sm" className="px-3" value={tab} onChange={setTab} tabs={[{ key: 'results', label: 'Results', count: resultsCount }, { key: 'log', label: 'Raw log', count: snap ? snap.log.length : undefined }]} />
      <div className="p-3 min-h-[160px]">
        {tab === 'results' ? results : (
          <div className="flex flex-col gap-3">
            <div className="rounded-btn border border-line bg-bg/60 max-h-[220px] overflow-y-auto p-2 font-mono text-[11px] leading-[1.5]">
              {(snap?.log ?? []).length === 0 && <div className="text-dim">No events yet.</div>}
              {(snap?.log ?? []).map((l, i) => (
                <div key={i} className="flex gap-2">
                  <span className="text-dim shrink-0">{fmtTime(l.ts)}</span>
                  <span className={clsx('shrink-0 w-10 uppercase', l.level === 'error' ? 'text-red' : l.level === 'warn' ? 'text-amber' : l.level === 'debug' ? 'text-dim' : 'text-cyan')}>{l.level}</span>
                  <span className="text-text/85">{l.msg}</span>
                </div>
              ))}
            </div>
            {log.data && log.data.items.length > 0 && (
              <div className="rounded-btn border border-line bg-bg/60 max-h-[200px] overflow-auto">
                <table className="w-full font-mono text-[11px]">
                  <thead className="sticky top-0 bg-surface2"><tr className="label !text-[9.5px]"><th className="px-2 py-1 text-left">ts</th><th className="px-2 py-1 text-left">model</th><th className="px-2 py-1 text-left">target</th><th className="px-2 py-1 text-right">in/out</th><th className="px-2 py-1 text-right">ms</th><th className="px-2 py-1 text-right">$</th><th className="px-2 py-1">status</th></tr></thead>
                  <tbody>
                    {log.data.items.slice(0, 60).map((c) => (
                      <tr key={c.id} className="border-t border-line/60">
                        <td className="px-2 py-[3px] text-dim">{fmtTime(c.ts)}</td><td className="px-2 py-[3px] text-muted truncate max-w-[160px]">{c.model}</td><td className="px-2 py-[3px] truncate max-w-[200px]">{c.target_id}</td>
                        <td className="px-2 py-[3px] text-right tabular-nums">{c.tokens_in}/{c.tokens_out}</td><td className="px-2 py-[3px] text-right tabular-nums">{c.latency_ms}</td><td className="px-2 py-[3px] text-right tabular-nums text-cyan">{c.cost_usd.toFixed(4)}</td>
                        <td className="px-2 py-[3px] text-center"><Chip tone={toneFor(c.status)}>{c.status}</Chip></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        )}
      </div>
    </Panel>
  )
}
