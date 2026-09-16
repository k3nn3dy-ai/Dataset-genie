import clsx from 'clsx'
import { useEffect, useState } from 'react'
import { createPortal } from 'react-dom'
import { NavLink, useParams } from 'react-router-dom'
import { STAGES } from '../lib/types'
import { usePatchProject, useSummary } from '../lib/queries'
import { useStore } from './store'
import { Icon } from '../components/Icon'
import { GenieMark } from '../components/GenieMark'
import { Button } from '../components/Button'
import { Field, NumberInput } from '../components/Fields'
import { Modal } from '../components/Modal'
import { Slider } from '../components/Slider'
import { BudgetBar } from './BudgetBar'
import { pad2, usd } from '../lib/format'
import type { StageState } from '../lib/viewtypes'

export function Rail() {
  const { projectId } = useParams()
  const summary = useSummary(projectId)
  const mock = useStore((s) => s.mock)
  const project = summary.data?.project
  const stageStatus = (n: number): StageState => (summary.data?.stages.find((s) => s.stage === n)?.status as StageState | undefined) ?? 'todo'

  return (
    <aside className="w-[236px] shrink-0 sticky top-0 h-screen flex flex-col border-r border-line bg-surface1 z-20">
      {/* Wordmark */}
      <NavLink to="/" className="flex items-center gap-3 px-4 h-[64px] border-b border-line">
        <span className="w-8 h-8 rounded-[9px] bg-green/10 text-green flex items-center justify-center shrink-0"><GenieMark size={24} /></span>
        <span className="flex flex-col leading-none min-w-0">
          <span className="font-display font-semibold text-[13.5px] tracking-[-0.01em] text-text">Dataset Genie</span>
        </span>
      </NavLink>

      <nav className="flex-1 overflow-y-auto py-3 flex flex-col">
        <RailLink to="/" icon="folder" label="Projects" end />

        <div className="px-4 mt-4 mb-1.5 flex items-baseline justify-between">
          <span className="label">pipeline</span>
          {mock && <span className="font-ui text-[9px] font-semibold text-muted bg-surface2 rounded-chip px-1.5 leading-[16px]" title="Mock data (no backend)">Mock</span>}
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
                    'relative flex items-center gap-3 h-[36px] mx-2 px-2.5 rounded-[8px] transition-colors group',
                    disabled && 'opacity-50 pointer-events-none',
                    isActive && !disabled ? 'bg-surface2 text-text' : 'text-muted hover:text-text hover:bg-surface2/70',
                  )}
                >
                  {({ isActive }) => (
                    <>
                      <StageBadge n={s.n} state={isActive && !disabled ? 'active' : st === 'done' || st === 'running' || st === 'paused' || st === 'failed' ? st : 'todo'} />
                      <span className={clsx('font-ui font-medium text-[13.5px] flex-1 truncate', isActive && !disabled && 'text-green')}>{s.title}</span>
                    </>
                  )}
                </NavLink>
              </li>
            )
          })}
        </ol>

        <div className="mt-auto pt-3 border-t border-line mx-0">
          <RailExternal href={GUIDE_URL} icon="external" label="Guide" />
          <RailLink to="/settings" icon="settings" label="Settings" />
        </div>
      </nav>

      <div className="px-4 py-3 border-t border-line">
        {project ? (
          <BudgetEditor projectId={project.id} spend={summary.data?.spend_usd ?? project.spend_usd ?? 0}
            cap={summary.data?.cap_usd ?? project.budget_cap_usd ?? 15} stopAt={project.stop_at_pct ?? 90} />
        ) : (
          <BudgetBar spend={0} cap={15} stopAt={90} />
        )}
      </div>
    </aside>
  )
}

