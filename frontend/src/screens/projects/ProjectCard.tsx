import clsx from 'clsx'
import { useNavigate } from 'react-router-dom'
import type { Project } from '../../lib/types'
import { useSummary } from '../../lib/queries'
import { num, relTime, usd } from '../../lib/format'
import { Chip, Icon, IconButton } from '../../components'
import { STAGES } from '../../lib/types'

export function ProjectCard({ project, onDelete }: { project: Project; onDelete: () => void }) {
  const nav = useNavigate()
  const summary = useSummary(project.id)
  const s = summary.data
  const done = s?.stages.filter((x) => x.status === 'done').length ?? 0
  const next = s?.stages.find((x) => x.status !== 'done')?.stage ?? 8
  const spendPct = s ? Math.min(100, (s.spend_usd / s.cap_usd) * 100) : 0
  const rowsPct = s && s.target_rows > 0 ? Math.min(100, (s.rows / s.target_rows) * 100) : 0
  const tone = spendPct >= 90 ? 'bg-red' : spendPct >= 70 ? 'bg-amber' : 'bg-ok'
  return (
    <article
      role="link" tabIndex={0} onClick={() => nav(`/p/${project.id}/${next}`)} onKeyDown={(e) => { if (e.key === 'Enter') nav(`/p/${project.id}/${next}`) }}
      className="panel group relative p-4 flex flex-col gap-3 cursor-pointer transition-all hover:border-green/50 hover:shadow-[0_0_28px_rgba(34,227,90,.10)] focus-ring"
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="font-display font-bold uppercase text-[16px] tracking-[.03em] leading-tight truncate">{project.name}</h3>
          <div className="font-mono text-[10.5px] text-dim mt-0.5 truncate">{project.slug} · {relTime(project.updated_at)}</div>
        </div>
        <div className="flex items-center gap-1 shrink-0" onClick={(e) => e.stopPropagation()}>
          <IconButton icon="trash" label="Delete project" size="sm" variant="ghost" className="opacity-0 group-hover:opacity-100 text-muted hover:text-red" onClick={onDelete} />
        </div>
      </div>
      <p className="text-[13px] text-muted leading-snug line-clamp-2 min-h-[34px]">{project.domain_brief}</p>
      <div className="flex flex-wrap gap-1">
        {project.data_types.map((d) => <Chip key={d} tone="green">{d}</Chip>)}
        {project.preset && <Chip tone="dim">{project.preset}</Chip>}
      </div>
      {/* Stage status dots */}
      <div className="flex items-center gap-1.5" aria-label="Stage status">
        {STAGES.map((st) => {
          const status: string = s?.stages.find((x) => x.stage === st.n)?.status ?? 'todo'
          return (
            <span key={st.n} title={`${st.title}: ${status}`} className={clsx('h-1.5 flex-1 rounded-full', status === 'done' && 'bg-green shadow-[0_0_6px_rgba(34,227,90,.6)]', status === 'running' && 'bg-ok pulse-dot', status === 'paused' && 'bg-amber shadow-[0_0_6px_rgba(255,160,64,.6)] ring-1 ring-amber/50', status === 'failed' && 'bg-red', status === 'todo' && 'bg-line2')} />
          )
        })}
        <span className="font-mono text-[10px] text-muted ml-1 tabular-nums">{done}/8</span>
      </div>
      <div className="grid grid-cols-3 gap-2 font-mono text-[11px]">
        <div><div className="label !text-[9px]">rows</div><div className="text-green text-[15px] tabular-nums">{s ? num(s.rows) : '—'}<span className="text-dim text-[10px]"> / {s ? num(s.target_rows) : '—'}</span></div></div>
        <div><div className="label !text-[9px]">accepted</div><div className="text-ok text-[15px] tabular-nums">{s ? num(s.accepted) : '—'}</div></div>
        <div><div className="label !text-[9px]">spend / cap</div><div className="text-[15px] tabular-nums">{usd(project.spend_usd)}<span className="text-dim text-[10px]"> / {usd(project.budget_cap_usd)}</span></div></div>
      </div>
      <div className="flex flex-col gap-1">
        <div className="h-1 rounded-full bg-bg/70 border border-line overflow-hidden"><div className="h-full bg-green" style={{ width: `${rowsPct}%` }} /></div>
        <div className="h-1 rounded-full bg-bg/70 border border-line overflow-hidden"><div className={clsx('h-full', tone)} style={{ width: `${spendPct}%` }} /></div>
      </div>
      <div className="flex items-center justify-between font-mono text-[10px] text-dim">
        <span>next: stage {String(next).padStart(2, '0')} {STAGES[next - 1].title}</span>
        <span className="flex items-center gap-1 text-green opacity-0 group-hover:opacity-100 transition-opacity">open <Icon name="arrowRight" size={11} /></span>
      </div>
    </article>
  )
}
