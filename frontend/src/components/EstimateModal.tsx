import type { Estimate } from '../lib/types'
import { num, usd } from '../lib/format'
import { Modal } from './Modal'
import { Button } from './Button'
import { StatTile } from './StatTile'
import { Banner, Spinner } from './States'

interface Props {
  open: boolean
  onClose: () => void
  onConfirm: () => void
  estimate: Estimate | null | undefined
  loading: boolean
  error?: unknown
  spend: number
  cap: number
  stageTitle: string
  running?: boolean
}

export function EstimateModal({ open, onClose, onConfirm, estimate, loading, error, spend, cap, stageTitle, running }: Props) {
  const after = spend + (estimate?.est_usd ?? 0)
  const pctAfter = cap > 0 ? (after / cap) * 100 : 0
  const over = estimate?.over_cap || after > cap
  return (
    <Modal open={open} onClose={onClose} title={`Estimate · ${stageTitle}`} kana="見積" width="md" tone={over ? 'amber' : 'default'}
      footer={(
        <>
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button variant="primary" icon="play" disabled={!estimate || over || loading} loading={running} onClick={onConfirm} data-testid="confirm-run">Run stage</Button>
        </>
      )}
    >
      {loading && <Spinner label="Estimating cost" />}
      {!loading && error !== undefined && error !== null && <Banner tone="red">Estimate failed: {error instanceof Error ? error.message : String(error)}</Banner>}
      {estimate && !loading && (
        <div className="flex flex-col gap-4">
          <div className="grid grid-cols-3 gap-2">
            <StatTile label="estimated cost" value={usd(estimate.est_usd)} tone={over ? 'amber' : 'cyan'} />
            <StatTile label="model calls" value={num(estimate.calls)} />
            <StatTile label="tokens in / out" value={`${Math.round(estimate.est_tokens_in / 1000)}k`} unit={`/ ${Math.round(estimate.est_tokens_out / 1000)}k`} size="sm" />
          </div>
          <div className="flex flex-col gap-1.5">
            <div className="flex justify-between label"><span>budget after run</span><span className={over ? 'text-amber' : 'text-text'}>{usd(after)} / {usd(cap)}</span></div>
            <div className="h-2 rounded-full bg-bg/70 border border-line overflow-hidden relative">
              <div className="absolute inset-y-0 left-0 bg-dim/60" style={{ width: `${Math.min(100, (spend / cap) * 100)}%` }} />
              <div className={over ? 'absolute inset-y-0 bg-amber' : 'absolute inset-y-0 bg-cyan'} style={{ left: `${Math.min(100, (spend / cap) * 100)}%`, width: `${Math.min(100 - (spend / cap) * 100, ((estimate.est_usd) / cap) * 100)}%` }} />
            </div>
            <div className="font-mono text-[10.5px] text-dim">current {usd(spend)} · est +{usd(estimate.est_usd)} · {pctAfter.toFixed(0)}% of cap</div>
          </div>
          {over
            ? <Banner tone="amber">This run would exceed the project cap. Raise the cap in Settings or reduce scope. The server enforces the cap regardless.</Banner>
            : <Banner tone="cyan" icon="lock">Cap is enforced server-side; the run auto-stops at {Math.round(90)}% of the cap. Actual usage is billed from provider usage, not this estimate.</Banner>}
        </div>
      )}
    </Modal>
  )
}
