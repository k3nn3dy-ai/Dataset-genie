import { useEffect, useState } from 'react'
import type { Message } from '../../lib/types'
import { usePatchRow, useRow } from '../../lib/queries'
import { Button, Chip, IconButton, Spinner, toneFor } from '../../components'
import { Conversation } from '../shared/Conversation'

interface Props { projectId: string | undefined; rowId: string | null; onClose: () => void; onNav: (dir: -1 | 1) => void }

/** Right-hand detail drawer: full conversation, judge rationale + criteria, accept / edit / flag. */
export function RowDrawer({ projectId, rowId, onClose, onNav }: Props) {
  const row = useRow(projectId, rowId)
  const patch = usePatchRow(projectId)
  const [editing, setEditing] = useState(false)
  const [messages, setMessages] = useState<Message[] | null>(null)
  useEffect(() => { setEditing(false); setMessages(null) }, [rowId])
  useEffect(() => {
    if (!rowId) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); if (e.key === 'ArrowDown' && !editing) onNav(1); if (e.key === 'ArrowUp' && !editing) onNav(-1) }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [rowId, onClose, onNav, editing])
  if (!rowId) return null
  const r = row.data
  const msgs = messages ?? r?.messages ?? []
  const save = () => { if (messages) patch.mutate({ rid: rowId, messages: messages.map((m) => (m.role === 'assistant' && m.content ? { ...m, content: m.content.replace(/\s+$/, '') } : m)) }, { onSuccess: () => { setEditing(false); setMessages(null) } }) }

  return (
    <aside className="fixed top-0 right-0 bottom-0 z-[70] w-[620px] max-w-[60vw] panel !rounded-none border-l border-orange/30 shadow-[-20px_0_60px_rgba(0,0,0,.6)] flex flex-col drawer-in">
      <header className="flex items-center gap-2 px-4 h-12 border-b border-line shrink-0">
        <IconButton icon="chevron" label="Previous" size="sm" className="-rotate-90" onClick={() => onNav(-1)} />
        <IconButton icon="chevron" label="Next" size="sm" className="rotate-90" onClick={() => onNav(1)} />
        <span className="font-mono text-[12px] text-orange truncate flex-1">{rowId}</span>
        {r && <Chip tone={toneFor(r.status)}>{r.status}</Chip>}
        <IconButton icon="x" label="Close" size="sm" onClick={onClose} />
      </header>
      {row.isLoading && <div className="p-4"><Spinner /></div>}
      {r && (
        <>
          <div className="px-4 py-2 border-b border-line flex flex-wrap items-center gap-1.5 font-mono text-[10.5px] text-muted">
            <span className="truncate max-w-full">{r.metadata.leaf_path.join(' / ')}</span>
            <Chip tone={toneFor(r.metadata.difficulty)}>{r.metadata.difficulty}</Chip><Chip>{r.metadata.task_type}</Chip>
            {r.metadata.persona && <Chip tone="dim">{r.metadata.persona}</Chip>}{r.metadata.style && <Chip tone="dim">{r.metadata.style}</Chip>}
            {r.metadata.flags.map((f) => <Chip key={f} tone={toneFor(f)}>{f}</Chip>)}
            <span className="ml-auto text-dim">{r.metadata.models.responses}</span>
          </div>
          <div className="flex-1 overflow-y-auto p-4 flex flex-col gap-4">
            <Conversation messages={msgs} editable={editing} onEdit={(i, content) => setMessages(msgs.map((m, k) => (k === i ? { ...m, content } : m)))} />
            {r.metadata.judge && (
              <section className="rounded-card border border-line bg-bg/40 p-3 flex flex-col gap-2">
                <div className="flex items-center justify-between"><span className="label">judge · {r.metadata.models.judge}</span><span className={`font-mono text-[20px] tabular-nums ${r.metadata.judge.score < 3 ? 'text-amber' : 'text-ok'}`}>{r.metadata.judge.score.toFixed(1)}<span className="text-dim text-[11px]"> / 5</span></span></div>
                <div className="grid grid-cols-4 gap-2">
                  {Object.entries(r.metadata.judge.criteria).map(([k, v]) => (
                    <div key={k} className="rounded-btn border border-line px-2 py-1.5"><div className="label !text-[9px] truncate">{k}</div><div className="flex gap-0.5 mt-1">{[1, 2, 3, 4, 5].map((i) => <span key={i} className={`h-1.5 flex-1 rounded-full ${i <= v ? 'bg-orange' : 'bg-line2'}`} />)}</div></div>
                  ))}
                </div>
                <p className="text-[13px] text-text/85 leading-snug">{r.metadata.judge.rationale}</p>
              </section>
            )}
          </div>
          <footer className="flex items-center gap-2 px-4 h-14 border-t border-line shrink-0">
            {editing ? (
              <>
                <span className="font-mono text-[10.5px] text-amber">editing assistant turns · trailing whitespace stripped on save</span><span className="flex-1" />
                <Button size="sm" onClick={() => { setEditing(false); setMessages(null) }}>Cancel</Button>
                <Button size="sm" variant="primary" icon="check" loading={patch.isPending} disabled={!messages} onClick={save}>Save edit</Button>
              </>
            ) : (
              <>
                <Button size="sm" variant="primary" icon="check" loading={patch.isPending} onClick={() => patch.mutate({ rid: rowId, action: 'accept' })} data-testid="row-accept">Accept</Button>
                <Button size="sm" variant="outline" icon="edit" onClick={() => setEditing(true)}>Edit</Button>
                <Button size="sm" variant={r.status === 'flagged' ? 'ghost' : 'danger'} icon="flag" loading={patch.isPending} onClick={() => patch.mutate({ rid: rowId, action: r.status === 'flagged' ? 'unflag' : 'flag' })}>{r.status === 'flagged' ? 'Unflag' : 'Flag'}</Button>
                <span className="flex-1" /><span className="font-mono text-[10px] text-dim">↑↓ navigate · esc close</span>
              </>
            )}
          </footer>
        </>
      )}
    </aside>
  )
}
