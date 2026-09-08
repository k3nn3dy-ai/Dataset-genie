import { useMemo } from 'react'
import type { Pair } from '../../lib/types'
import { wordDiff, type DiffOp } from '../../lib/diff'
import { Chip } from '../../components'

/** Side-by-side chosen vs rejected with word-level diff highlighting. */
export function PairDiff({ pair }: { pair: Pair }) {
  const chosen = pair.chosen.at(-1)?.content ?? ''
  const rejected = pair.rejected.at(-1)?.content ?? ''
  const diff = useMemo(() => wordDiff(chosen, rejected), [chosen, rejected])
  const prompt = pair.prompt.filter((m) => m.role === 'user').at(-1)?.content ?? ''
  return (
    <div className="rounded-card border border-line bg-bg/40 flex flex-col">
      <div className="px-3 py-2 border-b border-line flex items-start gap-3">
        <span className="label mt-0.5 shrink-0">prompt</span>
        <p className="text-[12.5px] text-muted leading-snug line-clamp-2 flex-1">{prompt}</p>
        <div className="flex gap-1 shrink-0"><Chip tone="steel">{pair.metadata.flaw}</Chip><Chip tone="dim">{pair.metadata.strategy}</Chip></div>
      </div>
      <div className="grid grid-cols-2 divide-x divide-line">
        <Side title="chosen" tone="ok" ops={diff.left} />
        <Side title="rejected" tone="red" ops={diff.right} />
      </div>
      {pair.metadata.judge?.rationale && (
        <div className="px-3 py-2 border-t border-line flex gap-3 items-start">
          <span className="label mt-0.5 shrink-0">judge</span>
          <p className="text-[12.5px] text-text/80 leading-snug flex-1">{pair.metadata.judge.rationale}</p>
          <Chip tone={pair.metadata.judge.verdict === 'tie' ? 'steel' : 'ok'}>{pair.metadata.judge.verdict}</Chip>
        </div>
      )}
    </div>
  )
}

function Side({ title, tone, ops }: { title: string; tone: 'ok' | 'red'; ops: DiffOp[] }) {
  return (
    <div className="p-3 min-w-0">
      <div className={`label mb-2 ${tone === 'ok' ? '!text-ok' : '!text-red'}`}>{title}</div>
      <div className="text-[13px] leading-relaxed whitespace-pre-wrap max-h-[300px] overflow-y-auto font-ui">
        {ops.map((op, i) => op.kind === 'same'
          ? <span key={i} className="text-text/80">{op.text}</span>
          : <mark key={i} className={op.kind === 'del' ? 'bg-ok/20 text-ok rounded-[2px] px-[1px]' : 'bg-red/25 text-red rounded-[2px] px-[1px] line-through decoration-red/60'}>{op.text}</mark>)}
      </div>
    </div>
  )
}