/** The rail's budget bar doubles as the per-project cap editor: click it to change cap / auto-stop. */
function BudgetEditor({ projectId, spend, cap, stopAt }: { projectId: string; spend: number; cap: number; stopAt: number }) {
  const [open, setOpen] = useState(false)
  const [draftCap, setDraftCap] = useState(cap)
  const [draftStop, setDraftStop] = useState(stopAt)
  const patch = usePatchProject(projectId)
  useEffect(() => { if (!open) { setDraftCap(cap); setDraftStop(stopAt) } }, [cap, stopAt, open])
  const save = () => patch.mutate({ budget_cap_usd: Math.max(1, draftCap), stop_at_pct: Math.min(100, Math.max(1, Math.round(draftStop))) }, { onSuccess: () => setOpen(false) })
  return (
    <>
      <button type="button" onClick={() => setOpen(true)} data-testid="budget-edit" title="Click to change this project's budget cap and auto-stop"
        className="w-full text-left rounded-[10px] -mx-1 px-1 py-1 hover:bg-surface2 transition-colors">
        <BudgetBar spend={spend} cap={cap} stopAt={stopAt} />
        <div className="label !text-[10px] mt-1 text-dim">Click to edit cap</div>
      </button>
      {/* portal: the rail's backdrop-filter would otherwise trap this fixed-position modal inside the rail */}
      {createPortal(<Modal open={open} onClose={() => setOpen(false)} title="Project budget" width="sm"
        footer={(<><Button variant="ghost" onClick={() => setOpen(false)}>Cancel</Button><Button variant="primary" icon="check" loading={patch.isPending} onClick={save}>Save</Button></>)}>
        <div className="flex flex-col gap-4 text-[14px]">
          <p className="text-muted leading-relaxed">Spent so far: <span className="font-mono text-text">{usd(spend)}</span>. The cap is enforced on the server before every model call; a run stops automatically at the auto-stop percentage.</p>
          <Field label="cap for this project" hint="USD"><NumberInput value={draftCap} min={1} max={1000} step={1} unit="USD" onChange={setDraftCap} /></Field>
          <Slider label="auto-stop at" value={draftStop} min={10} max={100} step={5} tone="amber" format={(v) => `${v}% of cap`} onChange={setDraftStop} />
          {patch.error && <div className="text-red text-[13px]">{String((patch.error as Error).message)}</div>}
        </div>
      </Modal>, document.body)}
    </>
  )
}

const GUIDE_URL = 'https://github.com/k3nn3dy-ai/Dataset-genie/blob/main/docs/USER_GUIDE.md'

function RailExternal({ href, icon, label }: { href: string; icon: 'external'; label: string }) {
  return (
    <a href={href} target="_blank" rel="noreferrer" title="Open the user guide in a new tab"
      className="relative flex items-center gap-3 h-[36px] mx-2 px-2.5 rounded-[8px] transition-colors text-muted hover:text-text hover:bg-surface2/70">
      <span className="w-6 h-6 rounded-[7px] bg-surface2 text-muted flex items-center justify-center"><Icon name={icon} size={13} /></span>
      <span className="font-ui font-medium text-[13.5px] flex-1">{label}</span>
    </a>
  )
}

function RailLink({ to, icon, label, end }: { to: string; icon: 'folder' | 'settings'; label: string; end?: boolean }) {
  return (
    <NavLink to={to} end={end} className={({ isActive }) => clsx('relative flex items-center gap-3 h-[36px] mx-2 px-2.5 rounded-[8px] transition-colors', isActive ? 'bg-surface2 text-green' : 'text-muted hover:text-text hover:bg-surface2/70')}>
      {({ isActive }) => (
        <>
          <span className={clsx('w-6 h-6 rounded-[7px] flex items-center justify-center', isActive ? 'bg-green/10 text-green' : 'bg-surface2')}><Icon name={icon} size={13} /></span>
          <span className="font-ui font-medium text-[13.5px] flex-1">{label}</span>
        </>
      )}
    </NavLink>
  )
}

function StageBadge({ n, state }: { n: number; state: 'done' | 'active' | 'todo' | 'running' | 'paused' | 'failed' }) {
  return (
    <span className={clsx(
      'w-6 h-6 rounded-[7px] flex items-center justify-center font-ui text-[11px] font-medium shrink-0',
      state === 'active' && 'bg-green text-white font-semibold',
      state === 'done' && 'bg-ok/15 text-ok',
      state === 'running' && 'bg-green/10 text-green',
      state === 'paused' && 'bg-amber/15 text-amber',
      state === 'failed' && 'bg-red/15 text-red',
      state === 'todo' && 'bg-surface2 text-dim',
    )}>
      {state === 'done' ? <Icon name="check" size={12} strokeWidth={2.4} /> : state === 'running' ? <span className="w-2 h-2 rounded-full bg-ok pulse-dot" /> : state === 'paused' ? <Icon name="pause" size={11} strokeWidth={2.6} title="Paused — resumable" /> : state === 'failed' ? <Icon name="warning" size={11} strokeWidth={2.2} /> : pad2(n)}
    </span>
  )
}
