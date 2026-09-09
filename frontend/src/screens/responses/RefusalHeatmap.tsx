import type { RefusalCell } from '../../lib/viewtypes'

/** Mini heatmap: rows = teacher models, columns = topics, cell colour = refusal rate (green → amber → red). */
export function RefusalHeatmap({ cells }: { cells: RefusalCell[] }) {
  const models = [...new Set(cells.map((c) => c.model))]
  const topics = [...new Set(cells.map((c) => c.topic))]
  const at = (m: string, t: string) => cells.find((c) => c.model === m && c.topic === t)
  const colour = (rate: number) => rate === 0 ? 'rgba(34,227,90,.10)' : rate < 0.1 ? `rgba(34,227,90,${0.18 + rate * 3})` : rate < 0.4 ? `rgba(255,160,64,${0.25 + rate})` : `rgba(255,74,74,${0.35 + rate * 0.5})`
  if (models.length === 0) return <div className="font-mono text-[11px] text-dim">No data.</div>
  return (
    <div className="overflow-x-auto">
      <table className="font-mono text-[11px] border-separate border-spacing-[3px]">
        <thead>
          <tr><th />{topics.map((t) => <th key={t} className="label !text-[9px] text-left px-1 max-w-[110px] truncate" title={t}>{t}</th>)}</tr>
        </thead>
        <tbody>
          {models.map((m) => (
            <tr key={m}>
              <td className="text-muted pr-2 whitespace-nowrap">{m.split('/')[1] ?? m}</td>
              {topics.map((t) => {
                const c = at(m, t)
                const rate = c && c.total > 0 ? c.refusals / c.total : 0
                return (
                  <td key={t} className="h-8 min-w-[72px] rounded-chip text-center tabular-nums" style={{ background: colour(rate) }} title={c ? `${c.refusals}/${c.total} refused` : 'no rows'}>
                    {c ? <span className={rate >= 0.4 ? 'text-text' : rate > 0 ? 'text-text/90' : 'text-green/70'}>{(rate * 100).toFixed(0)}%</span> : <span className="text-dim">—</span>}
                  </td>
                )
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
