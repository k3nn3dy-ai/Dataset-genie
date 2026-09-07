import clsx from 'clsx'
import type { Message } from '../../lib/types'
import { Textarea } from '../../components'

const ROLE: Record<Message['role'], { label: string; cls: string; bubble: string }> = {
  system: { label: 'system', cls: 'text-dim', bubble: 'border-line bg-bg/40 text-muted' },
  user: { label: 'user', cls: 'text-magenta', bubble: 'border-magenta/35 bg-magenta/5' },
  assistant: { label: 'assistant', cls: 'text-cyan', bubble: 'border-cyan/35 bg-cyan/5' },
  tool: { label: 'tool', cls: 'text-amber', bubble: 'border-amber/35 bg-bg/60 font-mono text-[11.5px]' },
}

interface Props {
  messages: Message[]
  editable?: boolean
  onEdit?: (index: number, content: string) => void
  compact?: boolean
  className?: string
}

/** Role-coloured conversation bubbles; tool calls rendered as mono blocks. */
export function Conversation({ messages, editable, onEdit, compact, className }: Props) {
  return (
    <div className={clsx('flex flex-col gap-2.5', className)}>
      {messages.map((m, i) => {
        const r = ROLE[m.role]
        return (
          <div key={i} className={clsx('rounded-card border px-3 py-2', r.bubble)}>
            <div className="flex items-center justify-between mb-1">
              <span className={clsx('font-mono text-[10px] uppercase tracking-[.14em]', r.cls)}>{r.label}{m.name ? ` · ${m.name}` : ''}</span>
              {m.tool_call_id && <span className="font-mono text-[10px] text-dim">{m.tool_call_id}</span>}
            </div>
            {editable && m.role === 'assistant' && onEdit ? (
              <Textarea value={m.content ?? ''} onChange={(e) => onEdit(i, e.target.value)} className="!min-h-[120px] !text-[13px]" />
            ) : (
              m.content && <div className={clsx('whitespace-pre-wrap leading-relaxed text-text/90', compact ? 'text-[12.5px]' : 'text-[13.5px]', m.role === 'tool' && 'font-mono')}>{m.content}</div>
            )}
            {m.tool_calls?.map((tc) => (
              <pre key={tc.id} className="mt-2 rounded-btn border border-amber/30 bg-bg/70 p-2 font-mono text-[11px] text-amber/90 overflow-x-auto">
                {`${tc.function.name}(${tc.function.arguments})`}
              </pre>
            ))}
          </div>
        )
      })}
    </div>
  )
}
