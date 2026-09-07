import clsx from 'clsx'
import { NavLink, useParams } from 'react-router-dom'
import { KANA, STAGES } from '../lib/types'
import { useSummary } from '../lib/queries'
import { useStore } from './store'
import { Icon } from '../components/Icon'
import { BudgetBar } from './BudgetBar'
import { pad2 } from '../lib/format'
import type { StageState } from '../lib/viewtypes'

export function Rail() {
  const { projectId } = useParams()
  const summary = useSummary(projectId)
  const mock = useStore((s) => s.mock)
  const project = summary.data?.project
  const stageStatus = (n: number): StageState => (summary.data?.stages.find((s) => s.stage === n)?.status as StageState | undefined) ?? 'todo'

  return (
    <aside className="w-[236px] shrink-0 sticky top-0 h-screen flex flex-col border-r border-line bg-surface1 backdrop-blur-md z-20">
      {/* Wordmark */}
      <NavLink to="/" className="flex items-center gap-3 px-4 h-[68px] border-b border-line">
        <span className="w-9 h-9 rounded-[9px] border border-cyan flex items-center justify-center text-cyan shadow-glow bg-bg/60 shrink-0"><Icon name="sparkle" size={18} /></span>
        <span className="flex flex-col leading-none min-w-0">
          <span className="font-display font-bold uppercase text-[14px] tracking-[.06em] text-text">Dataset Genie</span>
          <span className="font-mono text-[9.5px] text-cyan/80 tracking-[.12em] mt-1">{KANA.wordmark}</span>
        </span>
      </NavLink>

      <nav className="flex-1 overflow-y-auto py-3 flex flex-col">
        <RailLink to="/" icon="folder" label="Projects" kana={KANA.projects} end />

        <div className="px-4 mt-4 mb-1.5 flex items-baseline justify-between">
          <span className="label">pipeline</span>
          {mock && <span className="font-mono text-[9px] text-magenta border border-magenta/50 rounded-chip px-1 leading-[14px]" title="Mock data (no backend)">MOCK</span>}
        </div>
        <div className="px-4 mb-2 font-ui font-semibold text-[13.5px] text-text truncate">{project?.name ?? <span className="text-dim">No project selected</span>}</div>

        <ol className="flex flex-col">
          {STAGES.map((s) => {
            const st = stageStatus(s.n)
            const disabled = !projectId
            return (
              <li key={s.n}>
                <NavLink
                  to={projectId ? `/p/${projectId}/${s.n}` : '/'}
                  aria-disabled={disabled}
                  className={({ isActive }) => clsx(
                    'relative flex items-center gap-3 h-[38px] pl-4 pr-3 transition-colors group',
                    disabled && 'opacity-50 pointer-events-none',
                    isActive ? 'stage-active text-text' : 'text-muted hover:text-text hover:bg-surface2/60',
                  )}
                >
                  {({ isActive }) => (
                    <>
                      {isActive && <span className="absolute left-0 top-1.5 bottom-1.5 w-[3px] rounded-r bg-cyan shadow-glow" />}
                      <StageBadge n={s.n} state={isActive ? 'active' : st === 'done' || st === 'running' || st === 'paused' || st === 'failed' ? st : 'todo'} />
                      <span className={clsx('font-ui font-semibold text-[14px] flex-1 truncate', isActive && 'text-cyan')}>{s.title}</span>
                      <span className={clsx('font-mono text-[10px]', isActive ? 'text-cyan/80' : 'text-dim')}>{s.kana}</span>
                    </>
                  )}
                </NavLink>
              </li>
            )
          })}
        </ol>

        <div className="mt-auto pt-3 border-t border-line mx-0">
          <RailLink to="/settings" icon="settings" label="Settings" kana={KANA.settings} />
        </div>
      </nav>

      <div className="px-4 py-3 border-t border-line">
        <BudgetBar spend={summary.data?.spend_usd ?? project?.spend_usd ?? 0} cap={summary.data?.cap_usd ?? project?.budget_cap_usd ?? 15} stopAt={project?.stop_at_pct ?? 90} />
      </div>
    </aside>
  )
}

function RailLink({ to, icon, label, kana, end }: { to: string; icon: 'folder' | 'settings'; label: string; kana: string; end?: boolean }) {
  return (
    <NavLink to={to} end={end} className={({ isActive }) => clsx('relative flex items-center gap-3 h-[38px] pl-4 pr-3 transition-colors', isActive ? 'stage-active text-cyan' : 'text-muted hover:text-text hover:bg-surface2/60')}>
      {({ isActive }) => (
        <>
          {isActive && <span className="absolute left-0 top-1.5 bottom-1.5 w-[3px] rounded-r bg-cyan shadow-glow" />}
          <span className={clsx('w-6 h-6 rounded-[6px] border flex items-center justify-center', isActive ? 'border-cyan/60 text-cyan' : 'border-line2')}><Icon name={icon} size={13} /></span>
          <span className="font-ui font-semibold text-[14px] flex-1">{label}</span>
          <span className={clsx('font-mono text-[10px]', isActive ? 'text-cyan/80' : 'text-dim')}>{kana}</span>
        </>
      )}
    </NavLink>
  )
}

function StageBadge({ n, state }: { n: number; state: 'done' | 'active' | 'todo' | 'running' | 'paused' | 'failed' }) {
  return (
    <span className={clsx(
      'w-6 h-6 rounded-[6px] flex items-center justify-center font-mono text-[11px] shrink-0 border',
      state === 'active' && 'bg-cyan text-bg border-cyan shadow-glow font-bold',
      state === 'done' && 'bg-bg/80 text-cyan border-line2',
      state === 'running' && 'bg-bg/80 text-acid border-acid/60',
      state === 'paused' && 'bg-bg/80 text-amber border-amber/70 shadow-[0_0_10px_rgba(255,176,32,.35)]',
      state === 'failed' && 'bg-bg/80 text-red border-red/60',
      state === 'todo' && 'bg-transparent text-dim border-line2',
    )}>
      {state === 'done' ? <Icon name="check" size={12} strokeWidth={2.4} /> : state === 'running' ? <span className="w-2 h-2 rounded-full bg-acid pulse-dot" /> : state === 'paused' ? <Icon name="pause" size={11} strokeWidth={2.6} title="Paused — resumable" /> : state === 'failed' ? <Icon name="warning" size={11} strokeWidth={2.2} /> : pad2(n)}
    </span>
  )
}
